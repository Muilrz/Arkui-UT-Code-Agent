import json

import pytest
from pydantic import ValidationError

from arkui_ut_agent.agents import (
    CONTEXT_SECTION_ORDER,
    MIN_CONTEXT_CHARS,
    AgentState,
    ContextBudget,
    ContextBuilder,
    Evidence,
    EvidenceMemory,
    TaskMemory,
)
from arkui_ut_agent.tools import Provenance


def _evidence(
    summary: str,
    *,
    step_id: str = "step-1",
    source: str = "read_file",
    location: str = "src/text.cc:1",
    snippet: str = "TextPattern::OnModifyDone " + "implementation " * 20,
) -> Evidence:
    return Evidence(
        source=source,
        location=location,
        content={"snippet": snippet},
        summary=summary,
        provenance=[Provenance(source=source, location=location)],
        step_id=step_id,
    )


def _full_state() -> AgentState:
    return AgentState(
        goal="Add an ArkUI Text unit test",
        current_plan={"revision": 2, "steps": ["inspect", "verify"]},
        current_step={"index": 0, "label": "inspect"},
        information_gap="Fixture is unknown",
        next_action="Read the existing tests",
        open_questions=["Which fixture owns the mock?"],
        hypotheses=["A fixture may already exist"],
        retry_count=1,
        task_memory=TaskMemory(
            target_component="Text",
            target_files=["src/text.cc"],
            relevant_tests=["TextTest.CoversProperty"],
            important_decisions=["Reuse the existing fixture"],
        ),
        evidence_memory=EvidenceMemory(records=[_evidence("Read the current implementation")]),
    )


def test_builder_uses_existing_state_and_memory_contract_in_fixed_section_order():
    state = _full_state()

    context = ContextBuilder().build(state)

    assert tuple(section.name for section in context.sections) == CONTEXT_SECTION_ORDER
    assert all(section.truncated is False for section in context.sections)
    contents = {section.name: json.loads(section.content) for section in context.sections}
    assert contents["user_task"] == state.goal
    assert contents["current_plan"] == state.current_plan
    assert contents["working_memory"]["current_step"] == state.current_step
    assert contents["working_memory"]["next_action"] == state.next_action
    assert contents["task_memory"] == state.task_memory.model_dump(mode="json")
    expected_evidence = state.evidence_memory.records[0].model_dump(mode="json")
    assert contents["evidence_memory"] == {
        "records": [{"identity": state.evidence_memory.records[0].identity, **expected_evidence}]
    }
    assert context.text.index("## user_task") < context.text.index("## current_plan")
    assert context.text.index("## current_plan") < context.text.index("## working_memory")
    assert context.text.index("## working_memory") < context.text.index("## task_memory")
    assert context.text.index("## task_memory") < context.text.index("## evidence_memory")


def test_builder_is_deterministic_and_does_not_interpret_opaque_plan_or_step():
    state = _full_state().updated(
        current_plan={"z": [3, 2, 1], "a": {"opaque": True}},
        current_step=["also", {"opaque": 1}],
    )
    builder = ContextBuilder()

    first = builder.build(state)
    second = builder.build(AgentState.model_validate_json(state.model_dump_json()))

    assert first == second
    assert first.text == second.text
    assert json.loads(first.sections[1].content) == state.current_plan
    assert json.loads(first.sections[2].content)["current_step"] == state.current_step


def test_total_budget_is_hard_and_task_memory_is_truncated_before_prioritized_evidence():
    state = _full_state().updated(
        task_memory=_full_state().task_memory.updated(target_files=[f"src/file_{index}.cc" for index in range(40)]),
        evidence_memory=EvidenceMemory(
            records=[_evidence(f"Evidence {index}", step_id=f"step-{index}") for index in range(20)]
        ),
    )
    limit = MIN_CONTEXT_CHARS + 180

    context = ContextBuilder(ContextBudget(max_chars=limit)).build(state)

    assert context.char_count == limit
    assert len(context.text) <= limit
    assert tuple(section.name for section in context.sections) == CONTEXT_SECTION_ORDER
    assert context.sections[0].truncated is False
    assert context.sections[3].truncated is True
    assert context.sections[-1].truncated is True
    assert context.sections[-1].content.endswith("…")


def test_minimum_budget_keeps_all_headers_and_marks_every_section_truncated():
    context = ContextBuilder(ContextBudget(max_chars=MIN_CONTEXT_CHARS)).build(_full_state())

    assert context.char_count == MIN_CONTEXT_CHARS
    assert [section.content for section in context.sections] == ["…"] * len(CONTEXT_SECTION_ORDER)
    assert all(section.truncated for section in context.sections)
    assert tuple(section.name for section in context.sections) == CONTEXT_SECTION_ORDER


@pytest.mark.parametrize("max_chars", [MIN_CONTEXT_CHARS - 1, 0, -1, True, "16000"])
def test_invalid_budget_is_rejected_deterministically(max_chars):
    with pytest.raises(ValidationError):
        ContextBudget(max_chars=max_chars)


def test_builder_revalidates_mutated_stage_two_state_before_rendering():
    state = _full_state()
    state.task_memory.target_files.append("")  # Deliberately bypass the supported update API.

    with pytest.raises(ValidationError):
        ContextBuilder().build(state)


