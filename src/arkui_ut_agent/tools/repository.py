"""Read-only repository tools backed by pathlib, ripgrep, and Git."""

import json
import os
import subprocess
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from arkui_ut_agent.tools.contracts import Diagnostic, DiagnosticSeverity, Provenance, ToolResult
from arkui_ut_agent.tools.registry import ToolRegistry

ProcessRunner = Callable[..., subprocess.CompletedProcess[str]]

_TOOL_NAMES = ("rg_search", "read_file", "list_files", "git_diff", "git_status")


class RepositoryReadTools:
    """Execute read-only queries against one repository root."""

    def __init__(
        self,
        repository_root: str | Path,
        *,
        runner: ProcessRunner | None = None,
        rg_executable: str = "rg",
        git_executable: str = "git",
        timeout: float = 30,
    ) -> None:
        root = Path(repository_root).resolve()
        if not root.is_dir():
            raise ValueError(f"Repository root is not a directory: {root}")
        if timeout <= 0:
            raise ValueError("Repository tool timeout must be greater than zero.")

        self.repository_root = root
        self._runner = runner if runner is not None else subprocess.run
        self._rg_executable = rg_executable
        self._git_executable = git_executable
        self._timeout = timeout

    def register(self, registry: ToolRegistry) -> None:
        """Register all repository read tools without partially registering on a name collision."""
        duplicate_names = sorted(set(_TOOL_NAMES).intersection(registry.names))
        if duplicate_names:
            names = ", ".join(duplicate_names)
            raise ValueError(f"Repository tool names are already registered: {names}.")

        for name in _TOOL_NAMES:
            registry.register(name, getattr(self, name))

    def rg_search(self, arguments: Mapping[str, Any]) -> ToolResult:
        """Search repository text with ripgrep and return structured matches."""
        tool_name = "rg_search"
        invalid = _reject_unknown_arguments(tool_name, arguments, {"pattern", "path", "glob", "max_results"})
        if invalid:
            return invalid

        pattern = arguments.get("pattern")
        if not isinstance(pattern, str) or not pattern:
            return _invalid_arguments(tool_name, "'pattern' must be a non-empty string.")

        max_results = arguments.get("max_results", 100)
        if not _is_positive_int(max_results):
            return _invalid_arguments(tool_name, "'max_results' must be a positive integer.")

        globs = arguments.get("glob", [])
        if isinstance(globs, str):
            globs = [globs]
        if not isinstance(globs, Sequence) or isinstance(globs, (bytes, bytearray)):
            return _invalid_arguments(tool_name, "'glob' must be a string or a list of strings.")
        if any(not isinstance(item, str) or not item for item in globs):
            return _invalid_arguments(tool_name, "Every 'glob' value must be a non-empty string.")
        globs = list(globs)

        resolved = self._resolve_path(tool_name, arguments.get("path", "."))
        if isinstance(resolved, ToolResult):
            return resolved
        search_path, location = resolved
        if not search_path.exists():
            return _path_failure(tool_name, "path_not_found", "Search path does not exist.", location)

        command = [self._rg_executable, "--json", "--color", "never"]
        for glob in globs:
            command.extend(["--glob", glob])
        command.extend(["--", pattern, location])

        completed = self._run_process(tool_name, command, location)
        if isinstance(completed, ToolResult):
            return completed
        if completed.returncode not in (0, 1):
            return _process_failure(tool_name, "rg_failed", completed, location)

        try:
            matches = _parse_rg_matches(completed.stdout or "")
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            return _failure(
                tool_name,
                "invalid_tool_output",
                f"Could not parse ripgrep output: {exc}",
                location=location,
                details={"exception_type": type(exc).__name__},
            )

        truncated = len(matches) > max_results
        matches = matches[:max_results]
        diagnostics = _truncation_diagnostics(max_results) if truncated else []
        return ToolResult(
            success=True,
            data={"matches": matches, "count": len(matches), "truncated": truncated},
            summary=f"Found {len(matches)} match(es).",
            diagnostics=diagnostics,
            provenance=[
                Provenance(
                    source=tool_name,
                    location=location,
                    metadata={"pattern": pattern, "glob": globs, "truncated": truncated},
                )
            ],
        )

    def read_file(self, arguments: Mapping[str, Any]) -> ToolResult:
        """Read a UTF-8 repository file, optionally selecting an inclusive line range."""
        tool_name = "read_file"
        invalid = _reject_unknown_arguments(tool_name, arguments, {"path", "start_line", "end_line"})
        if invalid:
            return invalid

        resolved = self._resolve_path(tool_name, arguments.get("path"))
        if isinstance(resolved, ToolResult):
            return resolved
        file_path, location = resolved
        if not file_path.exists():
            return _path_failure(tool_name, "path_not_found", "File does not exist.", location)
        if not file_path.is_file():
            return _path_failure(tool_name, "not_a_file", "Path is not a file.", location)

        start_line = arguments.get("start_line", 1)
        end_line = arguments.get("end_line")
        if not _is_positive_int(start_line):
            return _invalid_arguments(tool_name, "'start_line' must be a positive integer.", location)
        if end_line is not None and (not _is_positive_int(end_line) or end_line < start_line):
            return _invalid_arguments(
                tool_name,
                "'end_line' must be a positive integer greater than or equal to 'start_line'.",
                location,
            )

        try:
            content = file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            return _failure(
                tool_name,
                "file_decode_error",
                "File is not valid UTF-8.",
                location=location,
                details={"start": exc.start, "end": exc.end},
            )
        except OSError as exc:
            return _failure(
                tool_name,
                "file_read_error",
                f"Could not read file: {exc}",
                location=location,
                details={"exception_type": type(exc).__name__},
            )

        lines = content.splitlines(keepends=True)
        if lines and start_line > len(lines):
            return _failure(
                tool_name,
                "line_out_of_range",
                f"'start_line' {start_line} exceeds the file's {len(lines)} line(s).",
                location=location,
                details={"start_line": start_line, "total_lines": len(lines)},
            )

        selected_lines = lines[start_line - 1 : end_line]
        returned_end = start_line + len(selected_lines) - 1 if selected_lines else 0
        return ToolResult(
            success=True,
            data={
                "path": location,
                "content": "".join(selected_lines),
                "start_line": start_line,
                "end_line": returned_end,
                "total_lines": len(lines),
            },
            summary=f"Read {len(selected_lines)} line(s) from {location}.",
            provenance=[
                Provenance(
                    source=tool_name,
                    location=location,
                    metadata={"start_line": start_line, "end_line": returned_end},
                )
            ],
        )

    def list_files(self, arguments: Mapping[str, Any]) -> ToolResult:
        """List repository files in deterministic path order without following directory symlinks."""
        tool_name = "list_files"
        invalid = _reject_unknown_arguments(tool_name, arguments, {"path", "glob", "recursive", "max_results"})
        if invalid:
            return invalid

        pattern = arguments.get("glob", "*")
        recursive = arguments.get("recursive", True)
        max_results = arguments.get("max_results", 1000)
        if not isinstance(pattern, str) or not pattern:
            return _invalid_arguments(tool_name, "'glob' must be a non-empty string.")
        if not isinstance(recursive, bool):
            return _invalid_arguments(tool_name, "'recursive' must be a boolean.")
        if not _is_positive_int(max_results):
            return _invalid_arguments(tool_name, "'max_results' must be a positive integer.")

        resolved = self._resolve_path(tool_name, arguments.get("path", "."))
        if isinstance(resolved, ToolResult):
            return resolved
        directory, location = resolved
        if not directory.exists():
            return _path_failure(tool_name, "path_not_found", "Directory does not exist.", location)
        if not directory.is_dir():
            return _path_failure(tool_name, "not_a_directory", "Path is not a directory.", location)

        files: list[str] = []
        walk_errors: list[OSError] = []
        try:
            for current_root, directory_names, file_names in os.walk(
                directory,
                topdown=True,
                followlinks=False,
                onerror=walk_errors.append,
            ):
                directory_names[:] = sorted(name for name in directory_names if name != ".git")
                for file_name in sorted(file_names):
                    file_path = Path(current_root, file_name)
                    relative_to_search = file_path.relative_to(directory)
                    if relative_to_search.match(pattern):
                        files.append(file_path.relative_to(self.repository_root).as_posix())
                        if len(files) > max_results:
                            break
                if len(files) > max_results:
                    break
                if not recursive:
                    directory_names.clear()
        except OSError as exc:
            walk_errors.append(exc)

        if walk_errors:
            error = walk_errors[0]
            return _failure(
                tool_name,
                "directory_read_error",
                f"Could not list directory: {error}",
                location=location,
                details={"exception_type": type(error).__name__},
                data={"files": files[:max_results], "count": min(len(files), max_results)},
            )

        truncated = len(files) > max_results
        files = files[:max_results]
        diagnostics = _truncation_diagnostics(max_results) if truncated else []
        return ToolResult(
            success=True,
            data={"files": files, "count": len(files), "truncated": truncated},
            summary=f"Listed {len(files)} file(s).",
            diagnostics=diagnostics,
            provenance=[
                Provenance(
                    source=tool_name,
                    location=location,
                    metadata={"glob": pattern, "recursive": recursive, "truncated": truncated},
                )
            ],
        )

    def git_diff(self, arguments: Mapping[str, Any]) -> ToolResult:
        """Return the unstaged or staged Git diff for optional repository-relative paths."""
        tool_name = "git_diff"
        invalid = _reject_unknown_arguments(tool_name, arguments, {"staged", "paths"})
        if invalid:
            return invalid

        staged = arguments.get("staged", False)
        if not isinstance(staged, bool):
            return _invalid_arguments(tool_name, "'staged' must be a boolean.")
        paths = self._resolve_git_paths(tool_name, arguments.get("paths", []))
        if isinstance(paths, ToolResult):
            return paths

        command = [self._git_executable, "diff", "--no-ext-diff", "--no-color"]
        if staged:
            command.append("--cached")
        if paths:
            command.extend(["--", *paths])

        completed = self._run_process(tool_name, command, ".")
        if isinstance(completed, ToolResult):
            return completed
        if completed.returncode != 0:
            return _process_failure(tool_name, "git_failed", completed, ".")

        diff = completed.stdout or ""
        return ToolResult(
            success=True,
            data={"diff": diff, "staged": staged, "paths": paths},
            summary=f"Read Git diff with {len(diff.splitlines())} line(s).",
            provenance=[Provenance(source=tool_name, location=".", metadata={"staged": staged, "paths": paths})],
        )

    def git_status(self, arguments: Mapping[str, Any]) -> ToolResult:
        """Return parsed Git porcelain status for the repository."""
        tool_name = "git_status"
        invalid = _reject_unknown_arguments(tool_name, arguments, set())
        if invalid:
            return invalid

        command = [self._git_executable, "status", "--porcelain=v1", "--branch", "--untracked-files=all"]
        completed = self._run_process(tool_name, command, ".")
        if isinstance(completed, ToolResult):
            return completed
        if completed.returncode != 0:
            return _process_failure(tool_name, "git_failed", completed, ".")

        raw_status = completed.stdout or ""
        branch = ""
        entries = []
        for line in raw_status.splitlines():
            if line.startswith("## "):
                branch = line[3:]
            elif len(line) >= 3:
                entries.append({"status": line[:2], "path": line[3:]})

        return ToolResult(
            success=True,
            data={"branch": branch, "entries": entries, "clean": not entries, "raw": raw_status},
            summary=f"Git status contains {len(entries)} change(s).",
            provenance=[
                Provenance(source=tool_name, location=".", metadata={"change_count": len(entries), "clean": not entries})
            ],
        )

    def _resolve_path(self, tool_name: str, raw_path: Any) -> tuple[Path, str] | ToolResult:
        if not isinstance(raw_path, str) or not raw_path:
            return _invalid_arguments(tool_name, "'path' must be a non-empty string.")

        candidate = Path(raw_path)
        if not candidate.is_absolute():
            candidate = self.repository_root / candidate
        try:
            resolved = candidate.resolve()
            relative = resolved.relative_to(self.repository_root)
        except ValueError:
            return _failure(
                tool_name,
                "path_outside_repository",
                "Path must remain inside the configured repository root.",
                location=str(raw_path),
            )
        except OSError as exc:
            return _failure(
                tool_name,
                "path_resolution_error",
                f"Could not resolve path: {exc}",
                location=str(raw_path),
                details={"exception_type": type(exc).__name__},
            )

        location = relative.as_posix() if relative.parts else "."
        return resolved, location

    def _resolve_git_paths(self, tool_name: str, raw_paths: Any) -> list[str] | ToolResult:
        if isinstance(raw_paths, str):
            raw_paths = [raw_paths]
        if not isinstance(raw_paths, Sequence) or isinstance(raw_paths, (bytes, bytearray)):
            return _invalid_arguments(tool_name, "'paths' must be a string or a list of strings.")

        paths: list[str] = []
        for raw_path in raw_paths:
            resolved = self._resolve_path(tool_name, raw_path)
            if isinstance(resolved, ToolResult):
                return resolved
            _, location = resolved
            paths.append(location)
        return paths

    def _run_process(
        self,
        tool_name: str,
        command: list[str],
        location: str,
    ) -> subprocess.CompletedProcess[str] | ToolResult:
        try:
            return self._runner(
                command,
                cwd=self.repository_root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self._timeout,
                check=False,
            )
        except FileNotFoundError:
            return _failure(
                tool_name,
                "tool_unavailable",
                f"Required executable '{command[0]}' was not found.",
                location=location,
                details={"executable": command[0]},
            )
        except subprocess.TimeoutExpired:
            return _failure(
                tool_name,
                "tool_timeout",
                f"Command exceeded the {self._timeout:g} second timeout.",
                location=location,
                details={"timeout_seconds": self._timeout},
            )
        except OSError as exc:
            return _failure(
                tool_name,
                "process_error",
                f"Could not execute command: {exc}",
                location=location,
                details={"exception_type": type(exc).__name__},
            )


