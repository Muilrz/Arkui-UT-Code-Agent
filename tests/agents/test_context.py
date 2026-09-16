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


def _evidence(summary: str, *, step_id: str = "step-1") -> Evidence:
    return Evidence(
        source="read_file",
        location="src/text.cc:1",
        content={"snippet": "TextPattern::OnModifyDone " + "implementation " * 20},
        summary=summary,
        provenance=[Provenance(source="read_file", location="src/text.cc:1")],
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
    assert contents["evidence_memory"] == state.evidence_memory.model_dump(mode="json")
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


def test_total_budget_is_hard_and_later_sections_are_truncated_first():
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
