"""Task-scoped execution state for the ArkUI UT agent control plane."""

import hashlib
import json
from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    SerializerFunctionWrapHandler,
    StringConstraints,
    TypeAdapter,
    field_validator,
    model_serializer,
    model_validator,
)

from arkui_ut_agent.agents.control import ControlDecision
from arkui_ut_agent.agents.planning import Plan
from arkui_ut_agent.tools.contracts import Provenance

NonEmptyString = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
NonNegativeInt = Annotated[int, Field(strict=True, ge=0)]
_TEXT_ADAPTER = TypeAdapter(NonEmptyString)
_METADATA_ADAPTER = TypeAdapter(dict[str, JsonValue], config=ConfigDict(allow_inf_nan=False))


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


class StopReason(str, Enum):
    """Terminal reasons required by the agent runtime contract."""

    SUCCESS = "success"
    STEP_LIMIT = "step_limit"
    COST_LIMIT = "cost_limit"
    REPEATED_FAILURE = "repeated_failure"
    NO_NEW_EVIDENCE = "no_new_evidence"
    TOOL_UNAVAILABLE = "tool_unavailable"
    IRRECOVERABLE_FAILURE = "irrecoverable_failure"


class TaskMemory(BaseModel):
    """Caller-confirmed structured information for the current task only.

    Callers must confirm engineering facts before supplying them here. Validation
    checks structure, not truth; no hypotheses, messages or tool outputs are
    automatically promoted to task facts. Evidence linkage belongs to a later slice.

    Collections are ordered labels/paths/summaries, not repository indexes. Completed
    steps and failed attempts are summaries, not PlanStep IDs or workflow records;
    important decisions are explicitly made task choices, not inferred conclusions.
    Updates replace whole fields using ``updated()``; they do not merge or deduplicate.
    As with AgentState, raw in-place mutations are unsupported and dumps revalidate
    the full snapshot before conversion.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, revalidate_instances="always")

    target_component: NonEmptyString | None = None
    target_files: list[NonEmptyString] = Field(default_factory=list)
    target_classes: list[NonEmptyString] = Field(default_factory=list)
    target_functions: list[NonEmptyString] = Field(default_factory=list)
    changed_files: list[NonEmptyString] = Field(default_factory=list)
    relevant_tests: list[NonEmptyString] = Field(default_factory=list)
    fixtures: list[NonEmptyString] = Field(default_factory=list)
    mocks: list[NonEmptyString] = Field(default_factory=list)
    build_target: NonEmptyString | None = None
    completed_steps: list[NonEmptyString] = Field(default_factory=list)
    failed_attempts: list[NonEmptyString] = Field(default_factory=list)
    important_decisions: list[NonEmptyString] = Field(default_factory=list)

    def updated(self, **changes: Any) -> "TaskMemory":
        """Return an isolated snapshot after validating all existing and changed fields."""
        return type(self).model_validate({**dict(self), **changes})

    @model_serializer(mode="wrap")
    def _serialize_validated_memory(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        """Reject invalid raw/container mutations before Pydantic converts any values."""
        validated = type(self).model_validate(dict(self))
        return handler(validated)


class Evidence(BaseModel):
    """An explicitly supplied Tool-derived fact, not a model hypothesis.

    Provenance must come from the producing Tool; validation checks completeness
    and JSON compatibility, not authenticity or factual truth. Unknown locations
    must be explicitly represented as None. step_id is a caller-provided trace label.
    Content retains meaningful snippet whitespace; summary/source labels are trimmed.
    Only the execution boundary maps Tool Observations; model text is never promoted.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, revalidate_instances="always", allow_inf_nan=False)

    source: NonEmptyString
    location: NonEmptyString | None
    content: JsonValue = None
    summary: NonEmptyString | None = None
    provenance: list[Provenance] = Field(min_length=1)
    step_id: NonEmptyString

    @field_validator("provenance")
    @classmethod
    def _validate_provenance(cls, value: list[Provenance]) -> list[Provenance]:
        # Stage 1 Provenance is mutable and allows Any metadata. Rebuild its existing
        # schema from raw fields so mutated instances cannot bypass Evidence validation.
        result = []
        for item in value:
            origin = Provenance.model_validate(dict(item))
            result.append(Provenance(
                source=_TEXT_ADAPTER.validate_python(origin.source),
                location=None if origin.location is None else _TEXT_ADAPTER.validate_python(origin.location),
                metadata=_METADATA_ADAPTER.validate_python(origin.metadata),
            ))
        return result

    @field_validator("content")
    @classmethod
    def _validate_content(cls, value: JsonValue) -> JsonValue:
        if (isinstance(value, str) and not value.strip()) or value == [] or value == {}:
            raise ValueError("Evidence content must be nonempty when provided.")
        return value

    @model_validator(mode="after")
    def _require_fact_payload(self) -> "Evidence":
        if self.content is None and self.summary is None:
            raise ValueError("Evidence requires content or summary.")
        return self

    @property
    def identity(self) -> str:
        """SHA-256 of all persisted fields except step_id; no cached/stored identity.

        Mapping keys and provenance ordering are canonicalized. Content array order
        remains significant; provenance multiplicity is retained. A repeated fact
        from a different step has the same identity, without occurrence history.
        """
        validated = type(self).model_validate(dict(self))
        payload = validated.model_dump(mode="json", exclude={"step_id"})
        payload["provenance"] = sorted(payload["provenance"], key=_canonical_json)
        return "sha256:" + hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()

    def updated(self, **changes: Any) -> "Evidence":
        """Return a fully validated isolated replacement snapshot."""
        return type(self).model_validate({**dict(self), **changes})

    @model_serializer(mode="wrap")
    def _serialize_validated_evidence(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        validated = type(self).model_validate(dict(self))
        return handler(validated)


class EvidenceMemory(BaseModel):
    """Task-local ordered facts, deduplicated by identity with first-record wins.

    Construction, replacement updates and restoration share the same dedup rule.
    add() returns (new isolated memory, inserted); duplicates retain the first full
    record including step_id. No global store, fuzzy merge or occurrence log exists.
    Raw container mutation/model_copy/model_construct are unsupported; all supported
    updates and dumps revalidate every record before deduplicating or serializing.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, revalidate_instances="always")

    records: list[Evidence] = Field(default_factory=list)

    @model_validator(mode="after")
    def _deduplicate(self) -> "EvidenceMemory":
        records = []
        seen = set()
        for record in self.records:
            identity = record.identity
            if identity not in seen:
                seen.add(identity)
                records.append(record)
        object.__setattr__(self, "records", records)
        return self

    def updated(self, **changes: Any) -> "EvidenceMemory":
        """Replace complete fields after full validation and first-wins dedup."""
        return type(self).model_validate({**dict(self), **changes})

    def add(self, record: Evidence | dict[str, Any]) -> tuple["EvidenceMemory", bool]:
        """Explicitly add a contract record; always return a new isolated snapshot."""
        memory = self.updated()
        candidate = Evidence.model_validate(record)
        inserted = candidate.identity not in {item.identity for item in memory.records}
        return memory.updated(records=[*memory.records, candidate]), inserted

    @model_serializer(mode="wrap")
    def _serialize_validated_memory(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        validated = type(self).model_validate(dict(self))
        return handler(validated)


class MemoryUpdateEvent(BaseModel):
    """An already-applied task-local update, not a replay command or event bus.

    Regions name existing state areas. Identities refer to facts related to that
    update; empty is valid for Working/Task Memory updates. Immutable tuples avoid
    a second mutable-state interface. No timestamps, random IDs or cross-task log.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, revalidate_instances="always")

    step_id: NonEmptyString
    regions: tuple[Literal["working_memory", "task_memory", "evidence_memory"], ...] = Field(min_length=1)
    evidence_identities: tuple[Annotated[str, StringConstraints(pattern=r"^sha256:[0-9a-f]{64}$")], ...] = ()

    @model_serializer(mode="wrap")
    def _serialize_validated_event(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        return handler(type(self).model_validate(dict(self)))


class AgentState(BaseModel):
    """Minimal, serializable state for one task execution.

    ``current_plan`` is the project-owned planning snapshot. ``current_step`` is a
    runtime producing-step label (for example ``step-1``), not a PlanStep id; Tool
    executions, Evidence and MemoryUpdateEvent continue to share that runtime label.
    ``current_decision`` is a control intention and model rationale, not confirmed
    Evidence. Conversation messages never belong to this state; evidence_memory
    holds explicit Tool facts.

    The existing execution fields are the sole Working Memory contract; task_memory
    separately holds caller-confirmed task information. No duplicate WorkingMemory
    model or automatic speculation-to-fact update exists.

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
    current_plan: Plan | None = None
    current_step: NonEmptyString | None = None
    current_decision: ControlDecision | None = None
    information_gap: NonEmptyString | None = None
    next_action: NonEmptyString | None = None
    open_questions: list[NonEmptyString] = Field(default_factory=list)
    hypotheses: list[NonEmptyString] = Field(default_factory=list)
    blocking_issue: NonEmptyString | None = None
    stop_reason: StopReason | None = None
    retry_count: NonNegativeInt = 0
    task_memory: TaskMemory = Field(default_factory=TaskMemory)
    evidence_memory: EvidenceMemory = Field(default_factory=EvidenceMemory)

    def updated(self, **changes: Any) -> "AgentState":
        """Return an isolated snapshot after validating all existing and changed fields."""
        return type(self).model_validate({**dict(self), **changes})

    @classmethod
    def from_trajectory(cls, trajectory: dict[str, Any]) -> "AgentState | None":
        """Restore only the state snapshot; never replay or infer from messages.

        Legacy/pre-run trajectories without a snapshot return None. Present but
        malformed snapshots fail validation. This is not execution/model resumption.
        """
        snapshot = trajectory.get("agent_state")
        return None if snapshot is None else cls.model_validate(snapshot)

    @model_serializer(mode="wrap")
    def _serialize_validated_state(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        """Reject invalid raw/container mutations before Pydantic converts any values."""
        validated = type(self).model_validate(dict(self))
        return handler(validated)


__all__ = ["AgentState", "Evidence", "EvidenceMemory", "MemoryUpdateEvent", "StopReason", "TaskMemory"]
