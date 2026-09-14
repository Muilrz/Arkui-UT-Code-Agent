import pytest
from pydantic import ValidationError

from arkui_ut_agent.tools import (
    Diagnostic,
    DiagnosticSeverity,
    Provenance,
    ToolResult,
    normalize_tool_result,
)


@pytest.mark.parametrize("success", [True, False])
def test_normalize_tool_result_preserves_success_and_payload(success):
    result = ToolResult(success=success, data={"matches": [1, 2]}, summary="finished")

    observation = normalize_tool_result("example", result)

    assert observation.tool_name == "example"
    assert observation.success is success
    assert observation.data == {"matches": [1, 2]}
    assert observation.summary == "finished"


def test_normalize_tool_result_preserves_diagnostics_and_provenance():
    diagnostic = Diagnostic(
        code="partial_result",
        message="One path could not be read.",
        severity=DiagnosticSeverity.WARNING,
        location="src/missing.cc",
        details={"reason": "permission denied"},
    )
    provenance = Provenance(
        source="rg_search",
        location="src/example.cc:17",
        metadata={"revision": "abc123"},
    )
    result = ToolResult(
        success=False,
        diagnostics=[diagnostic],
        provenance=[provenance],
    )

    observation = normalize_tool_result("rg_search", result)

    assert observation.diagnostics == [diagnostic]
    assert observation.provenance == [provenance]
    assert observation.model_dump(mode="json")["provenance"] == [
        {
            "source": "rg_search",
            "location": "src/example.cc:17",
            "metadata": {"revision": "abc123"},
        }
    ]


def test_contract_models_do_not_share_mutable_defaults():
    first = ToolResult(success=True)
    second = ToolResult(success=True)

    first.diagnostics.append(Diagnostic(code="notice", message="first only"))
    first.provenance.append(Provenance(source="first"))

    assert second.diagnostics == []
    assert second.provenance == []


def test_contract_models_reject_unknown_fields():
    with pytest.raises(ValidationError):
        ToolResult(success=True, unexpected="value")
