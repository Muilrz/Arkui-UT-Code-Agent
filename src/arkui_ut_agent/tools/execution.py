"""Thin execution tools backed by the existing local environment."""

from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol

from arkui_ut_agent.environments.local import LocalEnvironment
from arkui_ut_agent.tools.contracts import Diagnostic, Provenance, ToolResult
from arkui_ut_agent.tools.registry import ToolRegistry

_TOOL_NAMES = ("run_command", "build", "test")


class CommandEnvironment(Protocol):
    """The submission-free execution primitive required by execution tools."""

    config: Any
    backend: Any

    def execute_command(
        self,
        command: str,
        cwd: str = "",
        *,
        timeout: float | None = None,
        env: Mapping[str, str] | None = None,
    ) -> dict[str, Any]: ...


class ExecutionTools:
    """Run explicit commands through one configured command environment."""

    def __init__(
        self,
        repository_root: str | Path,
        *,
        environment: CommandEnvironment | None = None,
    ) -> None:
        root = Path(repository_root).resolve()
        if not root.is_dir():
            raise ValueError(f"Repository root is not a directory: {root}")
        self.repository_root = root
        self._environment = environment

    def register(self, registry: ToolRegistry) -> None:
        """Register all execution tools without partially registering on a name collision."""
        duplicate_names = sorted(set(_TOOL_NAMES).intersection(registry.names))
        if duplicate_names:
            names = ", ".join(duplicate_names)
            raise ValueError(f"Execution tool names are already registered: {names}.")

        for name in _TOOL_NAMES:
            registry.register(name, getattr(self, name))

    def run_command(self, arguments: Mapping[str, Any]) -> ToolResult:
        """Execute one explicit command without inspecting or rewriting it."""
        return self._execute("run_command", arguments)

    def build(self, arguments: Mapping[str, Any]) -> ToolResult:
        """Execute one caller-provided build command without target discovery."""
        return self._execute("build", arguments)

    def test(self, arguments: Mapping[str, Any]) -> ToolResult:
        """Execute one caller-provided test command without test discovery."""
        return self._execute("test", arguments)

    def _execute(self, tool_name: str, arguments: Mapping[str, Any]) -> ToolResult:
        invalid = _reject_unknown_arguments(tool_name, arguments, {"command", "cwd", "timeout", "env"})
        if invalid:
            return invalid

        command = arguments.get("command")
        timeout = arguments.get("timeout")
        env_override = arguments.get("env", {})
        if not isinstance(command, str) or not command.strip():
            return _invalid_arguments(tool_name, "'command' must be a non-empty string.")
        if timeout is not None and not _is_positive_number(timeout):
            return _invalid_arguments(tool_name, "'timeout' must be a positive number.")
        if not isinstance(env_override, Mapping):
            return _invalid_arguments(tool_name, "'env' must be a mapping of string names to string values.")
        if any(not isinstance(key, str) or not key or not isinstance(value, str) for key, value in env_override.items()):
            return _invalid_arguments(tool_name, "Every 'env' name must be non-empty and every value must be a string.")
        env_override = dict(env_override)

        resolved = self._resolve_cwd(
            tool_name,
            arguments.get("cwd", "."),
            command=command,
            timeout=timeout,
            env_keys=sorted(env_override),
        )
        if isinstance(resolved, ToolResult):
            return resolved
        cwd, location = resolved

        try:
            environment = (
                self._environment
                if self._environment is not None
                else LocalEnvironment(cwd=str(self.repository_root))
            )
            self._environment = environment
        except (OSError, RuntimeError) as exc:
            return _failure(
                tool_name,
                "backend_unavailable",
                f"Could not initialize the local shell backend: {exc}",
                command=command,
                cwd=location,
                timeout=timeout,
                env_keys=sorted(env_override),
                details={"exception_type": type(exc).__name__},
            )

        try:
            raw_result = environment.execute_command(
                command,
                cwd=str(cwd),
                timeout=timeout,
                env=env_override,
            )
        except Exception as exc:
            return _failure(
                tool_name,
                "execution_environment_error",
                f"Execution environment raised {type(exc).__name__}: {exc}",
                command=command,
                cwd=location,
                timeout=_effective_timeout(environment, timeout),
                env_keys=sorted(env_override),
                details={"exception_type": type(exc).__name__},
            )

        return _normalize_execution_result(
            tool_name=tool_name,
            command=command,
            cwd=location,
            timeout=_effective_timeout(environment, timeout),
            env_keys=sorted(env_override),
            backend_name=_backend_attribute(environment, "name"),
            backend_dialect=_backend_attribute(environment, "dialect"),
            raw_result=raw_result,
        )

    def _resolve_cwd(
        self,
        tool_name: str,
        raw_cwd: Any,
        *,
        command: str,
        timeout: float | None,
        env_keys: list[str],
    ) -> tuple[Path, str] | ToolResult:
        if not isinstance(raw_cwd, str) or not raw_cwd:
            return _invalid_arguments(tool_name, "'cwd' must be a non-empty string.")

        candidate = Path(raw_cwd)
        if not candidate.is_absolute():
            candidate = self.repository_root / candidate
        try:
            resolved = candidate.resolve()
            relative = resolved.relative_to(self.repository_root)
        except ValueError:
            return _failure(
                tool_name,
                "cwd_outside_repository",
                "Working directory must remain inside the configured repository root.",
                command=command,
                cwd=str(raw_cwd),
                timeout=timeout,
                env_keys=env_keys,
            )
        except OSError as exc:
            return _failure(
                tool_name,
                "cwd_resolution_error",
                f"Could not resolve working directory: {exc}",
                command=command,
                cwd=str(raw_cwd),
                timeout=timeout,
                env_keys=env_keys,
                details={"exception_type": type(exc).__name__},
            )

        location = relative.as_posix() if relative.parts else "."
        if not resolved.exists():
            return _failure(
                tool_name,
                "cwd_not_found",
                "Working directory does not exist.",
                command=command,
                cwd=location,
                timeout=timeout,
                env_keys=env_keys,
            )
        if not resolved.is_dir():
            return _failure(
                tool_name,
                "cwd_not_a_directory",
                "Working directory is not a directory.",
                command=command,
                cwd=location,
                timeout=timeout,
                env_keys=env_keys,
            )
        return resolved, location


