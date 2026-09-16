"""Deterministic, bounded context construction from the existing AgentState contract.

Slice 3A intentionally provides only section and character-budget foundations. It
does not rank facts, prioritize diagnostics, summarize failures, select snippets,
or wire the result into the runtime model call.
"""

import json
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from arkui_ut_agent.agents.state import AgentState

ContextSectionName = Literal[
    "user_task",
    "current_plan",
    "working_memory",
    "task_memory",
    "evidence_memory",
]

CONTEXT_SECTION_ORDER: tuple[ContextSectionName, ...] = (
    "user_task",
    "current_plan",
    "working_memory",
    "task_memory",
    "evidence_memory",
)
DEFAULT_CONTEXT_MAX_CHARS = 16_000
TRUNCATION_MARKER = "…"


def _render_section(name: ContextSectionName, content: str) -> str:
    return f"## {name}\n{content}"


def _render_parts(parts: list[tuple[ContextSectionName, str]]) -> str:
    return "\n\n".join(_render_section(name, content) for name, content in parts)


MIN_CONTEXT_CHARS = len(_render_parts([(name, TRUNCATION_MARKER) for name in CONTEXT_SECTION_ORDER]))

StrictContent = Annotated[str, StringConstraints(strict=True, min_length=1)]
ContextCharLimit = Annotated[int, Field(strict=True, ge=MIN_CONTEXT_CHARS)]


class ContextBudget(BaseModel):
    """Tokenizer-independent hard bound for the rendered context.

    The unit is Python Unicode characters, not estimated model tokens. Keeping the
    unit explicit makes the policy deterministic across model providers. The lower
    bound reserves every section header plus one visible truncation marker.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, revalidate_instances="always")

    max_chars: ContextCharLimit = DEFAULT_CONTEXT_MAX_CHARS


class ContextSection(BaseModel):
    """One rendered section after the total budget has been applied."""

    model_config = ConfigDict(extra="forbid", frozen=True, revalidate_instances="always")

    name: ContextSectionName
    content: StrictContent
    truncated: bool = False


class BuiltContext(BaseModel):
    """Immutable bounded context with a stable public section order."""

    model_config = ConfigDict(extra="forbid", frozen=True, revalidate_instances="always")

    budget: ContextBudget
    sections: tuple[ContextSection, ...]

    @model_validator(mode="after")
    def _validate_contract(self) -> "BuiltContext":
        names = tuple(section.name for section in self.sections)
        if names != CONTEXT_SECTION_ORDER:
            raise ValueError(f"Context sections must be ordered as {CONTEXT_SECTION_ORDER!r}.")
        if len(self.text) > self.budget.max_chars:
            raise ValueError("Rendered context exceeds its character budget.")
        return self

    @property
    def text(self) -> str:
        """Render the stable plain-text model input representation."""
        return _render_parts([(section.name, section.content) for section in self.sections])

    @property
    def char_count(self) -> int:
        return len(self.text)


class ContextBuilder:
    """Build bounded context solely from a validated Stage 2 AgentState snapshot.

    Full section contents are canonical compact JSON. Budget pressure may replace a
    JSON suffix with a visible truncation marker, removing suffixes from later
    sections first while keeping every header visible. Selection inside Task/Evidence
    Memory is deliberately deferred; this slice serializes their ordered contents as-is.
    """

    def __init__(self, budget: ContextBudget | None = None) -> None:
        self.budget = ContextBudget() if budget is None else ContextBudget.model_validate(budget)

    def build(self, state: AgentState) -> BuiltContext:
        """Validate the existing state contract and return a deterministic snapshot."""
        snapshot = AgentState.model_validate(state)
        payload = snapshot.model_dump(mode="json")
        working_memory = {
            key: payload[key]
            for key in (
                "current_step",
                "information_gap",
                "next_action",
                "open_questions",
                "hypotheses",
                "blocking_issue",
                "stop_reason",
                "retry_count",
            )
        }
        section_values = (
            snapshot.goal,
            snapshot.current_plan,
            working_memory,
            payload["task_memory"],
            payload["evidence_memory"],
        )
        parts = [
            (name, self._canonical_json(value), False)
            for name, value in zip(CONTEXT_SECTION_ORDER, section_values, strict=True)
        ]
        fitted = self._fit_budget(parts)
        sections = tuple(ContextSection(name=name, content=content, truncated=truncated) for name, content, truncated in fitted)
        return BuiltContext(budget=self.budget, sections=sections)

    @staticmethod
    def _canonical_json(value: object) -> str:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)

    def _fit_budget(
        self,
        parts: list[tuple[ContextSectionName, str, bool]],
    ) -> list[tuple[ContextSectionName, str, bool]]:
        rendered = _render_parts([(name, content) for name, content, _ in parts])
        excess = len(rendered) - self.budget.max_chars
        if excess <= 0:
            return parts

        fitted = list(parts)
        for index in range(len(fitted) - 1, -1, -1):
            name, content, _ = fitted[index]
            reducible = len(content) - len(TRUNCATION_MARKER)
            if reducible <= 0:
                continue
            reduction = min(excess, reducible)
            target_length = len(content) - reduction
            prefix_length = target_length - len(TRUNCATION_MARKER)
            fitted[index] = (name, content[:prefix_length] + TRUNCATION_MARKER, True)
            excess -= reduction
            if excess == 0:
                break

        if excess != 0:  # ContextBudget's lower bound makes this an internal invariant.
            raise RuntimeError("Context budget could not preserve the required section contract.")
        return fitted


__all__ = [
    "CONTEXT_SECTION_ORDER",
    "DEFAULT_CONTEXT_MAX_CHARS",
    "MIN_CONTEXT_CHARS",
    "BuiltContext",
    "ContextBudget",
    "ContextBuilder",
    "ContextSection",
    "ContextSectionName",
]
