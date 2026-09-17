"""Deterministic Stage 3 runtime integration tests."""

import copy
from types import SimpleNamespace

from arkui_ut_agent.agents import AgentState, Evidence, EvidenceMemory
from arkui_ut_agent.agents.context import DEFAULT_CONTEXT_MAX_CHARS
from arkui_ut_agent.agents.default import DefaultAgent
from arkui_ut_agent.exceptions import FormatError, Submitted
from arkui_ut_agent.models.test_models import DeterministicModel, make_output
from arkui_ut_agent.models.utils.content_string import get_content_string
from arkui_ut_agent.tools import Diagnostic, Observation, Provenance


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
        self.config = SimpleNamespace(cwd="workspace", timeout=10, env={})
        self.backend = SimpleNamespace(name="fake", dialect="fake")

    def execute(self, action):
        if action["command"] == "submit":
            raise Submitted({"role": "exit", "content": "done", "extra": {"exit_status": "Submitted"}})
        return next(self.outputs)

    def get_template_vars(self):
        return {}

    def serialize(self):
        return {}


def _agent(model, env=None, **config):
    return DefaultAgent(
        model,
        env or FakeEnvironment(),
        system_template="stable system instruction",
        instance_template="stable agent instruction for {{ task }}",
        cost_limit=0,
        **config,
    )


def test_model_query_uses_bounded_context_while_full_trajectory_keeps_growing():
    evidence = Evidence(
        source="read_file",
        location="src/text.cc:7",
        content={"snippet": "CriticalNeedle implementation"},
        summary="Current step critical evidence",
        provenance=[Provenance(source="read_file", location="src/text.cc:7")],
        step_id="step-1",
    )
    model = RecordingModel(outputs=[make_output("next", [])])
    agent = _agent(model)
    agent.state = AgentState(
        goal="Inspect CriticalNeedle",
        evidence_memory=EvidenceMemory(records=[evidence]),
    )
    agent.messages = [
        {"role": "system", "content": "stable system instruction"},
        {"role": "user", "content": "stable agent instruction"},
        *[
            {"role": "assistant", "content": f"trajectory-only-{index}-" + "x" * 5_000}
            for index in range(20)
        ],
    ]
    trajectory_size = sum(len(get_content_string(message)) for message in agent.messages)

    agent.query()

    actual_input = model.inputs[0]
    assert len(actual_input) == 3
    assert [get_content_string(message) for message in actual_input[:2]] == [
        "stable system instruction",
        "stable agent instruction",
    ]
    context = get_content_string(actual_input[2])
    assert len(context) <= DEFAULT_CONTEXT_MAX_CHARS
    assert evidence.identity in context
    assert '"step_id":"step-1"' in context
    assert "CriticalNeedle" in context
    assert "trajectory-only" not in context
    assert trajectory_size > 100_000
    assert len(agent.messages) == 23
    assert len(agent.serialize()["messages"]) == 23


def test_latest_build_failure_is_present_in_the_next_actual_model_call():
    failure = Observation(
        tool_name="build",
        success=False,
        data={"output": "compile stopped"},
        summary="Text target failed",
        diagnostics=[Diagnostic(code="compile_error", message="fatal CriticalDiagnostic")],
        provenance=[Provenance(source="build", location="text_test")],
    )
    model = RecordingModel(outputs=[
        make_output("build", [{"command": "build"}]),
        make_output("finish", [{"command": "submit"}]),
    ])
    agent = _agent(model, FakeEnvironment([failure]))

    assert agent.run("Repair Text target")["exit_status"] == "Submitted"

    assert len(model.inputs) == 2
    second_context = get_content_string(model.inputs[1][2])
    evidence = agent.state.evidence_memory.records[0]
    assert "CriticalDiagnostic" in second_context
    assert evidence.identity in second_context
    assert '"source":"build"' in second_context
    assert '"step_id":"step-1"' in second_context
    assert '"location":"text_test"' in second_context
    assert len(second_context) <= DEFAULT_CONTEXT_MAX_CHARS


def test_format_error_feedback_is_bounded_and_reaches_retry_without_history_replay():
    class FormatErrorThenRecord(RecordingModel):
        def query(self, messages, **kwargs):
            self.inputs.append(copy.deepcopy(messages))
            if len(self.inputs) == 1:
                raise FormatError({
                    "role": "user",
                    "content": "correct the format " + "detail " * 1_000,
                    "extra": {"interrupt_type": "FormatError", "cost": 0},
                })
            return DeterministicModel.query(self, messages, **kwargs)

    model = FormatErrorThenRecord(outputs=[make_output("recovered", [])])
    agent = _agent(model, step_limit=2)

    assert agent.run("format task")["exit_status"] == "LimitsExceeded"

    retry_input = model.inputs[1]
    assert len(retry_input) == 4
    assert "correct the format" in get_content_string(retry_input[-1])
    assert "runtime feedback omitted" in get_content_string(retry_input[-1])
    assert len(get_content_string(retry_input[-1])) <= 2_000
    assert all("recovered" not in get_content_string(message) for message in retry_input)
    assert any(
        message.get("extra", {}).get("interrupt_type") == "FormatError"
        for message in agent.messages
    )
