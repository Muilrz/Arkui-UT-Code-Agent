import json

import pytest
from pydantic import ValidationError

from arkui_ut_agent.agents import AgentState, StopReason


def test_minimal_state_has_safe_defaults():
    state = AgentState(goal="Add an ArkUI unit test")

    assert state.model_dump(mode="json") == {
        "goal": "Add an ArkUI unit test",
        "current_plan": None,
        "current_step": None,
        "information_gap": None,
        "next_action": None,
        "open_questions": [],
        "hypotheses": [],
        "blocking_issue": None,
        "stop_reason": None,
        "retry_count": 0,
    }


def test_full_state_accepts_opaque_json_plan_and_step():
    state = AgentState(
        goal="Repair the failing unit test",
        current_plan={"revision": 1, "steps": ["locate", "edit", "verify"]},
        current_step={"index": 1, "label": "edit"},
        information_gap="The relevant fixture is not known",
        next_action="Search existing tests",
        open_questions=["Which fixture owns the mock?"],
        hypotheses=["The failure is caused by a missing expectation"],
        blocking_issue="The build target is not known",
        stop_reason=StopReason.NO_NEW_EVIDENCE,
        retry_count=2,
    )

    assert state.current_plan == {"revision": 1, "steps": ["locate", "edit", "verify"]}
    assert state.current_step == {"index": 1, "label": "edit"}
    assert state.stop_reason is StopReason.NO_NEW_EVIDENCE
    assert state.retry_count == 2


def test_mutable_defaults_are_isolated():
    first = AgentState(goal="first")
    second = AgentState(goal="second")

    first.open_questions.append("first only")
    first.hypotheses.append("first only")

    assert second.open_questions == []
    assert second.hypotheses == []


def test_assignment_validates_updates():
    state = AgentState(goal="original")

    state.next_action = "Run the focused test"
    state.retry_count = 1
    state.stop_reason = StopReason.REPEATED_FAILURE

    assert state.next_action == "Run the focused test"
    assert state.retry_count == 1
    assert state.stop_reason is StopReason.REPEATED_FAILURE


@pytest.mark.parametrize(
    ("payload", "invalid_field"),
    [
        ({}, "goal"),
        ({"goal": "   "}, "goal"),
        ({"goal": "task", "retry_count": -1}, "retry_count"),
        ({"goal": "task", "retry_count": "1"}, "retry_count"),
        ({"goal": "task", "stop_reason": "unknown"}, "stop_reason"),
        ({"goal": "task", "current_plan": object()}, "current_plan"),
        ({"goal": "task", "open_questions": [""]}, "open_questions"),
    ],
)
def test_invalid_state_fails_deterministically(payload, invalid_field):
    with pytest.raises(ValidationError) as exc_info:
        AgentState.model_validate(payload)

    assert invalid_field in str(exc_info.value)


def test_invalid_assignment_is_rejected():
    state = AgentState(goal="task")

    with pytest.raises(ValidationError):
        state.retry_count = -1

    assert state.retry_count == 0


def test_unknown_fields_are_rejected():
    with pytest.raises(ValidationError) as exc_info:
        AgentState(goal="task", messages=[])

    assert "messages" in str(exc_info.value)


def test_serialization_and_deserialization_do_not_need_messages():
    state = AgentState(
        goal="task",
        current_plan=["inspect", "verify"],
        current_step="inspect",
        open_questions=["Where is the fixture?"],
        stop_reason=StopReason.TOOL_UNAVAILABLE,
        retry_count=1,
    )

    payload = state.model_dump(mode="json")
    restored = AgentState.model_validate(payload)

    assert "messages" not in payload
    assert restored == state
    assert restored.stop_reason is StopReason.TOOL_UNAVAILABLE


def test_json_round_trip_preserves_full_state():
    state = AgentState(
        goal="task",
        current_plan={"steps": [{"id": "inspect"}]},
        current_step={"id": "inspect"},
        next_action="Read the implementation",
        hypotheses=["The branch lacks coverage"],
        stop_reason=StopReason.SUCCESS,
    )

    serialized = state.model_dump_json()
    restored = AgentState.model_validate_json(serialized)

    assert json.loads(serialized) == state.model_dump(mode="json")
    assert restored == state
