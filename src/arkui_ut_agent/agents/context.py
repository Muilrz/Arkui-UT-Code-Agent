"""Deterministic, bounded selection from the existing AgentState contract.

The builder performs small task-local relevance and Evidence priority decisions. It
does not summarize failures, compress source snippets, define planning contracts, or
wire the result into the runtime model call.
"""

import json
import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from arkui_ut_agent.agents.state import AgentState, Evidence, TaskMemory

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
_TOKEN_PATTERN = re.compile(r"[^\W_]+", flags=re.UNICODE)
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_IGNORED_RELEVANCE_TOKENS = frozenset({
    "cc",
    "class",
    "component",
    "cpp",
    "file",
    "fixture",
    "function",
    "id",
    "mock",
    "pattern",
    "src",
    "step",
    "test",
    "tests",
})
_TRUNCATION_ORDER: tuple[ContextSectionName, ...] = (
    "task_memory",
    "evidence_memory",
    "working_memory",
    "current_plan",
    "user_task",
)


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

    Task Memory values are retained when their lexical terms overlap the current
    task/working focus; the two scalar anchors remain available when confirmed.
    Evidence is ordered current-step first, then failing build/test diagnostics, then
    lexically relevant facts. If none qualify, the latest fact is retained as a
    deterministic fallback. Full selected records preserve the Stage 2 fields and add
    their derived identity to the context view.

    Full section contents are canonical compact JSON. Budget pressure truncates Task
    Memory before the prioritized Evidence tail, then lower-level fallbacks, while
    keeping every header visible. This is suffix fitting, not failure summarization or
    source snippet compression.
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
            self._select_task_memory(snapshot).model_dump(mode="json"),
            {"records": [self._evidence_view(record) for record in self._select_evidence(snapshot)]},
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

    @classmethod
    def _focus_tokens(cls, state: AgentState) -> frozenset[str]:
        return cls._tokens((
            state.goal,
            state.current_plan,
            state.current_step,
            state.information_gap,
            state.next_action,
            state.open_questions,
            state.blocking_issue,
        ))

    @classmethod
    def _tokens(cls, value: object) -> frozenset[str]:
        tokens: set[str] = set()

        def collect(item: object) -> None:
            if isinstance(item, str):
                separated = cls._separate_camel_case(item)
                tokens.update(
                    normalized
                    for token in _TOKEN_PATTERN.findall(separated)
                    if len(token) > 1
                    and (normalized := token.casefold()) not in _IGNORED_RELEVANCE_TOKENS
                )
            elif isinstance(item, dict):
                for nested in item.values():
                    collect(nested)
            elif isinstance(item, (list, tuple)):
                for nested in item:
                    collect(nested)

        collect(value)
        return frozenset(tokens)

    @staticmethod
    def _separate_camel_case(value: str) -> str:
        return _CAMEL_BOUNDARY.sub(" ", value)

    @classmethod
    def _select_task_memory(cls, state: AgentState) -> TaskMemory:
        focus = cls._focus_tokens(state)
        memory = state.task_memory
        selected: dict[str, object] = {
            "target_component": memory.target_component,
            "build_target": memory.build_target,
        }
        for field_name in (
            "target_files",
            "target_classes",
            "target_functions",
            "changed_files",
            "relevant_tests",
            "fixtures",
            "mocks",
            "completed_steps",
            "failed_attempts",
            "important_decisions",
        ):
            selected[field_name] = [
                item for item in getattr(memory, field_name) if cls._tokens(item).intersection(focus)
            ]
        return TaskMemory.model_validate(selected)

    @classmethod
    def _select_evidence(cls, state: AgentState) -> tuple[Evidence, ...]:
        focus = cls._focus_tokens(state)
        current_step = state.current_step if isinstance(state.current_step, str) and state.current_step.strip() else None
        candidates: list[tuple[int, int, Evidence]] = []
        for index, record in enumerate(state.evidence_memory.records):
            current = current_step is not None and record.step_id == current_step
            diagnostic = cls._is_failing_build_or_test(record)
            relevance_view = record.model_dump(mode="json", exclude={"step_id"})
            relevant = bool(cls._tokens(relevance_view).intersection(focus))
            if current or diagnostic or relevant:
                priority = 0 if current else 1 if diagnostic else 2
                candidates.append((priority, -index, record))

        if not candidates and state.evidence_memory.records:
            return (state.evidence_memory.records[-1],)
        candidates.sort(key=lambda item: (item[0], item[1]))
        return tuple(record for _, _, record in candidates)

    @staticmethod
    def _is_failing_build_or_test(record: Evidence) -> bool:
        content = record.content
        return (
            record.source in {"build", "test"}
            and isinstance(content, dict)
            and content.get("success") is False
            and isinstance(content.get("diagnostics"), list)
            and bool(content["diagnostics"])
        )

    @staticmethod
    def _evidence_view(record: Evidence) -> dict[str, object]:
        return {"identity": record.identity, **record.model_dump(mode="json")}

    def _fit_budget(
        self,
        parts: list[tuple[ContextSectionName, str, bool]],
    ) -> list[tuple[ContextSectionName, str, bool]]:
        rendered = _render_parts([(name, content) for name, content, _ in parts])
        excess = len(rendered) - self.budget.max_chars
        if excess <= 0:
            return parts

        fitted = list(parts)
        indexes = {name: index for index, (name, _, _) in enumerate(fitted)}
        for name in _TRUNCATION_ORDER:
            index = indexes[name]
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