def test_task_memory_selects_focus_matches_and_keeps_confirmed_scalar_anchors():
    state = AgentState(
        goal="Repair TextPattern behavior",
        current_step="step-3",
        next_action="Run TextTest with MockPipelineContext",
        task_memory=TaskMemory(
            target_component="Text",
            build_target="text_test",
            target_files=["src/text.cc", "src/menu.cc"],
            target_classes=["TextPattern", "MenuPattern"],
            relevant_tests=["TextTest.Property", "MenuTest.Layout"],
            fixtures=["TextTest", "MenuTest"],
            mocks=["MockPipelineContext", "MockMenuTheme"],
            completed_steps=["Read Menu documentation"],
            failed_attempts=["TextTest compile failed", "Network unavailable"],
            important_decisions=["Use MockPipelineContext for TextTest", "Keep the Menu fixture"],
        ),
    )

    context = ContextBuilder().build(state)
    selected = json.loads(context.sections[3].content)

    assert selected["target_component"] == "Text"
    assert selected["build_target"] == "text_test"
    assert selected["target_files"] == ["src/text.cc"]
    assert selected["target_classes"] == ["TextPattern"]
    assert selected["relevant_tests"] == ["TextTest.Property"]
    assert selected["fixtures"] == ["TextTest"]
    assert selected["mocks"] == ["MockPipelineContext"]
    assert selected["completed_steps"] == []
    assert selected["failed_attempts"] == ["TextTest compile failed"]
    assert selected["important_decisions"] == ["Use MockPipelineContext for TextTest"]


def test_evidence_priority_preserves_identity_step_and_provenance():
    irrelevant = _evidence(
        "Menu history",
        step_id="step-old",
        location="src/menu.cc:1",
        snippet="MenuPattern layout implementation",
    )
    build_failure = Evidence(
        source="build",
        location=".",
        content={
            "success": False,
            "data": {"output": "compiler error"},
            "diagnostics": [{"code": "command_failed", "message": "build failed"}],
        },
        summary="Build failed",
        provenance=[Provenance(source="build", location=".", metadata={"command": "ninja text_test"})],
        step_id="step-build",
    )
    current = _evidence("Current Text evidence", step_id="step-current")
    state = AgentState(
        goal="Repair TextPattern",
        current_step="step-current",
        evidence_memory=EvidenceMemory(records=[irrelevant, build_failure, current]),
    )

    selected = json.loads(ContextBuilder().build(state).sections[4].content)["records"]

    assert [record["identity"] for record in selected] == [current.identity, build_failure.identity]
    assert selected[0]["step_id"] == "step-current"
    assert selected[0]["provenance"] == current.model_dump(mode="json")["provenance"]
    assert selected[1]["source"] == "build"
    assert selected[1]["content"]["diagnostics"][0]["code"] == "command_failed"
    assert all(record["identity"] != irrelevant.identity for record in selected)


def test_opaque_non_string_current_step_is_not_interpreted_as_step_identity():
    nested_step = _evidence(
        "Unrelated old fact",
        step_id="step-nested",
        location="src/menu.cc:1",
        snippet="Menu layout",
    )
    latest = _evidence(
        "Latest fallback",
        step_id="step-latest",
        location="src/button.cc:1",
        snippet="Button layout",
    )
    state = AgentState(
        goal="Repair TextPattern",
        current_step={"id": "step-nested"},
        evidence_memory=EvidenceMemory(records=[nested_step, latest]),
    )

    selected = json.loads(ContextBuilder().build(state).sections[4].content)["records"]

    assert [record["identity"] for record in selected] == [latest.identity]


def test_priority_evidence_is_not_displaced_by_ordinary_history_under_budget():
    priority = Evidence(
        source="test",
        location="tests/text_test.cc",
        content={
            "success": False,
            "data": {"output": "unique-current-assertion-failure"},
            "diagnostics": [{"code": "command_failed", "message": "unique current test failure"}],
        },
        summary="Current TextTest failure",
        provenance=[
            Provenance(
                source="test",
                location="tests/text_test.cc",
                metadata={"command": "run unique_text_test"},
            )
        ],
        step_id="step-current",
    )
    base_state = AgentState(
        goal="Repair TextTest",
        current_step="step-current",
        next_action="Inspect the current failure",
        evidence_memory=EvidenceMemory(records=[priority]),
    )
    required = ContextBuilder().build(base_state).char_count
    history = [
        _evidence(
            f"Text history {index}",
            step_id=f"step-old-{index}",
            snippet=f"Text historical evidence {index} " + "ordinary " * 40,
        )
        for index in range(30)
    ]
    crowded = base_state.updated(evidence_memory=EvidenceMemory(records=[*history, priority]))

    context = ContextBuilder(ContextBudget(max_chars=required + 100)).build(crowded)
    evidence_section = context.sections[4]

    assert context.char_count <= context.budget.max_chars
    assert evidence_section.truncated is True
    assert f'"identity":"{priority.identity}"' in evidence_section.content
    assert '"step_id":"step-current"' in evidence_section.content
    assert '"command":"run unique_text_test"' in evidence_section.content
    assert "unique-current-assertion-failure" in evidence_section.content
    assert f'"identity":"{history[0].identity}"' not in evidence_section.content
