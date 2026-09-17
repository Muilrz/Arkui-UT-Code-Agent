"""Deterministic Stage 4 Stop Policy and integrated-loop tests."""

import json
from types import SimpleNamespace

import pytest

from arkui_ut_agent.agents import (
    AgentState,
    ControlDecision,
    DecisionKind,
    ExecutionStepTrace,
    StopPolicy,
    StopReason,
)
from arkui_ut_agent.agents.default import DefaultAgent
from arkui_ut_agent.exceptions import Submitted
from arkui_ut_agent.models.test_models import DeterministicModel, make_output
from arkui_ut_agent.tools import Diagnostic, Observation, Provenance


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


def _plan_output(revision=1, *, step_id="inspect", cost=0.1):
    plan = {
        "revision": revision,
        "steps": [{"id": step_id, "description": f"Perform {step_id}"}],
        "active_step_id": step_id,
    }
    return make_output("plan", [{"command": json.dumps(plan)}], cost=cost)


def _decision_output(kind, *, cost=0.1):
    decision = {"kind": kind, "rationale": f"Choose {kind} from the actual Observation"}
    return make_output("decision", [{"command": json.dumps(decision)}], cost=cost)


def _agent(outputs, env=None, **config):
    return DefaultAgent(
        DeterministicModel(outputs=outputs),
        env or FakeEnvironment(),
        system_template="system",
        instance_template="task: {{ task }}",
        cost_limit=0,
        **config,
    )


def _raw_observation(text="fact"):
    return {"output": text, "returncode": 0, "exception_info": ""}


def test_finish_decision_stops_success_without_another_model_or_tool_call():
    env = FakeEnvironment([_raw_observation()])
    agent = _agent([
        _plan_output(),
        make_output("inspect", [{"command": "inspect"}], cost=0.2),
        _decision_output("finish", cost=0.3),
        make_output("must remain unused", [{"command": "unexpected"}]),
    ], env)

    result = agent.run("task")

    assert result["stop_reason"] == StopReason.SUCCESS.value
    assert agent.state.stop_reason is StopReason.SUCCESS
    assert agent.state.current_decision.kind is DecisionKind.FINISH
    assert agent.n_calls == 3
    assert env.calls == [{"command": "inspect"}]


@pytest.mark.parametrize(
    ("config", "planning_cost", "expected"),
    [
        ({"step_limit": 1}, 0.1, StopReason.STEP_LIMIT),
        ({"cost_limit": 0.5}, 0.5, StopReason.COST_LIMIT),
    ],
)
def test_model_limits_use_distinct_stop_reasons(config, planning_cost, expected):
    env = FakeEnvironment()
    agent = DefaultAgent(
        DeterministicModel(outputs=[
            _plan_output(cost=planning_cost),
            make_output("must remain unused", [{"command": "unexpected"}]),
        ]),
        env,
        system_template="system",
        instance_template="{{ task }}",
        **config,
    )

    result = agent.run("task")

    assert result["stop_reason"] == expected.value
    assert agent.state.stop_reason is expected
    assert agent.n_calls == 1
    assert env.calls == []


@pytest.mark.parametrize("kind", ["retrieve", "act", "repair", "verify"])
def test_actionable_decisions_reenter_action_boundary_and_are_traced(kind):
    env = FakeEnvironment([_raw_observation()])
    agent = _agent([
        _plan_output(),
        make_output("inspect", [{"command": "inspect"}]),
        _decision_output(kind),
        make_output("submit", [{"command": "submit"}]),
    ], env)

    assert agent.run("task")["stop_reason"] == StopReason.SUCCESS.value

    assert [call["command"] for call in env.calls] == ["inspect", "submit"]
    assert [trace.decision.kind.value for trace in agent.step_traces] == ["act", kind]
    assert agent.state.stop_reason is StopReason.SUCCESS


def test_diagnose_decision_runs_diagnose_again_before_any_action():
    env = FakeEnvironment([_raw_observation()])
    agent = _agent([
        _plan_output(),
        make_output("inspect", [{"command": "inspect"}]),
        _decision_output("diagnose"),
        _decision_output("finish"),
        make_output("must remain unused", [{"command": "unexpected"}]),
    ], env)

    assert agent.run("task")["stop_reason"] == StopReason.SUCCESS.value

    assert agent.n_calls == 4
    assert env.calls == [{"command": "inspect"}]
    assert len([message for message in agent.messages if message.get("extra", {}).get("phase") == "diagnose"]) == 2


