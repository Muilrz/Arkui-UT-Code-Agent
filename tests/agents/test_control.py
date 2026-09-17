"""Deterministic tests for the Stage 4 control-decision contract."""

import pytest
from pydantic import ValidationError

from arkui_ut_agent.agents import AgentState, ContextBuilder, ControlDecision, DecisionKind


@pytest.mark.parametrize("kind", [item.value for item in DecisionKind])
def test_all_control_decisions_validate_and_serialize(kind):
    decision = ControlDecision(kind=kind, rationale=f"Use {kind} next")

    assert decision.kind.value == kind
    assert decision.model_dump(mode="json") == {
        "kind": kind,
        "rationale": f"Use {kind} next",
    }
    assert ControlDecision.model_validate_json(decision.model_dump_json()) == decision


@pytest.mark.parametrize(
    "payload",
    [
        {"kind": "unknown", "rationale": "invalid kind"},
        {"kind": "act", "rationale": "   "},
        {"kind": "act", "rationale": "valid", "workflow": "execute"},
    ],
)
def test_invalid_control_decisions_are_rejected(payload):
    with pytest.raises(ValidationError):
        ControlDecision.model_validate(payload)


def test_decision_round_trips_through_agent_state_and_bounded_context():
    state = AgentState(
        goal="Repair Text behavior",
        current_decision=ControlDecision(
            kind=DecisionKind.REPAIR,
            rationale="The observed edit command failed without changing the file",
        ),
    )

    restored = AgentState.model_validate_json(state.model_dump_json())
    context = ContextBuilder().build(restored)

    assert restored == state
    assert restored.current_decision is not state.current_decision
    assert '"current_decision":{"kind":"repair"' in context.text
    assert "The observed edit command failed" in context.text

