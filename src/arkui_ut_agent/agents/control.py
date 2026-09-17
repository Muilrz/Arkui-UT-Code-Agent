"""Validated control decisions and control-only Diagnose response parsing."""

import json
from enum import Enum
from typing import Annotated, Any

from pydantic import (
    BaseModel,
    ConfigDict,
    SerializerFunctionWrapHandler,
    StringConstraints,
    model_serializer,
)

NonEmptyString = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]

_DIAGNOSE_INSTRUCTION = """Diagnose the recorded Tool Observation using only the supplied Agent State context and Tool records.
Choose exactly one next control intention: retrieve, act, diagnose, replan, repair, verify, or finish.
Use the existing single-action response transport, but do not return a shell command.
The action's command string must contain only one compact JSON object matching:
{"kind":"retrieve","rationale":"..."}
The rationale must explain the diagnosis grounded in the supplied state/Observation. Do not execute tools,
revise the Plan, claim new confirmed Evidence, or add fields outside this ControlDecision schema."""


class DecisionKind(str, Enum):
    """The explicit next-control intentions supported by the Stage 4 runtime."""

    RETRIEVE = "retrieve"
    ACT = "act"
    DIAGNOSE = "diagnose"
    REPLAN = "replan"
    REPAIR = "repair"
    VERIFY = "verify"
    FINISH = "finish"


class ControlDecision(BaseModel):
    """A validated next-control intention with its model-visible diagnosis.

    The rationale is control state, not confirmed Evidence. This contract records
    what should happen next but deliberately does not dispatch that workflow.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, revalidate_instances="always")

    kind: DecisionKind
    rationale: NonEmptyString

    def updated(self, **changes: Any) -> "ControlDecision":
        """Return a fully validated replacement snapshot."""
        return type(self).model_validate({**dict(self), **changes})

    @model_serializer(mode="wrap")
    def _serialize_validated_decision(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        return handler(type(self).model_validate(dict(self)))


def _parse_control_decision(message: dict[str, Any]) -> ControlDecision:
    """Parse one normalized action transport as non-executable decision data."""
    extra = message.get("extra")
    actions = extra.get("actions") if isinstance(extra, dict) else None
    if not isinstance(actions, list) or len(actions) != 1:
        raise ValueError("Diagnose requires exactly one ControlDecision action transport.")
    action = actions[0]
    if not isinstance(action, dict) or not isinstance(action.get("command"), str):
        raise ValueError("Diagnose requires a string ControlDecision payload in action.command.")
    try:
        payload = json.loads(action["command"])
    except json.JSONDecodeError as error:
        raise ValueError("Diagnose action.command must be valid JSON.") from error
    if not isinstance(payload, dict):
        raise ValueError("Diagnose JSON must be an object matching ControlDecision.")
    return ControlDecision.model_validate(payload)


__all__ = ["ControlDecision", "DecisionKind"]
