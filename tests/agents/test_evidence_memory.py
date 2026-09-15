import hashlib
import json
import os
import subprocess
import sys

import pytest
from pydantic import ValidationError
from pydantic_core import PydanticSerializationError

from arkui_ut_agent.agents import AgentState, Evidence, EvidenceMemory, TaskMemory
from arkui_ut_agent.tools import Observation, Provenance, ToolResult, normalize_tool_result


@pytest.fixture
def minimal_payload():
    return {
        "source": "read_file",
        "location": "src/text.cc:1",
        "summary": "Read Text implementation",
        "provenance": [{"source": "read_file", "location": "src/text.cc:1"}],
        "step_id": "inspect-1",
    }


@pytest.fixture
def full_evidence(minimal_payload):
    return Evidence(**{
        **minimal_payload,
        "content": {"values": [1, 2], "snippet": "  TextPattern implementation\n"},
        "provenance": [Provenance(source="read_file", location="src/text.cc:1", metadata={"revision": "abc123"})],
    })


def test_minimal_evidence_and_unknown_location_are_explicit(minimal_payload):
    evidence = Evidence(**minimal_payload)
    assert evidence.content is None
    assert evidence.summary == minimal_payload["summary"]
    assert evidence.step_id == "inspect-1"
    assert Evidence(**{**minimal_payload, "location": None}).location is None


def test_full_evidence_reuses_stage_one_provenance_without_loss(full_evidence):
    assert type(full_evidence.provenance[0]) is Provenance
    assert full_evidence.provenance[0].metadata == {"revision": "abc123"}
    assert full_evidence.model_dump(mode="json")["content"]["snippet"].startswith("  ")
    restored = Evidence.model_validate_json(full_evidence.model_dump_json())
    assert restored == full_evidence
    assert type(restored.provenance[0]) is Provenance


def test_explicit_caller_mapping_preserves_tool_provenance(minimal_payload):
    observation = normalize_tool_result("read_file", ToolResult(
        success=True, data={"snippet": "implementation"}, summary="read current source",
        provenance=[Provenance(source="read_file", location="src/text.cc:1", metadata={"revision": "abc123"})],
    ))
    # The caller explicitly chooses source/location/content/step. No arbitrary mapping API exists.
    evidence = Evidence(**{
        **minimal_payload, "content": observation.data,
        "summary": observation.summary, "provenance": observation.provenance,
    })
    assert evidence.provenance == observation.provenance
    assert evidence.provenance is not observation.provenance
    observation.provenance[0].metadata["revision"] = "external change"
    assert evidence.provenance[0].metadata["revision"] == "abc123"


@pytest.mark.parametrize("field", ["source", "location", "provenance", "step_id"])
def test_required_fields_cannot_be_omitted(minimal_payload, field):
    payload = {key: value for key, value in minimal_payload.items() if key != field}
    with pytest.raises(ValidationError):
        Evidence.model_validate(payload)


@pytest.mark.parametrize("content", ["source snippet", {"value": 0}, [0], 0, False])
def test_nonempty_content_can_replace_summary(minimal_payload, content):
    evidence = Evidence(**{**minimal_payload, "content": content, "summary": None})
    assert evidence.content == content


@pytest.mark.parametrize("content", ["", "   ", [], {}, object(), float("nan"), {"value": float("inf")}])
def test_invalid_content_is_rejected_even_if_summary_exists(minimal_payload, content):
    with pytest.raises(ValidationError):
        Evidence(**{**minimal_payload, "content": content})


@pytest.mark.parametrize("changes", [
    {"summary": None}, {"summary": ""}, {"source": " "}, {"location": ""},
    {"step_id": ""}, {"step_id": 1}, {"provenance": []},
    {"provenance": [{"source": " "}]},
    {"provenance": [{"source": "read_file", "metadata": {"object": object()}}]},
    {"provenance": [{"source": "read_file", "metadata": {"number": float("nan")}}]},
    {"provenance": [{"source": "read_file", "metadata": {1: "non-string key"}}]},
])
def test_invalid_evidence_and_updates_fail_without_mutating_original(minimal_payload, changes):
    original = Evidence(**minimal_payload)
    with pytest.raises(ValidationError):
        Evidence(**{**minimal_payload, **changes})
    with pytest.raises(ValidationError):
        original.updated(**changes)
    assert original == Evidence(**minimal_payload)


