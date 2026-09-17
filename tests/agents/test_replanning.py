"""Deterministic runtime tests for Replan and repeated-call detection."""

import copy
import json
from types import SimpleNamespace

import pytest

from arkui_ut_agent.agents import AgentState, DecisionKind, Plan
from arkui_ut_agent.agents.default import DefaultAgent
from arkui_ut_agent.agents.planning import _parse_replan
from arkui_ut_agent.exceptions import Submitted
from arkui_ut_agent.models.test_models import DeterministicModel, make_output
from arkui_ut_agent.models.utils.content_string import get_content_string
from arkui_ut_agent.tools import Observation, Provenance


class RecordingModel(DeterministicModel):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.inputs: list[list[dict]] = []

    def query(self, messages, **kwargs):
        self.inputs.append(copy.deepcopy(messages))
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


def _plan_payload(revision=1, *, step_id="inspect", active_step_id=None):
    return {
        "revision": revision,
        "steps": [{"id": step_id, "description": f"Perform {step_id}"}],
        "active_step_id": step_id if active_step_id is None else active_step_id,
    }


def _plan_output(revision=1, *, step_id="inspect", active_step_id=None, cost=0.1):
    payload = _plan_payload(revision, step_id=step_id, active_step_id=active_step_id)
    return make_output("plan data", [{"command": json.dumps(payload)}], cost=cost)


def _replan_decision_output(cost=0.3):
    payload = {"kind": "replan", "rationale": "The Observation invalidates the current Plan"}
    return make_output("diagnose", [{"command": json.dumps(payload)}], cost=cost)


def _agent(model, env, **config):
    return DefaultAgent(
        model,
        env,
        system_template="system",
        instance_template="task: {{ task }}",
        cost_limit=0,
        **config,
    )


def test_replan_parser_requires_exact_next_revision_and_selects_first_step():
    current = Plan.model_validate(_plan_payload(revision=2))
    replacement_payload = _plan_payload(revision=3, step_id="repair")
    replacement_payload["active_step_id"] = None

    replacement = _parse_replan(
        make_output("replan", [{"command": json.dumps(replacement_payload)}]),
        current,
    )

    assert replacement.revision == 3
    assert replacement.active_step_id == "repair"
    for invalid_revision in (2, 4):
        with pytest.raises(ValueError, match="revision must be exactly 3"):
            _parse_replan(_plan_output(invalid_revision), current)


def test_explicit_replan_decision_replaces_plan_before_next_normal_action():
    model = RecordingModel(outputs=[
        _plan_output(cost=0.1),
        make_output("inspect", [{"command": "inspect"}], cost=0.2),
        _replan_decision_output(cost=0.3),
        _plan_output(2, step_id="repair", active_step_id=None, cost=0.4),
        make_output("finish", [{"command": "submit"}], cost=0.5),
    ])
    env = FakeEnvironment([{"output": "new fact", "returncode": 0, "exception_info": ""}])
    agent = _agent(model, env)

    assert agent.run("Repair Text behavior")["exit_status"] == "Submitted"

    assert agent.n_calls == 5
    assert agent.cost == 1.5
    assert agent.state.current_plan == Plan.model_validate(_plan_payload(2, step_id="repair"))
    assert agent.state.current_decision.kind is DecisionKind.ACT
    assert env.calls == [{"command": "inspect"}, {"command": "submit"}]

    replan_input = get_content_string(model.inputs[3][2])
    assert "Revise the current structured Plan" in replan_input
    assert '"current_decision":{"kind":"replan"' in replan_input
    assert '"revision":1' in replan_input
    assert "new fact" in replan_input
    next_action_context = get_content_string(model.inputs[4][2])
    assert '"revision":2' in next_action_context
    assert '"current_decision":{"kind":"act"' in next_action_context

    replan_message = next(
        message for message in agent.messages if message.get("extra", {}).get("phase") == "replan"
    )
    assert replan_message["extra"]["previous_revision"] == 1
    assert replan_message["extra"]["expected_revision"] == 2
    assert "step_id" not in replan_message["extra"]
    assert agent._step_count == 2
    assert {event.step_id for event in agent.memory_updates} == {"step-1", "step-2"}


def test_invalid_replan_keeps_old_plan_and_never_executes_response_action():
    invalid_replacement = _plan_output(1, step_id="dangerous", cost=0.4)
    model = RecordingModel(outputs=[
        _plan_output(cost=0.1),
        make_output("inspect", [{"command": "inspect"}], cost=0.2),
        _replan_decision_output(cost=0.3),
        invalid_replacement,
    ])
    env = FakeEnvironment([{"output": "fact", "returncode": 0, "exception_info": ""}])
    agent = _agent(model, env, max_consecutive_format_errors=1)

    assert agent.run("task")["exit_status"] == "RepeatedFormatError"

    assert agent.n_calls == 4
    assert agent.cost == 1.0
    assert agent.state.current_plan == Plan.model_validate(_plan_payload())
    assert agent.state.current_decision.kind is DecisionKind.REPLAN
    assert agent.state.current_step == "step-1"
    assert agent._step_count == 1
    assert env.calls == [{"command": "inspect"}]
    feedback = next(
        message for message in agent.messages
        if message.get("extra", {}).get("interrupt_type") == "FormatError"
    )
    assert feedback["extra"]["phase"] == "replan"
    assert feedback["extra"]["previous_revision"] == 1
    assert feedback["extra"]["expected_revision"] == 2
    assert "step_id" not in feedback["extra"]