def register_repository_read_tools(
    registry: ToolRegistry,
    repository_root: str | Path,
    **kwargs: Any,
) -> RepositoryReadTools:
    """Create and register the five repository read tools."""
    tools = RepositoryReadTools(repository_root, **kwargs)
    tools.register(registry)
    return tools


def _parse_rg_matches(output: str) -> list[dict[str, Any]]:
    matches = []
    for raw_line in output.splitlines():
        event = json.loads(raw_line)
        if event.get("type") != "match":
            continue
        data = event["data"]
        path = data["path"]["text"]
        line_number = data["line_number"]
        submatches = data.get("submatches", [])
        column = submatches[0]["start"] + 1 if submatches else 1
        matches.append(
            {
                "path": Path(path).as_posix(),
                "line": line_number,
                "column": column,
                "text": data["lines"]["text"].rstrip("\r\n"),
            }
        )
    return matches


def _reject_unknown_arguments(
    tool_name: str,
    arguments: Mapping[str, Any],
    allowed: set[str],
) -> ToolResult | None:
    unknown = sorted(set(arguments).difference(allowed))
    if not unknown:
        return None
    return _invalid_arguments(tool_name, f"Unknown argument(s): {', '.join(unknown)}.")


def _is_positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _invalid_arguments(tool_name: str, message: str, location: str | None = None) -> ToolResult:
    return _failure(tool_name, "invalid_arguments", message, location=location)