@pytest.mark.parametrize("field", ["identity", "messages", "hypotheses", "kind", "unexpected"])
def test_evidence_rejects_unknown_fields(minimal_payload, field):
    with pytest.raises(ValidationError):
        Evidence(**{**minimal_payload, field: "unknown"})


def test_mutated_provenance_instances_are_validated_even_in_tuple_input(minimal_payload):
    origin = Provenance(source="read_file")
    origin.metadata["bad"] = object()
    with pytest.raises(ValidationError):
        Evidence(**{**minimal_payload, "provenance": (origin,)})


def test_identity_uses_fixed_canonical_fields_and_standard_digest(minimal_payload):
    canonical = (
        '{"content":null,"location":"src/text.cc:1","provenance":'
        '[{"location":"src/text.cc:1","metadata":{},"source":"read_file"}],'
        '"source":"read_file","summary":"Read Text implementation"}'
    )
    expected = "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    assert Evidence(**minimal_payload).identity == expected
    assert Evidence.model_validate_json(json.dumps(minimal_payload)).identity == expected


def test_identity_is_process_and_python_hash_seed_independent(minimal_payload):
    script = (
        "import sys; from arkui_ut_agent.agents import Evidence; "
        "print(Evidence.model_validate_json(sys.argv[1]).identity)"
    )
    identities = []
    for seed in ("1", "42"):
        result = subprocess.run(
            [sys.executable, "-c", script, json.dumps(minimal_payload)],
            env={**os.environ, "PYTHONHASHSEED": seed, "MSWEA_SILENT_STARTUP": "1"},
            capture_output=True, text=True, check=True, timeout=15,
        )
        identities.append(result.stdout.strip())
    assert identities == [Evidence(**minimal_payload).identity] * 2


@pytest.mark.parametrize("changes", [
    {"source": "rg_search"}, {"location": "src/other.cc:2"},
    {"content": "new engineering fact"}, {"summary": "changed summary"},
    {"provenance": [{"source": "read_file", "metadata": {"revision": "changed"}}]},
    {"provenance": [{"source": "rg_search", "location": "src/text.cc:1"}]},
    {"provenance": [{"source": "read_file", "location": "src/text.cc:2"}]},
])
def test_relevant_persisted_fact_changes_change_identity(minimal_payload, changes):
    original = Evidence(**minimal_payload)
    assert original.updated(**changes).identity != original.identity


def test_mapping_and_provenance_order_do_not_change_identity(full_evidence):
    first = full_evidence.updated(
        content={"a": 1, "b": [2, 3]},
        provenance=[Provenance(source="rg_search"), Provenance(source="read_file", metadata={"a": 1, "b": 2})],
    )
    second = first.updated(
        content={"b": [2, 3], "a": 1},
        provenance=[Provenance(source="read_file", metadata={"b": 2, "a": 1}), Provenance(source="rg_search")],
    )
    assert first.identity == second.identity
    assert first.updated(content={"a": 1, "b": [3, 2]}).identity != first.identity
    assert first.updated(provenance=first.provenance * 2).identity != first.identity


def test_identity_stays_stable_across_serialization_and_step_changes(full_evidence):
    assert Evidence.model_validate(full_evidence.model_dump(mode="json")).identity == full_evidence.identity
    assert Evidence.model_validate_json(full_evidence.model_dump_json()).identity == full_evidence.identity
    assert full_evidence.updated(step_id="another-step").identity == full_evidence.identity


