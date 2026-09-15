"""Narrow mapping used only after actual Tool execution, never from chat messages."""

from arkui_ut_agent.agents.state import Evidence
from arkui_ut_agent.tools.contracts import Observation


def _evidence_from_tool_observation(observation: Observation, step_id: str) -> tuple[Evidence | None, str]:
    """Copy the entire result envelope, not inferred/selected facts.

    Success and failure both record actual data, diagnostics or a nonblank Tool
    summary. A failure records what the Tool reported, not success of its action.
    Missing/invalid provenance, non-JSON payloads and empty results degrade to skip;
    no origin or content is fabricated. Multiple differing locations remain None
    at the top level, with every original location retained in provenance.

    This internal function cannot authenticate provenance: its caller must be the
    execution boundary. It deliberately rejects arbitrary text and message dicts.
    """
    if not isinstance(observation, Observation):
        raise TypeError("Expected an Observation from actual Tool execution.")
    try:
        # Stage 1 instances are mutable; validate raw nested fields, not instance identity.
        observation = Observation.model_validate({
            **dict(observation),
            "diagnostics": [dict(item) for item in observation.diagnostics],
            "provenance": [dict(item) for item in observation.provenance],
        })
        if not observation.provenance:
            return None, "missing_provenance"
        if observation.data in (None, "", [], {}) and not observation.summary.strip() and not observation.diagnostics:
            return None, "empty_result"
        locations = {item.location for item in observation.provenance if item.location is not None}
        evidence = Evidence(
            source=observation.tool_name,
            location=next(iter(locations)) if len(locations) == 1 else None,
            content={
                "success": observation.success,
                "data": observation.data,
                "diagnostics": [dict(item) for item in observation.diagnostics],
            },
            summary=observation.summary.strip() or None,
            provenance=observation.provenance,
            step_id=step_id,
        )
    except (ValueError, TypeError, AttributeError):
        return None, "invalid_provenance_or_payload"
    return evidence, "mapped"