def _path_failure(tool_name: str, code: str, message: str, location: str) -> ToolResult:
    return _failure(tool_name, code, f"{message} Path: {location}", location=location)


def _process_failure(
    tool_name: str,
    code: str,
    completed: subprocess.CompletedProcess[str],
    location: str,
) -> ToolResult:
    stderr = (completed.stderr or "").strip()
    message = stderr or f"Command exited with code {completed.returncode}."
    return _failure(
        tool_name,
        code,
        message,
        location=location,
        details={"returncode": completed.returncode, "stderr": stderr},
    )


def _truncation_diagnostics(max_results: int) -> list[Diagnostic]:
    return [
        Diagnostic(
            code="results_truncated",
            message=f"Results were limited to {max_results} item(s).",
            severity=DiagnosticSeverity.WARNING,
            details={"max_results": max_results},
        )
    ]


def _failure(
    tool_name: str,
    code: str,
    message: str,
    *,
    location: str | None = None,
    details: dict[str, Any] | None = None,
    data: Any = None,
) -> ToolResult:
    return ToolResult(
        success=False,
        data=data,
        summary=message,
        diagnostics=[Diagnostic(code=code, message=message, location=location, details=details or {})],
        provenance=[Provenance(source=tool_name, location=location)],
    )


__all__ = ["ProcessRunner", "RepositoryReadTools", "register_repository_read_tools"]
