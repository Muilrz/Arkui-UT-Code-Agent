import json

import pytest
from pydantic import ValidationError
from pydantic_core import PydanticSerializationError

from arkui_ut_agent.agents import AgentState, StopReason, TaskMemory
from arkui_ut_agent.tools import Observation, Provenance

COLLECTION_FIELDS = (
    "target_files",
    "target_classes",
    "target_functions",
    "changed_files",
    "relevant_tests",
    "fixtures",
    "mocks",
    "completed_steps",
    "failed_attempts",
    "important_decisions",
)


@pytest.fixture
def full_payload():
    return {
        "target_component": "Text",
        "target_files": ["src/text.cc"],
        "target_classes": ["TextPattern"],
        "target_functions": ["TextPattern::OnModifyDone"],
        "changed_files": ["tests/text_test.cc"],
        "relevant_tests": ["TextTest.CoversProperty"],
        "fixtures": ["TextTest"],
        "mocks": ["MockPipelineContext"],
        "build_target": "text_test",
        "completed_steps": ["Read the implementation and similar tests"],
        "failed_attempts": ["Target execution failed because the build environment was missing"],
        "important_decisions": ["Use the existing TextTest fixture"],
    }


def test_working_memory_has_one_source_and_updates_do_not_promote_hypotheses():
    working_fields = {
        "goal", "current_plan", "current_step", "information_gap", "next_action",
        "open_questions", "hypotheses", "blocking_issue", "stop_reason", "retry_count",
    }
    assert set(AgentState.model_fields) == working_fields | {"task_memory"}
    assert not working_fields.intersection(TaskMemory.model_fields)

    state = AgentState(goal="task")
    updated = state.updated(hypotheses=["The target component might be Text"], next_action="Inspect source")

    assert updated.task_memory == TaskMemory()
    assert updated.model_dump(mode="json")["task_memory"]["target_component"] is None
    assert state.hypotheses == []


def test_minimal_task_memory_defaults_and_legacy_state_payload():
    expected = {"target_component": None, "build_target": None, **{field: [] for field in COLLECTION_FIELDS}}

    assert TaskMemory().model_dump(mode="json") == expected
    assert AgentState.model_validate({"goal": "task"}).task_memory.model_dump(mode="json") == expected
    assert AgentState.model_validate_json('{"goal": "task"}').task_memory == TaskMemory()


def test_full_task_memory_construction_and_serialization(full_payload):
    memory = TaskMemory(**full_payload)

    assert memory.model_dump(mode="json") == full_payload
    assert TaskMemory.model_validate(full_payload) == memory
    assert TaskMemory.model_validate_json(memory.model_dump_json()) == memory


@pytest.mark.parametrize("field", COLLECTION_FIELDS)
def test_collection_defaults_inputs_and_snapshots_are_isolated(field):
    first = TaskMemory()
    second = TaskMemory()
    getattr(first, field).append("first only")  # Raw mutation used only to test ownership.
    assert getattr(second, field) == []

    supplied = ["original"]
    memory = TaskMemory(**{field: supplied})
    supplied.append("input only")
    assert getattr(memory, field) == ["original"]

    updated = memory.updated()
    getattr(updated, field).append("copy only")
    assert getattr(memory, field) == ["original"]

    state = AgentState(goal="task", task_memory=memory)
    copied_state = state.updated(next_action="verify")
    getattr(memory, field).append("external memory only")
    getattr(copied_state.task_memory, field).append("new state only")
    assert getattr(state.task_memory, field) == ["original"]
    assert state.task_memory is not copied_state.task_memory


def test_supported_task_update_replaces_fields_without_merging_or_deduplication():
    memory = TaskMemory(target_files=["old.cc"], fixtures=["OriginalFixture"])

    updated = memory.updated(target_files=["new.cc", "new.cc"], build_target="unit_test")

    assert updated.target_files == ["new.cc", "new.cc"]
    assert updated.fixtures == ["OriginalFixture"]
    assert updated.build_target == "unit_test"
    assert memory.target_files == ["old.cc"]
    assert memory.build_target is None


