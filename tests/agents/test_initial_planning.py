"""Deterministic runtime integration for Stage 4 / Slice 4B."""

import copy
import json
from types import SimpleNamespace

from arkui_ut_agent.agents import AgentState, Plan
from arkui_ut_agent.agents.context import DEFAULT_CONTEXT_MAX_CHARS
from arkui_ut_agent.agents.default import DefaultAgent
from arkui_ut_agent.agents.interactive import InteractiveAgent
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


class PlanningFormatErrorModel(DeterministicModel):
    def query(self, messages, **kwargs):
        raise FormatError({
            "role": "user",
            "content": "The planning response did not contain one action.",
            "extra": {"interrupt_type": "FormatError", "cost": 0.6},
        })


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


def _plan_payload(*, active_step_id="inspect") -> dict:
    return {
        "revision": 1,
        "steps": [
            {"id": "inspect", "description": "Inspect the implementation"},
            {"id": "verify", "description": "Run the focused test"},
        ],
        "active_step_id": active_step_id,
    }


def _planning_output(*, payload=None, actions=None, cost=0.25):
    plan_actions = actions if actions is not None else [{"command": json.dumps(payload or _plan_payload())}]
    return make_output("planning-only-marker", plan_actions, cost=cost)


def _agent(model, env, **config):
    return DefaultAgent(
        model,
        env,
        system_template="stable system instruction",
        instance_template="stable task instruction for {{ task }}",
        cost_limit=0,
        **config,
    )


def test_initial_plan_precedes_action_is_accounted_and_enters_bounded_context():
    model = RecordingModel(outputs=[
        _planning_output(cost=0.25),
        make_output("normal action", [{"command": "inspect"}], cost=0.75),
    ])
    env = FakeEnvironment([{"output": "fact", "returncode": 0, "exception_info": ""}])
    agent = _agent(model, env, step_limit=2)

    assert agent.run("Repair Text behavior")["exit_status"] == "LimitsExceeded"

    assert agent.n_calls == 2
    assert agent.cost == 1.0
    assert agent._step_count == 1
    assert env.calls == [{"command": "inspect"}]
    assert agent.state.current_plan == Plan.model_validate(_plan_payload())
    assert agent.state.current_plan.active_step_id == "inspect"
    assert agent.state.current_step == "step-1"
    assert [record.step_id for record in agent.state.evidence_memory.records] == ["step-1"]
    assert [record["step_id"] for record in agent.tool_executions] == ["step-1"]
    assert {event.step_id for event in agent.memory_updates} == {"step-1"}

    planning_message = agent.messages[2]
    assert planning_message["extra"]["phase"] == "initial_planning"
    assert "step_id" not in planning_message["extra"]
    assert agent.messages[3]["extra"]["step_id"] == "step-1"

    assert len(model.inputs) == 2
    planning_input = "\n".join(get_content_string(message) for message in model.inputs[0])
    action_context = get_content_string(model.inputs[1][2])
    assert "Create the initial plan" in planning_input
    assert len(action_context) <= DEFAULT_CONTEXT_MAX_CHARS
    assert '"active_step_id":"inspect"' in action_context
    assert '"description":"Inspect the implementation"' in action_context
    assert "planning-only-marker" not in action_context

    serialized = agent.serialize()
    assert serialized["info"]["model_stats"] == {"instance_cost": 1.0, "api_calls": 2}
    assert AgentState.from_trajectory(serialized) == agent.state
    assert serialized["agent_state"]["current_plan"] == _plan_payload()


def test_invalid_initial_plan_is_bounded_and_never_executes_its_action():
    model = RecordingModel(outputs=[_planning_output(actions=[{"command": "dangerous-shell-command"}], cost=0.4)])
    env = FakeEnvironment()
    agent = _agent(model, env, max_consecutive_format_errors=1)

    assert agent.run("task")["exit_status"] == "RepeatedFormatError"

    assert agent.n_calls == 1
    assert agent.cost == 0.4
    assert agent.state.current_plan is None
    assert agent.state.current_step is None
    assert agent._step_count == 0
    assert agent.memory_updates == []
    assert agent.tool_executions == []
    assert agent.state.evidence_memory.records == []
    assert env.calls == []
    assert agent.messages[2]["extra"]["phase"] == "initial_planning"
    assert "step_id" not in agent.messages[2]["extra"]
    feedback = next(message for message in agent.messages if message.get("extra", {}).get("interrupt_type"))
    assert feedback["extra"]["phase"] == "initial_planning"
    assert "step_id" not in feedback["extra"]


def test_model_format_error_during_planning_is_accounted_once_without_runtime_step():
    env = FakeEnvironment()
    agent = _agent(
        PlanningFormatErrorModel(outputs=[]),
        env,
        max_consecutive_format_errors=1,
    )

    assert agent.run("task")["exit_status"] == "RepeatedFormatError"

    assert agent.n_calls == 1
    assert agent.cost == 0.6
    assert agent.state.current_plan is None
    assert agent.state.current_step is None
    assert agent._step_count == 0
    assert agent.memory_updates == agent.tool_executions == []
    assert env.calls == []
    feedback = next(message for message in agent.messages if message.get("extra", {}).get("interrupt_type"))
    assert feedback["extra"]["phase"] == "initial_planning"
    assert "step_id" not in feedback["extra"]


def test_interactive_model_driven_path_inherits_initial_planning():
    model = RecordingModel(outputs=[_planning_output(), make_output("finish", [{"command": "submit"}])])
    env = FakeEnvironment()
    agent = InteractiveAgent(
        model,
        env,
        system_template="system",
        instance_template="{{ task }}",
        cost_limit=0,
        mode="yolo",
        confirm_exit=False,
    )

    assert agent.run("task")["exit_status"] == "Submitted"

    assert agent.n_calls == 2
    assert agent._step_count == 1
    assert agent.state.current_plan.active_step_id == "inspect"
    assert env.calls == [{"command": "submit"}]
    assert agent.tool_executions[0]["step_id"] == "step-1"


def test_interactive_human_mode_remains_model_free(monkeypatch):
    agent = InteractiveAgent(
        DeterministicModel(outputs=[]),
        FakeEnvironment(),
        system_template="system",
        instance_template="{{ task }}",
        cost_limit=0,
        mode="human",
        confirm_exit=False,
    )
    monkeypatch.setattr(agent, "_prompt_and_handle_slash_commands", lambda *args: "submit")

    assert agent.run("task")["exit_status"] == "Submitted"

    assert agent.n_calls == 0
    assert agent.state.current_plan is None
    assert agent.state.current_step == "step-1"