def register_execution_tools(
    registry: ToolRegistry,
    repository_root: str | Path,
    *,
    environment: CommandEnvironment | None = None,
) -> ExecutionTools:
    """Create and atomically register the three execution tools."""
    tools = ExecutionTools(repository_root, environment=environment)
    tools.register(registry)
    return tools


def _normalize_execution_result(
    *,
    tool_name: str,
    command: str,
    cwd: str,
    timeout: float | None,
    env_keys: list[str],
    backend_name: str,
    backend_dialect: str,
    raw_result: Any,
) -> ToolResult:
    if not isinstance(raw_result, Mapping):
        return _failure(
            tool_name,
            "invalid_execution_result",
            "Execution environment returned a non-mapping result.",
            command=command,
            cwd=cwd,
            timeout=timeout,
            env_keys=env_keys,
            backend_name=backend_name,
            backend_dialect=backend_dialect,
            details={"returned_type": type(raw_result).__name__},
        )

    output = raw_result.get("output", "")
    returncode = raw_result.get("returncode")
    exception_info = raw_result.get("exception_info", "")
    extra = raw_result.get("extra", {})
    if not isinstance(output, str) or not isinstance(returncode, int) or isinstance(returncode, bool):
        return _failure(
            tool_name,
            "invalid_execution_result",
            "Execution result must contain string 'output' and integer 'returncode'.",
            command=command,
            cwd=cwd,
            timeout=timeout,
            env_keys=env_keys,
            backend_name=backend_name,
            backend_dialect=backend_dialect,
        )
    if not isinstance(exception_info, str) or not isinstance(extra, Mapping):
        return _failure(
            tool_name,
            "invalid_execution_result",
            "Execution result contains invalid exception metadata.",
            command=command,
            cwd=cwd,
            timeout=timeout,
            env_keys=env_keys,
            backend_name=backend_name,
            backend_dialect=backend_dialect,
        )

    exception_type = extra.get("exception_type")
    timed_out = exception_type == "TimeoutExpired"
    execution_failed = bool(exception_info)
    data = {
        "command": command,
        "cwd": cwd,
        "output": output,
        "returncode": returncode,
        "timed_out": timed_out,
        "execution_failed": execution_failed,
        "timeout_seconds": timeout,
        "env_keys": env_keys,
    }
    provenance = _provenance(
        tool_name,
        command=command,
        cwd=cwd,
        returncode=returncode,
        timeout=timeout,
        timed_out=timed_out,
        env_keys=env_keys,
        backend_name=backend_name,
        backend_dialect=backend_dialect,
    )

    if timed_out:
        timeout_description = f"{timeout:g} second(s)" if timeout is not None else "the configured timeout"
        message = f"{tool_name} command timed out after {timeout_description}."
        return ToolResult(
            success=False,
            data=data,
            summary=message,
            diagnostics=[
                Diagnostic(
                    code="command_timeout",
                    message=message,
                    location=cwd,
                    details={"returncode": returncode, "timeout_seconds": timeout},
                )
            ],
            provenance=[provenance],
        )
    if execution_failed:
        code = "backend_unavailable" if exception_type == "FileNotFoundError" else "process_start_failed"
        message = exception_info or f"{tool_name} command could not be started."
        return ToolResult(
            success=False,
            data=data,
            summary=message,
            diagnostics=[
                Diagnostic(
                    code=code,
                    message=message,
                    location=cwd,
                    details={"returncode": returncode, "exception_type": exception_type},
                )
            ],
            provenance=[provenance],
        )
    if returncode != 0:
        message = f"{tool_name} command exited with return code {returncode}."
        return ToolResult(
            success=False,
            data=data,
            summary=message,
            diagnostics=[
                Diagnostic(
                    code="command_failed",
                    message=message,
                    location=cwd,
                    details={"returncode": returncode},
                )
            ],
            provenance=[provenance],
        )

    return ToolResult(
        success=True,
        data=data,
        summary=f"{tool_name} command completed with return code 0.",
        provenance=[provenance],
    )