def test_memory_add_reports_duplicate_and_preserves_first_record(full_evidence):
    empty = EvidenceMemory()
    first, inserted = empty.add(full_evidence)
    duplicate, inserted_again = first.add(full_evidence.updated(step_id="repeat-step"))

    assert inserted is True
    assert inserted_again is False
    assert empty.records == []
    assert len(first.records) == len(duplicate.records) == 1
    assert duplicate.records[0].step_id == full_evidence.step_id
    assert duplicate is not first
    assert duplicate.records[0] is not first.records[0]


def test_distinct_add_and_initialization_update_restore_all_keep_first_insertion_order(full_evidence):
    other = full_evidence.updated(summary="different engineering fact")
    initialized = EvidenceMemory(records=[full_evidence, other, full_evidence.updated(step_id="repeat")])
    assert initialized.records == [full_evidence, other]
    restored = EvidenceMemory.model_validate_json(initialized.model_dump_json())
    assert restored.records == initialized.records
    updated = initialized.updated(records=[other, full_evidence, other.updated(step_id="repeat")])
    assert updated.records == [other, full_evidence]
    added, inserted = EvidenceMemory().add(full_evidence.model_dump(mode="json"))
    added, distinct_inserted = added.add(other)
    assert inserted is distinct_inserted is True
    assert added.records == [full_evidence, other]


def test_memory_and_agent_snapshots_do_not_share_mutable_inputs(full_evidence):
    first = EvidenceMemory()
    second = EvidenceMemory()
    supplied = [full_evidence]
    memory = EvidenceMemory(records=supplied)
    supplied.clear()
    assert len(memory.records) == 1
    first.records.append(full_evidence)  # Raw mutation used only to test ownership.
    assert second.records == []

    state = AgentState(goal="task", evidence_memory=memory)
    copied = state.updated(next_action="verify")
    full_evidence.content["values"].append(3)
    memory.records[0].provenance[0].metadata["revision"] = "external change"
    copied.evidence_memory.records[0].content["values"].append(4)
    assert state.evidence_memory.records[0].content["values"] == [1, 2]
    assert state.evidence_memory.records[0].provenance[0].metadata["revision"] == "abc123"


def test_deep_content_and_provenance_metadata_are_isolated_on_construction_and_update(minimal_payload):
    content = {"nested": [{"values": [1]}]}
    origin = Provenance(source="read_file", metadata={"nested": {"values": [1]}})
    evidence = Evidence(**{**minimal_payload, "content": content, "provenance": [origin]})
    content["nested"][0]["values"].append(2)
    origin.metadata["nested"]["values"].append(2)
    copied = evidence.updated()
    copied.content["nested"][0]["values"].append(3)
    copied.provenance[0].metadata["nested"]["values"].append(3)

    assert evidence.content == {"nested": [{"values": [1]}]}
    assert evidence.provenance[0].metadata == {"nested": {"values": [1]}}


def test_unicode_content_and_metadata_identity_round_trip(minimal_payload):
    evidence = Evidence(**{
        **minimal_payload, "content": {"说明": "读取实现"},
        "provenance": [{"source": "read_file", "metadata": {"说明": "当前源码"}}],
    })
    assert Evidence.model_validate_json(evidence.model_dump_json()).identity == evidence.identity


def test_agent_explicit_update_round_trip_and_legacy_payloads(full_evidence):
    original = AgentState(goal="task", hypotheses=["Unconfirmed guess"], task_memory=TaskMemory(target_component="Text"))
    memory, inserted = original.evidence_memory.add(full_evidence)
    updated = original.updated(evidence_memory=memory)
    assert inserted is True
    assert original.evidence_memory.records == []
    assert updated.task_memory == original.task_memory
    assert updated.task_memory is not original.task_memory
    assert updated.hypotheses is not original.hypotheses
    assert updated.hypotheses == original.hypotheses
    payload = updated.model_dump(mode="json")
    assert "messages" not in payload
    assert "identity" not in payload["evidence_memory"]["records"][0]
    assert AgentState.model_validate(payload) == updated
    assert AgentState.model_validate_json(updated.model_dump_json()) == updated
    assert AgentState.model_validate(payload).evidence_memory.records[0].identity == full_evidence.identity
    for legacy in ({"goal": "legacy-2A"}, {"goal": "legacy-2B", "task_memory": {"target_component": "Text"}}):
        assert AgentState.model_validate(legacy).evidence_memory == EvidenceMemory()
        assert AgentState.model_validate_json(json.dumps(legacy)).evidence_memory == EvidenceMemory()