def test_task_memory_update_keeps_original_agent_snapshot():
    original = AgentState(goal="task", hypotheses=["An unconfirmed idea"])
    updated = original.updated(task_memory=original.task_memory.updated(target_component="Text", mocks=["Mock"]))

    assert original.task_memory == TaskMemory()
    assert updated.task_memory.target_component == "Text"
    assert updated.task_memory.mocks == ["Mock"]
    assert updated.hypotheses == original.hypotheses
    assert updated.hypotheses is not original.hypotheses

    # A dictionary is a whole TaskMemory replacement, not a nested patch.
    replaced = updated.updated(task_memory={"relevant_tests": ["TextTest.Case"]})
    assert replaced.task_memory.target_component is None
    assert replaced.task_memory.mocks == []
    assert updated.task_memory.target_component == "Text"


@pytest.mark.parametrize(
    "payload",
    [
        {"target_component": "   "},
        {"build_target": 7},
        {"target_files": "not-a-list"},
        {"target_classes": [7]},
        {"target_functions": [object()]},
        {"failed_attempts": [{"unsupported": "workflow schema"}]},
    ],
)
def test_invalid_memory_construction_and_update_fail_deterministically(payload):
    with pytest.raises(ValidationError):
        TaskMemory.model_validate(payload)

    memory = TaskMemory()
    with pytest.raises(ValidationError):
        memory.updated(**payload)
    assert memory == TaskMemory()

    original = AgentState(goal="task")
    with pytest.raises(ValidationError):
        original.updated(task_memory=payload)
    assert original.task_memory == TaskMemory()


@pytest.mark.parametrize("field", ["messages", "hypotheses", "provenance", "evidence", "step_id"])
def test_unknown_memory_fields_are_rejected(field):
    with pytest.raises(ValidationError) as exc_info:
        TaskMemory(**{field: []})
    assert exc_info.value.errors()[0]["type"] == "extra_forbidden"

    with pytest.raises(ValidationError):
        AgentState(goal="task").updated(task_memory={field: []})


def test_task_memory_does_not_auto_ingest_observations_or_provenance():
    observation = Observation(
        tool_name="read_file", success=True, data={"target_component": "Text"},
        provenance=[Provenance(source="read_file", location="src/text.cc")],
    )
    with pytest.raises(ValidationError):
        TaskMemory.model_validate(observation.model_dump(mode="json"))


def test_task_memory_assignment_and_null_host_memory_are_rejected():
    memory = TaskMemory()
    with pytest.raises(ValidationError) as exc_info:
        memory.target_component = "Text"
    assert exc_info.value.errors()[0]["type"] == "frozen_instance"

    with pytest.raises(ValidationError):
        AgentState(goal="task", task_memory=None)


@pytest.mark.parametrize("field", COLLECTION_FIELDS)
def test_nested_memory_mutation_is_revalidated_even_during_working_update_or_exclusion(field):
    state = AgentState(goal="task")
    getattr(state.task_memory, field).append(object())  # Deliberately bypass supported updates.

    with pytest.raises(ValidationError):
        state.task_memory.updated()
    with pytest.raises(ValidationError):
        TaskMemory.model_validate(state.task_memory)
    with pytest.raises(ValidationError):
        state.updated(next_action="verify")
    with pytest.raises(ValidationError):
        AgentState.model_validate(state)

    with pytest.raises(PydanticSerializationError, match="ValidationError"):
        state.task_memory.model_dump(mode="json", exclude={field})
    with pytest.raises(PydanticSerializationError, match="ValidationError"):
        state.task_memory.model_dump_json()
    with pytest.raises(PydanticSerializationError, match="ValidationError"):
        state.model_dump()
    with pytest.raises(PydanticSerializationError, match="ValidationError"):
        state.model_dump(mode="json", exclude={"task_memory"})
    with pytest.raises(PydanticSerializationError, match="ValidationError"):
        state.model_dump_json()


def test_agent_and_task_memory_round_trip_without_conversation_history(full_payload):
    state = AgentState(
        goal="Add a Text unit test", current_plan=["inspect", "verify"], current_step="verify",
        hypotheses=["Not a confirmed fact"], task_memory=TaskMemory(**full_payload),
        retry_count=1, stop_reason=StopReason.SUCCESS,
    )

    payload = state.model_dump(mode="json")
    restored = AgentState.model_validate(payload)
    serialized = state.model_dump_json()

    assert payload["task_memory"] == full_payload
    assert "messages" not in payload
    assert "messages" not in payload["task_memory"]
    assert json.loads(serialized) == payload
    assert restored == state
    assert AgentState.model_validate_json(serialized) == state
    assert restored.task_memory is not state.task_memory
    restored.task_memory.target_files.append("restored only")
    assert state.task_memory.target_files == full_payload["target_files"]
