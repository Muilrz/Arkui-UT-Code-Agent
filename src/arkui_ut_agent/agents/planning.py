"""Structured, task-local planning data contracts and initial-plan parsing."""

import json
from typing import Annotated, Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SerializerFunctionWrapHandler,
    StringConstraints,
    model_serializer,
    model_validator,
)

NonEmptyString = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
PositiveInt = Annotated[int, Field(strict=True, ge=1)]

_INITIAL_PLANNING_INSTRUCTION = """Create the initial plan for the current task.
Use the existing single-action response transport, but do not return a shell command.
The action's command string must contain only one compact JSON object matching:
{"revision":1,"steps":[{"id":"...","description":"..."}],"active_step_id":"..."}
Step ids must be unique. active_step_id may be null; the runtime will then select the first step.
Do not perform the task, execute tools, or add fields outside this Plan schema."""


class PlanStep(BaseModel):
    """One stable, plan-local unit of work.

    ``id`` identifies this item only within its containing Plan. It is not the
    runtime execution ``step_id`` used by Tool calls, Evidence, or memory events.
    The contract intentionally does not prescribe Planner prompts, actions, status,
    or control decisions.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, revalidate_instances="always")

    id: NonEmptyString
    description: NonEmptyString

    def updated(self, **changes: Any) -> "PlanStep":
        """Return a fully validated replacement without changing this step."""
        return type(self).model_validate({**dict(self), **changes})

    @model_serializer(mode="wrap")
    def _serialize_validated_step(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        return handler(type(self).model_validate(dict(self)))


class Plan(BaseModel):
    """An ordered, immutable planning snapshot for one task.

    ``active_step_id`` is a plan-local reference to one of ``steps``. Runtime
    producing-step identity remains ``AgentState.current_step`` and is deliberately
    outside this model. ``revision`` is caller-managed; this contract does not
    implement planning or replanning policy.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, revalidate_instances="always")

    revision: PositiveInt = 1
    steps: tuple[PlanStep, ...] = Field(min_length=1)
    active_step_id: NonEmptyString | None = None

    @model_validator(mode="after")
    def _validate_step_references(self) -> "Plan":
        step_ids = [step.id for step in self.steps]
        if len(step_ids) != len(set(step_ids)):
            raise ValueError("PlanStep ids must be unique within a Plan.")
        if self.active_step_id is not None and self.active_step_id not in set(step_ids):
            raise ValueError("active_step_id must reference a PlanStep in this Plan.")
        return self

    def updated(self, **changes: Any) -> "Plan":
        """Return a fully validated replacement without merging nested fields."""
        return type(self).model_validate({**dict(self), **changes})

    @model_serializer(mode="wrap")
    def _serialize_validated_plan(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        return handler(type(self).model_validate(dict(self)))


def _parse_initial_plan(message: dict[str, Any]) -> Plan:
    """Parse one normalized action transport as a validated initial Plan.

    Model providers already normalize their action-capable responses to
    ``extra.actions``. Initial planning reuses that boundary but never executes the
    action. The command string is JSON data, not a shell command. A missing active
    step deterministically selects the first validated PlanStep.
    """
    extra = message.get("extra")
    actions = extra.get("actions") if isinstance(extra, dict) else None
    if not isinstance(actions, list) or len(actions) != 1:
        raise ValueError("Initial planning requires exactly one Plan action transport.")
    action = actions[0]
    if not isinstance(action, dict) or not isinstance(action.get("command"), str):
        raise ValueError("Initial planning requires a string Plan payload in action.command.")
    try:
        payload = json.loads(action["command"])
    except json.JSONDecodeError as error:
        raise ValueError("Initial planning action.command must be valid JSON.") from error
    if not isinstance(payload, dict):
        raise ValueError("Initial planning JSON must be an object matching Plan.")
    plan = Plan.model_validate(payload)
    if plan.active_step_id is None:
        plan = plan.updated(active_step_id=plan.steps[0].id)
    return plan


__all__ = ["Plan", "PlanStep"]
