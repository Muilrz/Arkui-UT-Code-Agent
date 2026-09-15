"""Task-scoped execution state for the ArkUI UT agent control plane."""

from enum import Enum
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

NonEmptyString = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
NonNegativeInt = Annotated[int, Field(strict=True, ge=0)]


class StopReason(str, Enum):
    """Terminal reasons required by the agent runtime contract."""

    SUCCESS = "success"
    STEP_LIMIT = "step_limit"
    COST_LIMIT = "cost_limit"
    REPEATED_FAILURE = "repeated_failure"
    NO_NEW_EVIDENCE = "no_new_evidence"
    TOOL_UNAVAILABLE = "tool_unavailable"
    IRRECOVERABLE_FAILURE = "irrecoverable_failure"


class AgentState(BaseModel):
    """Minimal, serializable state for one task execution.

    ``current_plan`` and ``current_step`` deliberately remain opaque JSON values
    until Stage 4 defines the project-owned Plan and PlanStep contracts. Conversation
    messages and evidence do not belong to this foundation model.

    Supported updates use ``updated()`` to produce a fully validated new snapshot.
    Field assignment, in-place container mutation, ``model_copy(update=...)`` and
    ``model_construct()`` are not supported update interfaces. Every Pydantic dump
    revalidates the full snapshot, including fields excluded from the output, so an
    invalid in-place mutation cannot silently produce persisted state.
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        revalidate_instances="always",
        allow_inf_nan=False,
    )

    goal: NonEmptyString
    current_plan: JsonValue = None
    current_step: JsonValue = None
    information_gap: NonEmptyString | None = None
    next_action: NonEmptyString | None = None
    open_questions: list[NonEmptyString] = Field(default_factory=list)
    hypotheses: list[NonEmptyString] = Field(default_factory=list)
    blocking_issue: NonEmptyString | None = None
    stop_reason: StopReason | None = None
    retry_count: NonNegativeInt = 0

    def updated(self, **changes: Any) -> "AgentState":
        """Return an isolated snapshot after validating all existing and changed fields."""
        return type(self).model_validate({**dict(self), **changes})

    @model_serializer(mode="wrap")
    def _serialize_validated_state(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        """Reject invalid raw/container mutations before Pydantic converts any values."""
        validated = type(self).model_validate(dict(self))
        return handler(validated)


__all__ = ["AgentState", "StopReason"]
