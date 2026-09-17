"""Structured, task-local planning data contracts."""

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


__all__ = ["Plan", "PlanStep"]