def _failure(
    tool_name: str,
    code: str,
    message: str,
    *,
    command: str | None,
    cwd: str | None,
    timeout: float | None,
    env_keys: list[str],
    backend_name: str = "",
    backend_dialect: str = "",
    details: dict[str, Any] | None = None,
) -> ToolResult:
    return ToolResult(
        success=False,
        data={
            "command": command,
            "cwd": cwd,
            "output": "",
            "returncode": None,
            "timed_out": False,
            "execution_failed": True,
            "timeout_seconds": timeout,
            "env_keys": env_keys,
        },
        summary=message,
        diagnostics=[Diagnostic(code=code, message=message, location=cwd, details=details or {})],
        provenance=[
            _provenance(
                tool_name,
                command=command,
                cwd=cwd,
                returncode=None,
                timeout=timeout,
                timed_out=False,
                env_keys=env_keys,
                backend_name=backend_name,
                backend_dialect=backend_dialect,
            )
        ],
    )


def _provenance(
    tool_name: str,
    *,
    command: str | None,
    cwd: str | None,
    returncode: int | None,
    timeout: float | None,
    timed_out: bool,
    env_keys: list[str],
    backend_name: str,
    backend_dialect: str,
) -> Provenance:
    return Provenance(
        source=tool_name,
        location=cwd,
        metadata={
            "command": command,
            "returncode": returncode,
            "timeout_seconds": timeout,
            "timed_out": timed_out,
            "env_keys": env_keys,
            "backend_name": backend_name,
            "backend_dialect": backend_dialect,
        },
    )


def _effective_timeout(environment: CommandEnvironment, requested_timeout: float | None) -> float | None:
    if requested_timeout is not None:
        return requested_timeout
    configured_timeout = getattr(getattr(environment, "config", None), "timeout", None)
    return configured_timeout if _is_positive_number(configured_timeout) else None


def _backend_attribute(environment: CommandEnvironment, attribute: str) -> str:
    value = getattr(getattr(environment, "backend", None), attribute, "")
    return value if isinstance(value, str) else ""


def _reject_unknown_arguments(
    tool_name: str,
    arguments: Mapping[str, Any],
    allowed: set[str],
) -> ToolResult | None:
    unknown = sorted(set(arguments).difference(allowed))
    if not unknown:
        return None
    return _invalid_arguments(tool_name, f"Unknown argument(s): {', '.join(unknown)}.")


def _invalid_arguments(tool_name: str, message: str) -> ToolResult:
    return _failure(tool_name, "invalid_arguments", message, command=None, cwd=None, timeout=None, env_keys=[])


def _is_positive_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0


__all__ = ["CommandEnvironment", "ExecutionTools", "register_execution_tools"]
