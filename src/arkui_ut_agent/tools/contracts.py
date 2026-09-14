"""Shared result and observation models for project-owned tools."""

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class DiagnosticSeverity(str, Enum):
    """Severity of a diagnostic emitted while executing a tool."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class Diagnostic(BaseModel):
    """Structured information explaining a tool warning or failure."""

    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    severity: DiagnosticSeverity = DiagnosticSeverity.ERROR
    location: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class Provenance(BaseModel):
    """Origin information carried with facts returned by a tool."""

    model_config = ConfigDict(extra="forbid")

    source: str
    location: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ToolResult(BaseModel):
    """The result contract implemented by every project-owned tool."""

    model_config = ConfigDict(extra="forbid")

    success: bool
    data: Any = None
    summary: str = ""
    diagnostics: list[Diagnostic] = Field(default_factory=list)
    provenance: list[Provenance] = Field(default_factory=list)


class Observation(BaseModel):
    """Normalized tool output consumed by the future agent control plane."""

    model_config = ConfigDict(extra="forbid")

    tool_name: str
    success: bool
    data: Any = None
    summary: str = ""
    diagnostics: list[Diagnostic] = Field(default_factory=list)
    provenance: list[Provenance] = Field(default_factory=list)


def normalize_tool_result(tool_name: str, result: ToolResult) -> Observation:
    """Convert a tool result to an observation without dropping diagnostics or provenance."""
    return Observation(
        tool_name=tool_name,
        success=result.success,
        data=result.data,
        summary=result.summary,
        diagnostics=result.diagnostics,
        provenance=result.provenance,
    )


__all__ = [
    "Diagnostic",
    "DiagnosticSeverity",
    "Observation",
    "Provenance",
    "ToolResult",
    "normalize_tool_result",
]