def test_exact_repeat_replans_once_then_stops_without_new_evidence():
    repeated = _raw_observation("same fact")
    env = FakeEnvironment([repeated, repeated, repeated])
    agent = _agent([
        _plan_output(),
        make_output("first", [{"command": "inspect"}]),
        make_output("repeat", [{"command": "inspect"}]),
        _plan_output(2, step_id="change-approach"),
        make_output("post-replan repeat", [{"command": "inspect"}]),
        make_output("must remain unused", [{"command": "inspect"}]),
    ], env, diagnose_after_observation=False)

    result = agent.run("task")

    assert result["stop_reason"] == StopReason.NO_NEW_EVIDENCE.value
    assert agent.state.stop_reason is StopReason.NO_NEW_EVIDENCE
    assert agent.state.current_plan.revision == 2
    assert agent.n_calls == 5
    assert len(env.calls) == 3
    assert len(agent.state.evidence_memory.records) == 1
    assert [record["repeat_of_step_id"] for record in agent.tool_executions] == [
        None,
        "step-1",
        "step-2",
    ]
    assert sum(message.get("extra", {}).get("phase") == "replan" for message in agent.messages) == 1


@pytest.mark.parametrize(
    "reason",
    [StopReason.TOOL_UNAVAILABLE, StopReason.IRRECOVERABLE_FAILURE],
)
def test_existing_terminal_tool_diagnostics_stop_without_invented_detection(reason):
    observation = Observation(
        tool_name="run_command",
        success=False,
        summary="The tool reported a terminal runtime signal",
        diagnostics=[Diagnostic(code=reason.value, message="terminal")],
        provenance=[Provenance(source="runtime", location=None)],
    )
    env = FakeEnvironment([observation])
    agent = _agent([
        _plan_output(),
        make_output("run", [{"command": "inspect"}]),
        _decision_output("finish"),
    ], env)

    assert agent.run("task")["stop_reason"] == reason.value

    assert agent.state.stop_reason is reason
    assert agent.n_calls == 2
    assert env.calls == [{"command": "inspect"}]


def test_integrated_loop_never_executes_without_a_decision():
    env = FakeEnvironment()
    agent = _agent(
        [make_output("must remain unused", [{"command": "unexpected"}])],
        env,
        initial_planning=False,
    )

    result = agent.run("task")

    assert result["stop_reason"] == StopReason.IRRECOVERABLE_FAILURE.value
    assert agent.n_calls == 0
    assert env.calls == []
    assert agent.step_traces == []


def test_minimal_execution_step_trace_is_validated_and_serializable():
    env = FakeEnvironment([_raw_observation("first"), _raw_observation("second")])
    agent = _agent([
        _plan_output(),
        make_output("first", [{"command": "inspect"}]),
        _decision_output("verify"),
        make_output("second", [{"command": "test"}]),
        _decision_output("finish"),
    ], env)

    assert agent.run("Trace the task")["stop_reason"] == StopReason.SUCCESS.value
    serialized = agent.serialize()

    traces = [ExecutionStepTrace.model_validate(item) for item in serialized["step_traces"]]
    assert len(traces) == 2
    assert [trace.step_id for trace in traces] == ["step-1", "step-2"]
    assert [trace.current_goal for trace in traces] == ["Trace the task", "Trace the task"]
    assert [trace.decision.kind for trace in traces] == [DecisionKind.ACT, DecisionKind.VERIFY]
    assert all(trace.actions and trace.observations and trace.state_updates for trace in traces)
    assert AgentState.from_trajectory(serialized) == agent.state
    assert serialized["info"]["stop_reason"] == StopReason.SUCCESS.value


def test_stop_policy_distinguishes_repeat_outcomes_and_nonterminal_decisions():
    assert StopPolicy.for_repeat_after_replan(evidence_changed=False) is StopReason.NO_NEW_EVIDENCE
    assert StopPolicy.for_repeat_after_replan(evidence_changed=True) is StopReason.REPEATED_FAILURE
    for kind in StopPolicy.ACTIONABLE_DECISIONS:
        assert StopPolicy.for_decision(
            ControlDecision(kind=kind, rationale="continue")
        ) is None
