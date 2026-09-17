import pytest
from pydantic import ValidationError
from pydantic_core import PydanticSerializationError

from arkui_ut_agent.agents import AgentState, Plan, PlanStep


def _plan() -> Plan:
    return Plan(
        revision=2,
        steps=(
            PlanStep(id="inspect", description="Inspect the implementation"),
            PlanStep(id="verify", description="Run the focused test"),
        ),
        active_step_id="inspect",
    )


def test_plan_and_steps_validate_and_serialize_as_a_stable_contract():
    plan = Plan.model_validate({
        "revision": 2,
        "steps": [
            {"id": " inspect ", "description": " Inspect the implementation "},
            {"id": "verify", "description": "Run the focused test"},
        ],
        "active_step_id": "inspect",
    })

    assert plan == _plan()
    assert isinstance(plan.steps, tuple)
    assert plan.model_dump(mode="json") == {
        "revision": 2,
        "steps": [
            {"id": "inspect", "description": "Inspect the implementation"},
            {"id": "verify", "description": "Run the focused test"},
        ],
        "active_step_id": "inspect",
    }
    assert Plan.model_validate_json(plan.model_dump_json()) == plan


@pytest.mark.parametrize(
    "payload",
    [
        {"steps": []},
        {"revision": 0, "steps": [{"id": "inspect", "description": "Inspect"}]},
        {"revision": "1", "steps": [{"id": "inspect", "description": "Inspect"}]},
        {"steps": [{"id": "", "description": "Inspect"}]},
        {"steps": [{"id": "inspect", "description": "   "}]},
        {"steps": [{"id": "same", "description": "First"}, {"id": "same", "description": "Second"}]},
        {"steps": [{"id": "inspect", "description": "Inspect"}], "active_step_id": "missing"},
        {"steps": [{"id": "inspect", "description": "Inspect", "action": "read"}]},
        {"steps": [{"id": "inspect", "description": "Inspect"}], "decision": "act"},
    ],
)
def test_invalid_plan_shapes_fail_deterministically(payload):
    with pytest.raises(ValidationError):
        Plan.model_validate(payload)


def test_validated_updates_are_immutable_and_replace_complete_fields():
    plan = _plan()
    updated_step = plan.steps[1].updated(description="Run all directly related tests")
    updated = plan.updated(
        revision=3,
        steps=(plan.steps[0], updated_step),
        active_step_id="verify",
    )

    assert updated.revision == 3
    assert updated.active_step_id == "verify"
    assert updated.steps[1].description == "Run all directly related tests"
    assert plan.revision == 2
    assert plan.active_step_id == "inspect"
    assert plan.steps[1].description == "Run the focused test"

    with pytest.raises(ValidationError):
        plan.revision = 3
    with pytest.raises(ValidationError):
        plan.steps[0].description = "Changed"
    with pytest.raises(ValidationError):
        plan.updated(active_step_id="missing")
    with pytest.raises(ValidationError):
        plan.steps[0].updated(description="")


def test_unsafe_plan_copy_is_revalidated_before_serialization():
    unsafe = _plan().model_copy(update={"active_step_id": "missing"})

    with pytest.raises(PydanticSerializationError, match="ValidationError"):
        unsafe.model_dump_json()


def test_agent_state_requires_structured_plan_and_runtime_step_label():
    plan = _plan()
    state = AgentState(goal="task", current_plan=plan, current_step="step-4")

    restored = AgentState.model_validate_json(state.model_dump_json())

    assert restored == state
    assert restored.current_plan == plan
    assert restored.current_plan is not plan
    assert restored.current_step == "step-4"
    with pytest.raises(ValidationError):
        AgentState(goal="task", current_plan=["inspect", "verify"])
    with pytest.raises(ValidationError):
        AgentState(goal="task", current_plan={"steps": ["inspect"]})
    with pytest.raises(ValidationError):
        AgentState(goal="task", current_step={"id": "inspect"})
