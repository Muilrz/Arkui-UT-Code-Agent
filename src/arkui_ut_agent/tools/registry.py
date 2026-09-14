"""Registration and dispatch for tools that implement the shared contract."""

from collections.abc import Callable, Mapping
from typing import Any

from arkui_ut_agent.tools.contracts import Diagnostic, Observation, Provenance, ToolResult, normalize_tool_result

ToolHandler = Callable[[Mapping[str, Any]], ToolResult]


class ToolRegistry:
    """Store named tools and normalize every dispatch outcome to an observation."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolHandler] = {}

    @property
    def names(self) -> tuple[str, ...]:
        """Return registered names in a deterministic order."""
        return tuple(sorted(self._tools))

    def register(self, name: str, handler: ToolHandler) -> None:
        """Register a tool, rejecting invalid or duplicate names."""
        if not name or name != name.strip():
            raise ValueError("Tool name must be a non-empty string without surrounding whitespace.")
        if not callable(handler):
            raise TypeError("Tool handler must be callable.")
        if name in self._tools:
            raise ValueError(f"Tool '{name}' is already registered.")
        self._tools[name] = handler

    def dispatch(self, name: str, arguments: Mapping[str, Any] | None = None) -> Observation:
        """Execute a named tool and represent lookup, contract, and execution failures as data."""
        handler = self._tools.get(name)
        if handler is None:
            return _failure_observation(
                tool_name=name,
                code="unknown_tool",
                message=f"Tool '{name}' is not registered.",
                source="tool_registry",
                metadata={"requested_tool": name},
            )

        try:
            result = handler(dict(arguments or {}))
        except Exception as exc:
            return _failure_observation(
                tool_name=name,
                code="tool_execution_error",
                message=f"Tool '{name}' raised {type(exc).__name__}: {exc}",
                source=name,
                diagnostic_details={
                    "exception_type": type(exc).__name__,
                    "exception_message": str(exc),
                },
                metadata={"phase": "execution"},
            )

        if not isinstance(result, ToolResult):
            return _failure_observation(
                tool_name=name,
                code="invalid_tool_result",
                message=f"Tool '{name}' returned {type(result).__name__}; expected ToolResult.",
                source=name,
                diagnostic_details={"returned_type": type(result).__name__},
                metadata={"phase": "normalization"},
            )

        return normalize_tool_result(name, result)


def _failure_observation(
    *,
    tool_name: str,
    code: str,
    message: str,
    source: str,
    metadata: dict[str, Any],
    diagnostic_details: dict[str, Any] | None = None,
) -> Observation:
    result = ToolResult(
        success=False,
        summary=message,
        diagnostics=[Diagnostic(code=code, message=message, details=diagnostic_details or {})],
        provenance=[Provenance(source=source, metadata=metadata)],
    )
    return normalize_tool_result(tool_name, result)


__all__ = ["ToolHandler", "ToolRegistry"]
