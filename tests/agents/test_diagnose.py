"""Runtime integration tests for control-only Diagnose."""

import copy
import json
from types import SimpleNamespace

from arkui_ut_agent.agents import AgentState, DecisionKind
from arkui_ut_agent.agents.default import DefaultAgent
from arkui_ut_agent.exceptions import FormatError, Submitted
from arkui_ut_agent.models.test_models import DeterministicModel, make_output
from arkui_ut_agent.models.utils.content_string import get_content_string


class RecordingModel(DeterministicModel):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.inputs: list[list[dict]] = []

    def query(self, messages, **kwargs):
        self.inputs.append(copy.deepcopy(messages))
        return super().query(messages, **kwargs)


class DiagnosisFormatErrorModel(RecordingModel):
    def query(self, messages, **kwargs):
        if len(self.inputs) == 2:
            self.inputs.append(copy.deepcopy(messages))
            raise FormatError({
                "role": "user",
                "content": "The Diagnose response did not contain one action.",
                "extra": {"interrupt_type": "FormatError", "cost": 0.6},
            })
        return super().query(messages, **kwargs)


class FakeEnvironment:
    def __init__(self, outputs=()):
        self.outputs = iter(outputs)
        self.calls: list[dict] = []
        self.config = SimpleNamespace(cwd="workspace", timeout=10, env={})
        self.backend = SimpleNamespace(name="fake", dialect="fake")

    def execute(self, action):
        self.calls.append(dict(action))
        if action["command"] == "submit":
            raise Submitted({"role": "exit", "content": "done", "extra": {"exit_status": "Submitted"}})
        return next(self.outputs)

    def get_template_vars(self):
        return {}

    def serialize(self):
        return {}


def _plan_output():
    payload = {
        "revision": 1,
        "steps": [{"id": "inspect", "description": "Inspect current behavior"}],
        "active_step_id": "inspect",
    }
    return make_output("plan", [{"command": json.dumps(payload)}], cost=0.1)


def _decision_output(kind="repair", rationale="The Tool failed and the current edit needs repair", *, cost=0.3):
    payload = {"kind": kind, "rationale": rationale}
    return make_output("control-only", [{"command": json.dumps(payload)}], cost=cost)


def _agent(model, env, **config):
    return DefaultAgent(
        model,
        env,
        system_template="system",
        instance_template="task: {{ task }}",
        cost_limit=0,
        **config,
    )


def test_tool_observation_is_diagnosed_and_decision_enters_next_bounded_context():
    model = RecordingModel(outputs=[
        _plan_output(),
        make_output("act", [{"command": "inspect"}], cost=0.2),
        _decision_output(),
        make_output("finish", [{"command": "submit"}], cost=0.4),
    ])
    env = FakeEnvironment([{"output": "compile failed", "returncode": 1, "exception_info": "error"}])
    agent = _agent(model, env)

    assert agent.run("Repair Text behavior")["exit_status"] == "Submitted"

    assert agent.n_calls == 4
    assert agent.cost == 1.0
    assert agent.state.current_decision.kind is DecisionKind.REPAIR
    assert agent.state.current_decision.rationale == "The Tool failed and the current edit needs repair"
    assert env.calls == [{"command": "inspect"}, {"command": "submit"}]

    diagnosis_input = get_content_string(model.inputs[2][2])
    assert "## recent_tool_observations" in diagnosis_input
    assert '"step_id":"step-1"' in diagnosis_input
    assert '"status":"added"' in diagnosis_input
    assert "compile failed" in diagnosis_input

    next_action_context = get_content_string(model.inputs[3][2])
    assert '"current_decision":{"kind":"repair"' in next_action_context
    assert "control-only" not in next_action_context

    diagnosis_message = next(
        message for message in agent.messages if message.get("extra", {}).get("phase") == "diagnose"
    )
    assert diagnosis_message["extra"]["source_step_ids"] == ["step-1"]
    assert "step_id" not in diagnosis_message["extra"]
    assert {event.step_id for event in agent.memory_updates} == {"step-1", "step-2"}

    serialized = agent.serialize()
    assert serialized["info"]["model_stats"] == {"instance_cost": 1.0, "api_calls": 4}
    assert serialized["agent_state"]["current_decision"]["kind"] == "repair"
    assert AgentState.from_trajectory(serialized) == agent.state


