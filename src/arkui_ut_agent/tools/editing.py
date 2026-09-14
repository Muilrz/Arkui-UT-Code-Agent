"""Deterministic UTF-8 editing tools constrained to one repository root."""

import hashlib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from arkui_ut_agent.tools.contracts import Diagnostic, Provenance, ToolResult
from arkui_ut_agent.tools.registry import ToolRegistry

_TOOL_NAMES = ("apply_patch", "write_file")


class EditingTools:
    """Apply explicit text edits to files within a configured repository root."""

    def __init__(self, repository_root: str | Path) -> None:
        root = Path(repository_root).resolve()
        if not root.is_dir():
            raise ValueError(f"Repository root is not a directory: {root}")
        self.repository_root = root

    def register(self, registry: ToolRegistry) -> None:
        """Register both editing tools without partially registering on a name collision."""
        duplicate_names = sorted(set(_TOOL_NAMES).intersection(registry.names))
        if duplicate_names:
            names = ", ".join(duplicate_names)
            raise ValueError(f"Editing tool names are already registered: {names}.")

        for name in _TOOL_NAMES:
            registry.register(name, getattr(self, name))

    def write_file(self, arguments: Mapping[str, Any]) -> ToolResult:
        """Create or overwrite one UTF-8 text file using explicit parent-directory behavior."""
        tool_name = "write_file"
        invalid = _reject_unknown_arguments(
            tool_name,
            arguments,
            {"path", "content", "create_parents", "overwrite"},
        )
        if invalid:
            return invalid

        content = arguments.get("content")
        create_parents = arguments.get("create_parents", False)
        overwrite = arguments.get("overwrite", True)
        if not isinstance(content, str):
            return _invalid_arguments(tool_name, "'content' must be a string.")
        if not isinstance(create_parents, bool):
            return _invalid_arguments(tool_name, "'create_parents' must be a boolean.")
        if not isinstance(overwrite, bool):
            return _invalid_arguments(tool_name, "'overwrite' must be a boolean.")

        resolved = self._resolve_edit_path(tool_name, arguments.get("path"))
        if isinstance(resolved, ToolResult):
            return resolved
        file_path, location = resolved

        encoded = _encode_content(tool_name, content, location)
        if isinstance(encoded, ToolResult):
            return encoded

        parent = file_path.parent
        if not parent.exists():
            if not create_parents:
                return _failure(
                    tool_name,
                    "parent_not_found",
                    "Parent directory does not exist; set 'create_parents' to true to create it.",
                    location=location,
                )
            try:
                parent.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                return _io_failure(tool_name, "directory_create_error", "Could not create parent directory", location, exc)
        elif not parent.is_dir():
            return _failure(tool_name, "not_a_directory", "Parent path is not a directory.", location=location)

        existed = file_path.exists()
        if existed and not file_path.is_file():
            return _failure(tool_name, "not_a_file", "Target path is not a file.", location=location)
        if existed and not overwrite:
            return _failure(
                tool_name,
                "file_exists",
                "Target file already exists and 'overwrite' is false.",
                location=location,
            )

        previous_content = None
        if existed:
            previous_content = self._read_text(tool_name, file_path, location)
            if isinstance(previous_content, ToolResult):
                return previous_content

        before_sha256 = _sha256(previous_content.encode("utf-8")) if previous_content is not None else None
        after_sha256 = _sha256(encoded)
        if previous_content == content:
            return ToolResult(
                success=True,
                data={
                    "path": location,
                    "operation": "unchanged",
                    "created": False,
                    "overwritten": False,
                    "changed": False,
                    "bytes_written": 0,
                    "before_sha256": before_sha256,
                    "after_sha256": after_sha256,
                },
                summary=f"File already contains the requested content: {location}.",
                provenance=[
                    Provenance(
                        source=tool_name,
                        location=location,
                        metadata={"operation": "unchanged", "content_sha256": after_sha256},
                    )
                ],
            )

        write_failure = self._write_text(tool_name, file_path, location, content)
        if write_failure:
            return write_failure

        operation = "overwrite" if existed else "create"
        action = "Overwrote" if existed else "Created"
        return ToolResult(
            success=True,
            data={
                "path": location,
                "operation": operation,
                "created": not existed,
                "overwritten": existed,
                "changed": True,
                "bytes_written": len(encoded),
                "before_sha256": before_sha256,
                "after_sha256": after_sha256,
            },
            summary=f"{action} UTF-8 file {location} ({len(encoded)} byte(s)).",
            provenance=[
                Provenance(
                    source=tool_name,
                    location=location,
                    metadata={"operation": operation, "content_sha256": after_sha256},
                )
            ],
        )

    def apply_patch(self, arguments: Mapping[str, Any]) -> ToolResult:
        """Replace one exact, uniquely occurring text fragment in an existing UTF-8 file."""
        tool_name = "apply_patch"
        invalid = _reject_unknown_arguments(tool_name, arguments, {"path", "old_text", "new_text"})
        if invalid:
            return invalid

        old_text = arguments.get("old_text")
        new_text = arguments.get("new_text")
        if not isinstance(old_text, str) or not old_text:
            return _invalid_arguments(tool_name, "'old_text' must be a non-empty string.")
        if not isinstance(new_text, str):
            return _invalid_arguments(tool_name, "'new_text' must be a string.")
        if old_text == new_text:
            return _invalid_arguments(tool_name, "'old_text' and 'new_text' must differ.")

        resolved = self._resolve_edit_path(tool_name, arguments.get("path"))
        if isinstance(resolved, ToolResult):
            return resolved
        file_path, location = resolved
        if not file_path.exists():
            return _failure(tool_name, "path_not_found", "Target file does not exist.", location=location)
        if not file_path.is_file():
            return _failure(tool_name, "not_a_file", "Target path is not a file.", location=location)

        content = self._read_text(tool_name, file_path, location)
        if isinstance(content, ToolResult):
            return content

        occurrences = content.count(old_text)
        if occurrences == 0:
            return _failure(
                tool_name,
                "patch_target_not_found",
                "The exact 'old_text' fragment was not found.",
                location=location,
                details={"occurrences": 0},
            )
        if occurrences > 1:
            return _failure(
                tool_name,
                "patch_conflict",
                "The exact 'old_text' fragment is not unique.",
                location=location,
                details={"occurrences": occurrences},
            )

        updated_content = content.replace(old_text, new_text, 1)
        encoded = _encode_content(tool_name, updated_content, location)
        if isinstance(encoded, ToolResult):
            return encoded

        write_failure = self._write_text(tool_name, file_path, location, updated_content)
        if write_failure:
            return write_failure

        before_sha256 = _sha256(content.encode("utf-8"))
        after_sha256 = _sha256(encoded)
        return ToolResult(
            success=True,
            data={
                "path": location,
                "operation": "replace",
                "replacements": 1,
                "old_text": old_text,
                "new_text": new_text,
                "before_sha256": before_sha256,
                "after_sha256": after_sha256,
            },
            summary=f"Replaced one exact text fragment in {location}.",
            provenance=[
                Provenance(
                    source=tool_name,
                    location=location,
                    metadata={"operation": "replace", "before_sha256": before_sha256, "after_sha256": after_sha256},
                )
            ],
        )

    def _resolve_edit_path(self, tool_name: str, raw_path: Any) -> tuple[Path, str] | ToolResult:
        if not isinstance(raw_path, str) or not raw_path:
            return _invalid_arguments(tool_name, "'path' must be a non-empty relative path.")

        relative_path = Path(raw_path)
        if relative_path.is_absolute() or relative_path.drive:
            return _failure(
                tool_name,
                "absolute_path_not_allowed",
                "Editing paths must be relative to the configured repository root.",
                location=raw_path,
            )
        if ".." in relative_path.parts:
            return _failure(
                tool_name,
                "path_outside_repository",
                "Editing paths must not contain '..'.",
                location=raw_path,
            )

        candidate = self.repository_root / relative_path
        current = self.repository_root
        try:
            for part in relative_path.parts:
                if part in ("", "."):
                    continue
                current /= part
                if current.is_symlink():
                    component = current.relative_to(self.repository_root).as_posix()
                    return _failure(
                        tool_name,
                        "symlink_not_allowed",
                        "Editing through symlink path components is not allowed.",
                        location=raw_path,
                        details={"symlink": component},
                    )

            resolved = candidate.resolve()
            relative = resolved.relative_to(self.repository_root)
        except ValueError:
            return _failure(
                tool_name,
                "path_outside_repository",
                "Path must remain inside the configured repository root.",
                location=raw_path,
            )
        except OSError as exc:
            return _io_failure(tool_name, "path_resolution_error", "Could not resolve path", raw_path, exc)

        location = relative.as_posix() if relative.parts else "."
        return resolved, location

    def _read_text(self, tool_name: str, file_path: Path, location: str) -> str | ToolResult:
        try:
            return _read_utf8(file_path)
        except UnicodeDecodeError as exc:
            return _failure(
                tool_name,
                "file_decode_error",
                "File is not valid UTF-8.",
                location=location,
                details={"start": exc.start, "end": exc.end},
            )
        except OSError as exc:
            return _io_failure(tool_name, "file_read_error", "Could not read file", location, exc)

    def _write_text(self, tool_name: str, file_path: Path, location: str, content: str) -> ToolResult | None:
        try:
            _write_utf8(file_path, content)
        except UnicodeEncodeError as exc:
            return _failure(
                tool_name,
                "file_encode_error",
                "Content cannot be encoded as UTF-8.",
                location=location,
                details={"start": exc.start, "end": exc.end},
            )
        except OSError as exc:
            return _io_failure(tool_name, "file_write_error", "Could not write file", location, exc)
        return None


