"""Deterministic Stage 4 stop policy over existing runtime signals."""

from arkui_ut_agent.agents.control import ControlDecision, DecisionKind
from arkui_ut_agent.agents.state import StopReason
from arkui_ut_agent.tools.contracts import Observation


class StopPolicy:
    """Map validated runtime signals to terminal reasons without inferring facts."""

    ACTIONABLE_DECISIONS = frozenset({
        DecisionKind.RETRIEVE,
        DecisionKind.ACT,
        DecisionKind.REPAIR,
        DecisionKind.VERIFY,
    })

    @staticmethod
    def for_decision(decision: ControlDecision | None) -> StopReason | None:
        if decision is not None and decision.kind is DecisionKind.FINISH:
            return StopReason.SUCCESS
        return None

    @staticmethod
    def for_limits(*, step_limit: int, n_calls: int, cost_limit: float, cost: float) -> StopReason | None:
        if 0 < step_limit <= n_calls:
            return StopReason.STEP_LIMIT
        if 0 < cost_limit <= cost:
            return StopReason.COST_LIMIT
        return None

    @staticmethod
    def for_observation(observation: Observation) -> StopReason | None:
        """Honor only explicit terminal diagnostic codes on failed Observations."""
        if observation.success:
            return None
        codes = {diagnostic.code for diagnostic in observation.diagnostics}
        if StopReason.TOOL_UNAVAILABLE.value in codes:
            return StopReason.TOOL_UNAVAILABLE
        if StopReason.IRRECOVERABLE_FAILURE.value in codes:
            return StopReason.IRRECOVERABLE_FAILURE
        return None

    @staticmethod
    def for_repeat_after_replan(*, evidence_changed: bool) -> StopReason:
        return StopReason.REPEATED_FAILURE if evidence_changed else StopReason.NO_NEW_EVIDENCE


__all__ = ["StopPolicy"]