def test_invalid_diagnosis_is_bounded_and_control_action_never_executes():
    invalid_control = make_output("bad control", [{"command": "dangerous-shell-command"}], cost=0.5)
    model = RecordingModel(outputs=[
        _plan_output(),
        make_output("act", [{"command": "inspect"}], cost=0.2),
        invalid_control,
    ])
    env = FakeEnvironment([{"output": "fact", "returncode": 0, "exception_info": ""}])
    agent = _agent(model, env, max_consecutive_format_errors=1)

    assert agent.run("task")["exit_status"] == "RepeatedFormatError"

    assert agent.n_calls == 3
    assert agent.cost == 0.8
    assert env.calls == [{"command": "inspect"}]
    assert agent.state.current_decision is None
    assert len(agent.state.evidence_memory.records) == 1
    assert all(record.source != "diagnose" for record in agent.state.evidence_memory.records)
    feedback = next(
        message for message in agent.messages
        if message.get("extra", {}).get("interrupt_type") == "FormatError"
    )
    assert feedback["extra"]["phase"] == "diagnose"
    assert feedback["extra"]["source_step_ids"] == ["step-1"]
    assert "step_id" not in feedback["extra"]


def test_model_format_error_during_diagnose_is_accounted_once_without_new_step():
    model = DiagnosisFormatErrorModel(outputs=[
        _plan_output(),
        make_output("act", [{"command": "inspect"}], cost=0.2),
    ])
    env = FakeEnvironment([{"output": "fact", "returncode": 0, "exception_info": ""}])
    agent = _agent(model, env, max_consecutive_format_errors=1)

    assert agent.run("task")["exit_status"] == "RepeatedFormatError"

    assert agent.n_calls == 3
    assert agent.cost == 0.9
    assert agent._step_count == 1
    assert agent.state.current_step == "step-1"
    assert agent.state.current_decision is None
    assert env.calls == [{"command": "inspect"}]
    feedback = next(
        message for message in agent.messages
        if message.get("extra", {}).get("interrupt_type") == "FormatError"
    )
    assert feedback["extra"]["phase"] == "diagnose"
    assert feedback["extra"]["source_step_ids"] == ["step-1"]
    assert "step_id" not in feedback["extra"]


def test_diagnose_preserves_evidence_identity_dedup_and_producing_step_provenance():
    model = RecordingModel(outputs=[
        _plan_output(),
        make_output("two calls", [{"command": "inspect"}, {"command": "inspect"}], cost=0.2),
        _decision_output(kind="verify", rationale="The repeated observation is sufficient to verify"),
    ])
    raw = {"output": "same fact", "returncode": 0, "exception_info": ""}
    env = FakeEnvironment([raw, raw])
    agent = _agent(model, env, step_limit=3)

    assert agent.run("task")["exit_status"] == "LimitsExceeded"

    assert [record["status"] for record in agent.tool_executions] == ["added", "duplicate"]
    assert len(agent.state.evidence_memory.records) == 1
    evidence = agent.state.evidence_memory.records[0]
    identity = evidence.identity
    assert evidence.step_id == "step-1"
    assert evidence.identity == identity
    assert [record["evidence_identity"] for record in agent.tool_executions] == [identity, identity]
    assert agent.state.current_decision.kind is DecisionKind.VERIFY
    assert agent.state.current_decision.rationale not in json.dumps(
        agent.state.evidence_memory.model_dump(mode="json")
    )
