"""Deterministic, bounded selection from the existing AgentState contract.

The builder performs small task-local relevance, compression and Evidence priority
decisions. It does not use LLM summarization or implement planning behavior.
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
_CONTENT_OMISSION_MARKER = "\n…[context content omitted]…\n"
_DUPLICATE_SNIPPET_MARKER = "…[duplicate source snippet omitted]…"
_MAX_FAILURE_ATTEMPTS = 5
_MAX_FAILURE_TEXT_CHARS = 240
_MAX_HISTORICAL_FAILURE_EVIDENCE = 3
_MAX_EXECUTION_OUTPUT_CHARS = 1_200
_MAX_SOURCE_SNIPPET_CHARS = 1_200
_MAX_SOURCE_SNIPPET_LINES = 40
_MAX_RG_MATCHES = 20
_MAX_RG_MATCH_TEXT_CHARS = 240
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
    """Build bounded context solely from a validated AgentState snapshot.

    Task Memory values are retained when their lexical terms overlap the current
    task/working focus; the two scalar anchors remain available when confirmed.
    Evidence is ordered current-step first, then the latest failing build/test
    diagnostics, then lexically relevant facts. If none qualify, the latest fact is
    retained as a deterministic fallback. Full selected records preserve the Stage 2
    fields and add their derived identity to the context view.

    Relevant failure history is exact-deduplicated and bounded. Only the established
    read_file and rg_search payloads receive narrow source controls: relevant line
    windows, text/match limits and exact duplicate removal. These are context-view
    transformations; stored Evidence and its identity remain unchanged.

    Full section contents are canonical compact JSON. Budget pressure truncates Task
    Memory before the prioritized Evidence tail, then lower-level fallbacks, while
    keeping every header visible.
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
            payload["current_plan"],
            working_memory,
            self._select_task_memory(snapshot).model_dump(mode="json"),
            {"records": self._evidence_views(self._select_evidence(snapshot), snapshot)},
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
            None if state.current_plan is None else state.current_plan.model_dump(mode="json"),
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
            values = [
                item for item in getattr(memory, field_name) if cls._tokens(item).intersection(focus)
            ]
            selected[field_name] = cls._compress_failures(values) if field_name == "failed_attempts" else values
        return TaskMemory.model_validate(selected)

    @classmethod
    def _compress_failures(cls, failures: list[str]) -> list[str]:
        grouped: dict[str, tuple[str, int]] = {}
        for failure in reversed(failures):
            normalized = " ".join(failure.split())
            key = normalized.casefold()
            if key in grouped:
                representative, count = grouped[key]
                grouped[key] = (representative, count + 1)
            else:
                grouped[key] = (normalized, 1)

        selected = []
        for text, count in list(grouped.values())[:_MAX_FAILURE_ATTEMPTS]:
            suffix = f" [repeated {count} times]" if count > 1 else ""
            bounded = cls._bound_text(
                str(text),
                _MAX_FAILURE_TEXT_CHARS - len(suffix),
                "…[failure text omitted]…",
            )
            selected.append(bounded + suffix)
        omitted = len(grouped) - len(selected)
        if omitted:
            selected.append(f"…[{omitted} older relevant failure(s) omitted]…")
        return selected

    @classmethod
    def _select_evidence(cls, state: AgentState) -> tuple[Evidence, ...]:
        focus = cls._focus_tokens(state)
        current_step = state.current_step
        current_records: list[tuple[int, Evidence]] = []
        diagnostic_records: list[tuple[int, Evidence]] = []
        relevant_records: list[tuple[int, Evidence]] = []
        for index, record in enumerate(state.evidence_memory.records):
            current = current_step is not None and record.step_id == current_step
            diagnostic = cls._is_failing_build_or_test(record)
            relevance_view = record.model_dump(mode="json", exclude={"step_id"})
            relevant = bool(cls._tokens(relevance_view).intersection(focus))
            if current:
                current_records.append((index, record))
            elif diagnostic:
                diagnostic_records.append((index, record))
            elif relevant:
                relevant_records.append((index, record))

        selected = [record for _, record in sorted(current_records, reverse=True)]
        selected.extend(
            record
            for _, record in sorted(diagnostic_records, reverse=True)[:_MAX_HISTORICAL_FAILURE_EVIDENCE]
        )
        selected.extend(record for _, record in sorted(relevant_records, reverse=True))
        if not selected and state.evidence_memory.records:
            return (state.evidence_memory.records[-1],)
        return tuple(selected)

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

    @classmethod
    def _evidence_views(cls, records: tuple[Evidence, ...], state: AgentState) -> list[dict[str, object]]:
        focus = cls._focus_tokens(state)
        seen_read_snippets: set[tuple[object, ...]] = set()
        seen_rg_matches: set[tuple[object, ...]] = set()
        return [
            cls._evidence_view(record, focus, seen_read_snippets, seen_rg_matches)
            for record in records
        ]

    @classmethod
    def _evidence_view(
        cls,
        record: Evidence,
        focus: frozenset[str],
        seen_read_snippets: set[tuple[object, ...]],
        seen_rg_matches: set[tuple[object, ...]],
    ) -> dict[str, object]:
        payload = record.model_dump(mode="json")
        content = payload.get("content")
        if isinstance(content, dict):
            if record.source == "read_file":
                cls._control_read_file(content, focus, seen_read_snippets)
            elif record.source == "rg_search":
                cls._control_rg_search(content, seen_rg_matches)
            elif record.source in {"build", "test"} and content.get("success") is False:
                cls._control_failure_output(content)
        return {"identity": record.identity, **payload}

    @classmethod
    def _control_read_file(
        cls,
        content: dict[str, object],
        focus: frozenset[str],
        seen: set[tuple[object, ...]],
    ) -> None:
        data = content.get("data")
        if not isinstance(data, dict) or not isinstance(data.get("content"), str):
            return
        source = data["content"]
        key = (data.get("path"), data.get("start_line"), data.get("end_line"), source)
        if key in seen:
            data["content"] = _DUPLICATE_SNIPPET_MARKER
            return
        seen.add(key)
        data["content"] = cls._select_source_lines(source, focus)

    @classmethod
    def _select_source_lines(cls, source: str, focus: frozenset[str]) -> str:
        lines = source.splitlines(keepends=True)
        if not lines:
            return source
        matching = [index for index, line in enumerate(lines) if cls._tokens(line).intersection(focus)]
        if matching:
            indexes = sorted({
                candidate
                for index in matching
                for candidate in range(max(0, index - 1), min(len(lines), index + 2))
            })
        else:
            half = _MAX_SOURCE_SNIPPET_LINES // 2
            indexes = list(range(min(half, len(lines))))
            indexes.extend(range(max(half, len(lines) - half), len(lines)))
            indexes = sorted(set(indexes))
        if len(indexes) > _MAX_SOURCE_SNIPPET_LINES:
            half = _MAX_SOURCE_SNIPPET_LINES // 2
            indexes = indexes[:half] + indexes[-half:]
        selected = cls._join_line_ranges(lines, indexes)
        return cls._bound_text(selected, _MAX_SOURCE_SNIPPET_CHARS, _CONTENT_OMISSION_MARKER)

    @staticmethod
    def _join_line_ranges(lines: list[str], indexes: list[int]) -> str:
        parts = []
        previous = -2
        for index in indexes:
            if index != previous + 1 and parts:
                parts.append(_CONTENT_OMISSION_MARKER)
            parts.append(lines[index])
            previous = index
        return "".join(parts)

    @classmethod
    def _control_rg_search(
        cls,
        content: dict[str, object],
        seen: set[tuple[object, ...]],
    ) -> None:
        data = content.get("data")
        if not isinstance(data, dict) or not isinstance(data.get("matches"), list):
            return
        selected = []
        omitted = False
        for match in data["matches"]:
            if not isinstance(match, dict) or not isinstance(match.get("text"), str):
                continue
            key = (match.get("path"), match.get("line"), match.get("column"), match["text"])
            if key in seen:
                omitted = True
                continue
            if len(seen) == _MAX_RG_MATCHES:
                omitted = True
                continue
            seen.add(key)
            selected_match = dict(match)
            selected_match["text"] = cls._bound_text(
                match["text"],
                _MAX_RG_MATCH_TEXT_CHARS,
                "…[match text omitted]…",
            )
            selected.append(selected_match)
        data["matches"] = selected
        data["count"] = len(selected)
        data["truncated"] = bool(data.get("truncated")) or omitted

    @classmethod
    def _control_failure_output(cls, content: dict[str, object]) -> None:
        data = content.get("data")
        if isinstance(data, dict) and isinstance(data.get("output"), str):
            data["output"] = cls._bound_text(
                data["output"],
                _MAX_EXECUTION_OUTPUT_CHARS,
                _CONTENT_OMISSION_MARKER,
            )

    @staticmethod
    def _bound_text(value: str, limit: int, marker: str) -> str:
        if len(value) <= limit:
            return value
        available = limit - len(marker)
        head = (available + 1) // 2
        tail = available - head
        return value[:head] + marker + value[-tail:] if tail else value[:head] + marker

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