def test_exact_repeat_triggers_replan_before_any_third_normal_action():
    model = RecordingModel(outputs=[
        _plan_output(cost=0.1),
        make_output("first", [{"command": "inspect", "tool_call_id": "call-a"}], cost=0.2),
        make_output("second and attempted third", [
            {"command": "inspect", "tool_call_id": "call-b"},
            {"command": "inspect", "tool_call_id": "call-c"},
        ], cost=0.3),
        _plan_output(2, step_id="change-approach", cost=0.4),
        make_output("finish", [{"command": "submit"}], cost=0.5),
    ])
    raw = {"output": "same fact", "returncode": 0, "exception_info": ""}
    env = FakeEnvironment([raw, raw])
    agent = _agent(model, env, diagnose_after_observation=False)

    assert agent.run("task")["exit_status"] == "Submitted"

    assert [call["command"] for call in env.calls] == ["inspect", "inspect", "submit"]
    assert [record["repeat_of_step_id"] for record in agent.tool_executions] == [
        None, "step-1", None,
    ]
    assert [record["status"] for record in agent.tool_executions[:2]] == ["added", "duplicate"]
    assert len(agent.state.evidence_memory.records) == 1
    evidence = agent.state.evidence_memory.records[0]
    assert evidence.step_id == "step-1"
    assert [record["evidence_identity"] for record in agent.tool_executions[:2]] == [
        evidence.identity, evidence.identity,
    ]
    assert agent.state.current_plan.revision == 2

    assert "Revise the current structured Plan" in get_content_string(model.inputs[3][2])
    assert '"kind":"replan"' in get_content_string(model.inputs[3][2])
    replan_message_index = next(
        index for index, message in enumerate(agent.messages)
        if message.get("extra", {}).get("phase") == "replan"
    )
    final_action_index = next(
        index for index, message in enumerate(agent.messages)
        if message.get("extra", {}).get("step_id") == "step-3"
    )
    assert replan_message_index < final_action_index


def test_repeat_signature_requires_tool_input_and_observation_to_all_match():
    agent = _agent(DeterministicModel(outputs=[]), FakeEnvironment(), initial_planning=False)
    base = {
        "step_id": "step-1",
        "tool_name": "read_file",
        "tool_input": {"command": "read_file", "arguments": {"path": "a.cc"}, "tool_call_id": "one"},
        "observation": {"success": True, "data": {"content": "same"}},
        "evidence_identity": "sha256:" + "0" * 64,
        "status": "added",
        "repeat_of_step_id": None,
    }
    agent.tool_executions = [base]

    same = copy.deepcopy(base)
    same["step_id"] = "step-2"
    same["tool_input"]["tool_call_id"] = "two"
    assert agent._find_repeated_execution(same) is base

    different_tool = copy.deepcopy(same)
    different_tool["tool_name"] = "rg_search"
    assert agent._find_repeated_execution(different_tool) is None

    different_input = copy.deepcopy(same)
    different_input["tool_input"]["arguments"]["path"] = "b.cc"
    assert agent._find_repeated_execution(different_input) is None

    different_observation = copy.deepcopy(same)
    different_observation["observation"]["data"]["content"] = "changed"
    assert agent._find_repeated_execution(different_observation) is None

    missing_observation = copy.deepcopy(same)
    missing_observation["observation"] = None
    assert agent._find_repeated_execution(missing_observation) is None


def test_evidence_dedup_does_not_imply_repeat_when_tool_input_differs():
    agent = _agent(
        DeterministicModel(outputs=[]),
        FakeEnvironment(),
        initial_planning=False,
        diagnose_after_observation=False,
    )
    agent.state = AgentState(goal="task")
    observation = Observation(
        tool_name="read_file",
        success=True,
        data={"content": "same source fact"},
        summary="Read the file",
        provenance=[Provenance(source="read_file", location="src/a.cc")],
    )

    agent._record_tool_observation(
        "step-1",
        {"command": "read_file", "arguments": {"path": "src/a.cc"}},
        observation,
    )
    identity = agent.state.evidence_memory.records[0].identity
    provenance = agent.state.evidence_memory.records[0].provenance
    agent._record_tool_observation(
        "step-2",
        {"command": "read_file", "arguments": {"path": "src/b.cc"}},
        observation,
    )

    assert [record["status"] for record in agent.tool_executions] == ["added", "duplicate"]
    assert [record["repeat_of_step_id"] for record in agent.tool_executions] == [None, None]
    assert len(agent.state.evidence_memory.records) == 1
    assert agent.state.evidence_memory.records[0].identity == identity
    assert agent.state.evidence_memory.records[0].provenance == provenance
    assert agent.state.evidence_memory.records[0].step_id == "step-1"
    assert agent.state.current_decision is None