def test_model_text_hypotheses_messages_and_observations_are_not_auto_promoted(minimal_payload):
    state = AgentState(goal="task").updated(hypotheses=["The model claims it passed"])
    assert state.evidence_memory == EvidenceMemory()
    observation = Observation(tool_name="read_file", success=True, data="fact", provenance=[Provenance(source="read_file")])
    for unsupported in ("model text", {"role": "assistant", "content": "claim"}, observation.model_dump(mode="json")):
        with pytest.raises(ValidationError):
            state.evidence_memory.add(unsupported)
    missing_origin = {key: value for key, value in minimal_payload.items() if key != "provenance"}
    with pytest.raises(ValidationError):
        state.evidence_memory.add(missing_origin)
    with pytest.raises(ValidationError):
        state.updated(messages=[])


@pytest.mark.parametrize("payload", [{"unknown": []}, {"records": ["model text"]}, {"records": None}])
def test_invalid_memory_and_agent_updates_fail_deterministically(payload):
    original = AgentState(goal="task")
    with pytest.raises(ValidationError):
        EvidenceMemory.model_validate(payload)
    with pytest.raises(ValidationError):
        original.updated(evidence_memory=payload)
    assert original.evidence_memory == EvidenceMemory()
    with pytest.raises(ValidationError):
        original.updated(evidence_memory=None)


@pytest.mark.parametrize("mutation", ["content", "metadata", "source", "location", "provenance", "records"])
def test_nested_raw_mutation_is_rejected_before_update_identity_dedup_or_serialization(full_evidence, mutation):
    state = AgentState(goal="task", evidence_memory=EvidenceMemory(records=[full_evidence]))
    record = state.evidence_memory.records[0]
    if mutation == "content":
        record.content["values"].append(object())
    elif mutation == "metadata":
        record.provenance[0].metadata["invalid"] = object()
    elif mutation == "source":
        record.provenance[0].source = ""
    elif mutation == "location":
        record.provenance[0].location = 7
    elif mutation == "provenance":
        record.provenance.clear()
    else:
        state.evidence_memory.records.append("model text")

    if mutation != "records":
        with pytest.raises(ValidationError):
            record.identity
        with pytest.raises(ValidationError):
            record.updated()
        with pytest.raises(PydanticSerializationError, match="ValidationError"):
            record.model_dump(mode="json", exclude={"content", "provenance"})
    with pytest.raises(ValidationError):
        state.evidence_memory.add(full_evidence)
    with pytest.raises(ValidationError):
        state.evidence_memory.updated()
    with pytest.raises(ValidationError):
        state.updated(next_action="verify")
    with pytest.raises(ValidationError):
        AgentState.model_validate(state)
    with pytest.raises(PydanticSerializationError, match="ValidationError"):
        state.evidence_memory.model_dump_json()
    with pytest.raises(PydanticSerializationError, match="ValidationError"):
        state.model_dump(mode="json", exclude={"evidence_memory"})
    with pytest.raises(PydanticSerializationError, match="ValidationError"):
        state.model_dump_json()


def test_invalid_duplicate_is_validated_before_dedup_and_identity_is_not_cached(full_evidence):
    unsafe_duplicate = full_evidence.model_copy(update={"step_id": ""})
    with pytest.raises(ValidationError):
        EvidenceMemory(records=[full_evidence, unsafe_duplicate])
    before = full_evidence.identity
    full_evidence.content["values"].append(3)  # Valid but unsupported mutation; no stale identity cache.
    assert full_evidence.identity != before


def test_top_level_field_assignment_is_not_supported(full_evidence):
    with pytest.raises(ValidationError):
        full_evidence.step_id = "new"
    memory = EvidenceMemory()
    with pytest.raises(ValidationError):
        memory.records = [full_evidence]
