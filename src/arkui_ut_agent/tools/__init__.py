"""Tool contracts and registry for arkui-ut-code-agent."""

from arkui_ut_agent.tools.contracts import (
    Diagnostic,
    DiagnosticSeverity,
    Observation,
    Provenance,
    ToolResult,
    normalize_tool_result,
)
from arkui_ut_agent.tools.editing import EditingTools, register_editing_tools
from arkui_ut_agent.tools.execution import CommandEnvironment, ExecutionTools, register_execution_tools
from arkui_ut_agent.tools.knowledge import (
    ArkuiKbBackend,
    ArkuiKbBackendError,
    ArkuiKbEntry,
    ArkuiKbSearchResponse,
    KnowledgeTools,
    LocalArkuiKbBackend,
    register_knowledge_tools,
)
from arkui_ut_agent.tools.registry import ToolHandler, ToolRegistry
from arkui_ut_agent.tools.repository import ProcessRunner, RepositoryReadTools, register_repository_read_tools

__all__ = [
    "Diagnostic",
    "DiagnosticSeverity",
    "EditingTools",
    "CommandEnvironment",
    "ExecutionTools",
    "ArkuiKbBackend",
    "ArkuiKbBackendError",
    "ArkuiKbEntry",
    "ArkuiKbSearchResponse",
    "KnowledgeTools",
    "LocalArkuiKbBackend",
    "Observation",
    "Provenance",
    "ProcessRunner",
    "RepositoryReadTools",
    "ToolHandler",
    "ToolRegistry",
    "ToolResult",
    "normalize_tool_result",
    "register_editing_tools",
    "register_execution_tools",
    "register_knowledge_tools",
    "register_repository_read_tools",
]
