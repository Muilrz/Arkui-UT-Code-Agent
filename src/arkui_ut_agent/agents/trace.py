"""Minimal per-step trace required by the Stage 4 control-loop acceptance."""

from typing import Annotated, Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    SerializerFunctionWrapHandler,
    StringConstraints,
    model_serializer,
)

from arkui_ut_agent.agents.control import ControlDecision

NonEmptyString = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class ExecutionStepTrace(BaseModel):
    """One runtime producing step, separate from plan-local PlanStep identity."""

    model_config = ConfigDict(extra="forbid", frozen=True, revalidate_instances="always")

    step_id: NonEmptyString
    current_goal: NonEmptyString
    decision: ControlDecision
    actions: tuple[JsonValue, ...] = ()
    observations: tuple[JsonValue, ...] = ()
    state_updates: tuple[NonEmptyString, ...] = Field(min_length=1)

    def updated(self, **changes: Any) -> "ExecutionStepTrace":
        return type(self).model_validate({**dict(self), **changes})

    @model_serializer(mode="wrap")
    def _serialize_validated_trace(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        return handler(type(self).model_validate(dict(self)))


__all__ = ["ExecutionStepTrace"]