def register_editing_tools(
    registry: ToolRegistry,
    repository_root: str | Path,
) -> EditingTools:
    """Create and atomically register both editing tools."""
    tools = EditingTools(repository_root)
    tools.register(registry)
    return tools


def _read_utf8(path: Path) -> str:
    with path.open("r", encoding="utf-8", newline="") as stream:
        return stream.read()


def _write_utf8(path: Path, content: str) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        stream.write(content)


def _encode_content(tool_name: str, content: str, location: str) -> bytes | ToolResult:
    try:
        return content.encode("utf-8")
    except UnicodeEncodeError as exc:
        return _failure(
            tool_name,
            "file_encode_error",
            "Content cannot be encoded as UTF-8.",
            location=location,
            details={"start": exc.start, "end": exc.end},
        )


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
    return _failure(tool_name, "invalid_arguments", message)


def _io_failure(
    tool_name: str,
    code: str,
    message: str,
    location: str,
    error: OSError,
) -> ToolResult:
    return _failure(
        tool_name,
        code,
        f"{message}: {error}",
        location=location,
        details={"exception_type": type(error).__name__, "error": str(error)},
    )


def _failure(
    tool_name: str,
    code: str,
    message: str,
    *,
    location: str | None = None,
    details: dict[str, Any] | None = None,
) -> ToolResult:
    return ToolResult(
        success=False,
        summary=message,
        diagnostics=[Diagnostic(code=code, message=message, location=location, details=details or {})],
        provenance=[Provenance(source=tool_name, location=location)],
    )


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


__all__ = ["EditingTools", "register_editing_tools"]
