from typing import Any

import pytest

from arkui_ut_agent.tools import Diagnostic, Provenance, ToolRegistry, ToolResult


def test_dispatch_normalizes_successful_tool_result():
    received: list[dict[str, Any]] = []

    def handler(arguments):
        received.append(dict(arguments))
        return ToolResult(
            success=True,
            data={"answer": 42},
            summary="Found an answer.",
            provenance=[Provenance(source="fixture", location="answer.txt:1")],
        )

    registry = ToolRegistry()
    registry.register("answer", handler)

    observation = registry.dispatch("answer", {"question": "meaning"})

    assert received == [{"question": "meaning"}]
    assert observation.tool_name == "answer"
    assert observation.success is True
    assert observation.data == {"answer": 42}
    assert observation.summary == "Found an answer."
    assert observation.provenance == [Provenance(source="fixture", location="answer.txt:1")]


def test_dispatch_normalizes_explicit_tool_failure():
    diagnostic = Diagnostic(code="not_found", message="No matching item.")
    provenance = Provenance(source="fixture", location="empty.txt")
    registry = ToolRegistry()
    registry.register(
        "lookup",
        lambda _arguments: ToolResult(
            success=False,
            summary="Lookup failed.",
            diagnostics=[diagnostic],
            provenance=[provenance],
        ),
    )

    observation = registry.dispatch("lookup")

    assert observation.success is False
    assert observation.summary == "Lookup failed."
    assert observation.diagnostics == [diagnostic]
    assert observation.provenance == [provenance]


def test_register_rejects_duplicate_tool_name():
    registry = ToolRegistry()
    registry.register("lookup", lambda _arguments: ToolResult(success=True))

    with pytest.raises(ValueError, match="already registered"):
        registry.register("lookup", lambda _arguments: ToolResult(success=True))


def test_dispatch_unknown_tool_returns_standard_failure():
    observation = ToolRegistry().dispatch("missing", {"path": "file.cc"})

    assert observation.tool_name == "missing"
    assert observation.success is False
    assert observation.data is None
    assert observation.diagnostics[0].code == "unknown_tool"
    assert observation.provenance == [
        Provenance(source="tool_registry", metadata={"requested_tool": "missing"})
    ]


def test_dispatch_standardizes_tool_execution_exception():
    def failing_tool(_arguments):
        raise RuntimeError("backend unavailable")

    registry = ToolRegistry()
    registry.register("failing", failing_tool)

    observation = registry.dispatch("failing")

    assert observation.tool_name == "failing"
    assert observation.success is False
    assert observation.diagnostics[0].code == "tool_execution_error"
    assert observation.diagnostics[0].message == "Tool 'failing' raised RuntimeError: backend unavailable"
    assert observation.diagnostics[0].details == {
        "exception_type": "RuntimeError",
        "exception_message": "backend unavailable",
    }
    assert observation.provenance == [
        Provenance(
            source="failing",
            metadata={"phase": "execution"},
        )
    ]


def test_dispatch_standardizes_invalid_tool_result():
    registry = ToolRegistry()
    registry.register("invalid", lambda _arguments: {"success": True})

    observation = registry.dispatch("invalid")

    assert observation.success is False
    assert observation.diagnostics[0].code == "invalid_tool_result"
    assert observation.diagnostics[0].details == {"returned_type": "dict"}
    assert observation.provenance[0].metadata == {"phase": "normalization"}


def test_registry_names_are_sorted_and_read_only():
    registry = ToolRegistry()
    registry.register("zeta", lambda _arguments: ToolResult(success=True))
    registry.register("alpha", lambda _arguments: ToolResult(success=True))

    assert registry.names == ("alpha", "zeta")
