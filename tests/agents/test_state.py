import json

import pytest
from pydantic import ValidationError
from pydantic_core import PydanticSerializationError

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
        "task_memory": {
            "target_component": None,
            "target_files": [],
            "target_classes": [],
            "target_functions": [],
            "changed_files": [],
            "relevant_tests": [],
            "fixtures": [],
            "mocks": [],
            "build_target": None,
            "completed_steps": [],
            "failed_attempts": [],
            "important_decisions": [],
        },
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


def test_updates_validate_a_new_isolated_snapshot():
    state = AgentState(goal="original")

    updated = state.updated(
        next_action="Run the focused test",
        retry_count=1,
        stop_reason=StopReason.REPEATED_FAILURE,
        open_questions=["Which test target?"],
        current_plan={"steps": ["verify"]},
    )

    assert updated.next_action == "Run the focused test"
    assert updated.retry_count == 1
    assert updated.stop_reason is StopReason.REPEATED_FAILURE
    assert state.next_action is None
    assert state.retry_count == 0
    assert state.open_questions == []
    assert state.current_plan is None

    copied = updated.updated()
    copied.open_questions.append("copy only")
    copied.current_plan["steps"].append("copy only")
    assert updated.open_questions == ["Which test target?"]
    assert updated.current_plan == {"steps": ["verify"]}


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


@pytest.mark.parametrize("changes", [{"retry_count": -1}, {"open_questions": [""]}, {"messages": []}])
def test_invalid_update_is_rejected_without_changing_original(changes):
    state = AgentState(goal="task")

    with pytest.raises(ValidationError):
        state.updated(**changes)

    assert state == AgentState(goal="task")


def test_field_assignment_is_not_a_supported_update():
    state = AgentState(goal="task")

    with pytest.raises(ValidationError) as exc_info:
        state.retry_count = 1

    assert exc_info.value.errors()[0]["type"] == "frozen_instance"
    assert state.retry_count == 0


@pytest.mark.parametrize(
    ("field", "initial", "invalid_item"),
    [
        ("open_questions", ["known question"], ""),
        ("hypotheses", ["unconfirmed idea"], "   "),
        ("current_plan", {"items": []}, object()),
        ("current_step", {"items": []}, float("nan")),
    ],
)
def test_in_place_mutation_is_rejected_at_update_and_serialization_boundaries(field, initial, invalid_item):
    state = AgentState(goal="task", **{field: initial})
    container = getattr(state, field)
    if isinstance(container, dict):
        container = container["items"]
    container.append(invalid_item)  # Deliberately bypass the supported update API.

    with pytest.raises(ValidationError) as exc_info:
        state.updated(retry_count=1)
    assert exc_info.value.errors()[0]["loc"][0] == field

    with pytest.raises(ValidationError):
        AgentState.model_validate(state)

    with pytest.raises(PydanticSerializationError, match="ValidationError"):
        state.model_dump()
    with pytest.raises(PydanticSerializationError, match="ValidationError"):
        state.model_dump(mode="json", exclude={field})
    with pytest.raises(PydanticSerializationError, match="ValidationError"):
        state.model_dump_json()


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
