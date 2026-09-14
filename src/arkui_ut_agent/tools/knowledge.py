"""Thin wrapper around the ArkUI Ace Engine ``docs/kb_search.py`` CLI."""

import os
import re
import subprocess
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from arkui_ut_agent.tools.contracts import Diagnostic, Provenance, ToolResult
from arkui_ut_agent.tools.registry import ToolRegistry

ProcessRunner = Callable[..., subprocess.CompletedProcess[str]]
_TOOL_NAMES = ("kb_search",)
_SCRIPT_LOCATION = "docs/kb_search.py"
_REGISTRY_LOCATION = "docs/context_registry.json"
_SUMMARY_PATTERN = re.compile(r"^找到 (?P<total>\d+) 个匹配条目(?:（显示前 (?P<shown>\d+)，用 --all 查看全部）)?:$")
_ENTRY_PATTERN = re.compile(
    r"^--- \[(?P<index>\d+)\] (?P<name>.*) \((?P<name_cn>.*)\) \[score:(?P<score>\d+)\] ---$"
)


class ArkuiKbEntry(BaseModel):
    """Fields emitted by the real ArkUI ``kb_search.py --detail`` format."""

    model_config = ConfigDict(extra="forbid")

    name: str
    name_cn: str = ""
    category: str = ""
    type: str = ""
    kb_path: str
    spec_domain: str | None = None
    func_id: str | None = None
    source_paths: dict[str, str] = Field(default_factory=dict)
    api_paths: dict[str, str] = Field(default_factory=dict)
    test_paths: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    aliases: list[str] = Field(default_factory=list)
    score: int
    detail: str


class ArkuiKbSearchResponse(BaseModel):
    """Backend-neutral result passed from a KB backend into the Tool wrapper."""

    model_config = ConfigDict(extra="forbid")

    results: list[ArkuiKbEntry] = Field(default_factory=list)
    total_count: int = 0
    truncated: bool = False


class ArkuiKbBackendError(RuntimeError):
    """Structured failure raised by a KB backend and normalized by the Tool."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        output: str = "",
        stderr: str = "",
        returncode: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.output = output
        self.stderr = stderr
        self.returncode = returncode


class ArkuiKbBackend(Protocol):
    """Replaceable boundary for an ArkUI KB search implementation."""

    name: str

    def search(self, query: str) -> ArkuiKbSearchResponse: ...


class LocalArkuiKbBackend:
    """Invoke the KB CLI from one configured ArkUI Ace Engine checkout."""

    name = "local_arkui_kb"

    def __init__(
        self,
        arkui_ace_engine_root: str | Path | None,
        *,
        runner: ProcessRunner | None = None,
        timeout: float = 30,
        python_executable: str | None = None,
    ) -> None:
        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= 0:
            raise ValueError("ArkUI KB timeout must be greater than zero.")
        self._root = Path(arkui_ace_engine_root).expanduser() if arkui_ace_engine_root else None
        self._runner = runner if runner is not None else subprocess.run
        self._timeout = timeout
        self._python_executable = python_executable or sys.executable

    def search(self, query: str) -> ArkuiKbSearchResponse:
        """Run the checkout-owned search and minimally normalize its detail output."""
        root = self._root
        if root is None:
            raise ArkuiKbBackendError("kb_unavailable", "ArkUI Ace Engine root is not configured.")
        if not root.is_dir():
            raise ArkuiKbBackendError("kb_root_not_found", "Configured ArkUI Ace Engine root does not exist.")

        script = root / _SCRIPT_LOCATION
        if not script.is_file():
            raise ArkuiKbBackendError(
                "kb_unavailable",
                "Configured ArkUI checkout does not contain docs/kb_search.py.",
            )

        command = [self._python_executable, str(script), query, "--detail"]
        try:
            completed = self._runner(
                command,
                cwd=root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                timeout=self._timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise ArkuiKbBackendError(
                "kb_search_timeout",
                f"ArkUI KB search exceeded the {self._timeout:g} second timeout.",
                output=_exception_text(exc.stdout),
                stderr=_exception_text(exc.stderr),
            ) from exc
        except OSError as exc:
            raise ArkuiKbBackendError(
                "kb_search_failed",
                f"Could not execute ArkUI KB search: {exc}",
            ) from exc

        output = completed.stdout or ""
        stderr = completed.stderr or ""
        if completed.returncode != 0:
            raise ArkuiKbBackendError(
                "kb_search_failed",
                stderr.strip() or f"ArkUI KB search exited with code {completed.returncode}.",
                output=output,
                stderr=stderr,
                returncode=completed.returncode,
            )

        try:
            return _parse_detail_output(output)
        except ValueError as exc:
            raise ArkuiKbBackendError(
                "invalid_kb_output",
                f"ArkUI KB search returned an unexpected format: {exc}",
                output=output,
                stderr=stderr,
                returncode=completed.returncode,
            ) from exc


class KnowledgeTools:
    """Expose KB navigation through the shared ToolResult contract."""

    def __init__(self, backend: ArkuiKbBackend | None = None) -> None:
        self._backend = backend

    def register(self, registry: ToolRegistry) -> None:
        """Register ``kb_search`` atomically with the existing registry."""
        duplicate_names = sorted(set(_TOOL_NAMES).intersection(registry.names))
        if duplicate_names:
            names = ", ".join(duplicate_names)
            raise ValueError(f"Knowledge tool names are already registered: {names}.")
        registry.register("kb_search", self.kb_search)

    def kb_search(self, arguments: Mapping[str, Any]) -> ToolResult:
        """Search the configured ArkUI KB without interpreting it as source truth."""
        unknown = sorted(set(arguments).difference({"query"}), key=str)
        if unknown:
            return _failure(
                "invalid_arguments",
                f"Unknown argument(s): {', '.join(map(str, unknown))}.",
                query=None,
            )

        query = arguments.get("query")
        if not isinstance(query, str) or not query.strip():
            return _failure("invalid_arguments", "'query' must be a non-empty string.", query=None)

        backend = self._backend
        if backend is None:
            return _failure("kb_unavailable", "ArkUI KB backend is not configured.", query=query)
        backend_name = _backend_name(backend)

        try:
            response = backend.search(query)
        except ArkuiKbBackendError as exc:
            return _failure(
                exc.code,
                str(exc),
                query=query,
                backend_name=backend_name,
                output=exc.output,
                stderr=exc.stderr,
                returncode=exc.returncode,
            )
        except Exception as exc:
            return _failure(
                "kb_search_failed",
                f"ArkUI KB backend raised {type(exc).__name__}: {exc}",
                query=query,
                backend_name=backend_name,
                details={"exception_type": type(exc).__name__},
            )

        if not _valid_response(response):
            return _failure(
                "invalid_backend_result",
                "ArkUI KB backend returned an invalid result.",
                query=query,
                backend_name=backend_name,
                details={"returned_type": type(response).__name__},
            )

        results = [entry.model_dump(mode="json") for entry in response.results]
        provenance = [
            Provenance(
                source="arkui_kb",
                location=entry.kb_path,
                metadata={
                    "backend": backend_name,
                    "identity": entry.name,
                    "script": _SCRIPT_LOCATION,
                    "fact_scope": "domain_navigation",
                },
            )
            for entry in response.results
        ]
        if not provenance:
            provenance.append(_query_provenance(query, backend_name))

        return ToolResult(
            success=True,
            data={
                "query": query,
                "results": results,
                "count": len(results),
                "total_count": response.total_count,
                "truncated": response.truncated,
            },
            summary=f"ArkUI KB search returned {len(results)} of {response.total_count} result(s).",
            provenance=provenance,
        )


def register_knowledge_tools(
    registry: ToolRegistry,
    *,
    arkui_ace_engine_root: str | Path | None = None,
    backend: ArkuiKbBackend | None = None,
    **backend_kwargs: Any,
) -> KnowledgeTools:
    """Register the tool using an injected backend or the configured local checkout."""
    if backend is not None and backend_kwargs:
        raise ValueError("Backend options cannot be used with an injected ArkUI KB backend.")
    resolved_backend = backend if backend is not None else LocalArkuiKbBackend(arkui_ace_engine_root, **backend_kwargs)
    tools = KnowledgeTools(resolved_backend)
    tools.register(registry)
    return tools


def _parse_detail_output(output: str) -> ArkuiKbSearchResponse:
    text = output.strip()
    if text.startswith("未找到匹配 "):
        return ArkuiKbSearchResponse()
    if not text:
        raise ValueError("output is empty")

    blocks = re.split(r"\n\s*\n", text)
    summary = _SUMMARY_PATTERN.fullmatch(blocks[0].strip())
    if summary is None:
        raise ValueError("result summary is missing")
    total_count = int(summary.group("total"))
    entries = [_parse_entry(block) for block in blocks[1:] if block.strip()]
    if not entries or len(entries) > total_count:
        raise ValueError("result count does not match the summary")
    shown_count = summary.group("shown")
    if shown_count is not None and int(shown_count) != len(entries):
        raise ValueError("shown result count does not match the summary")
    return ArkuiKbSearchResponse(
        results=entries,
        total_count=total_count,
        truncated=len(entries) < total_count,
    )


def _parse_entry(block: str) -> ArkuiKbEntry:
    lines = [line.strip() for line in block.splitlines() if line.strip()]
    if not lines:
        raise ValueError("empty result entry")
    header = _ENTRY_PATTERN.fullmatch(lines[0])
    if header is None:
        raise ValueError("result entry header is invalid")

    fields = _detail_fields(lines[1:])
    classification = fields.get("分类", "")
    category, separator, entry_type = classification.partition(" | 类型: ")
    if not separator:
        raise ValueError("result classification is missing")
    kb_path = fields.get("知识库", "")
    if not kb_path:
        raise ValueError("result KB path is missing")

    func_id = None
    spec_domain = None
    if spec := fields.get("Spec"):
        func_id, separator, spec_domain = spec.partition(" -> ")
        if not separator:
            raise ValueError("result Spec field is invalid")

    return ArkuiKbEntry(
        name=header.group("name"),
        name_cn=header.group("name_cn"),
        category=category,
        type=entry_type,
        kb_path=kb_path,
        spec_domain=spec_domain,
        func_id=func_id,
        source_paths=_parse_path_mapping(fields.get("源码", "")),
        api_paths=_parse_path_mapping(fields.get("API", "")),
        test_paths=_parse_list(fields.get("测试", "")),
        keywords=_parse_list(fields.get("关键词", ""), remove_ellipsis=True),
        aliases=_parse_list(fields.get("别名", "")),
        score=int(header.group("score")),
        detail=block.strip(),
    )


def _detail_fields(lines: list[str]) -> dict[str, str]:
    fields = {}
    for line in lines:
        key, separator, value = line.partition(": ")
        if separator:
            fields[key] = value
    return fields


def _parse_path_mapping(value: str) -> dict[str, str]:
    if not value:
        return {}
    result = {}
    for item in value.split(", "):
        key, separator, path = item.partition(": ")
        if not separator:
            raise ValueError("path mapping is invalid")
        result[key] = path
    return result


def _parse_list(value: str, *, remove_ellipsis: bool = False) -> list[str]:
    if not value:
        return []
    if remove_ellipsis and value.endswith("..."):
        value = value[:-3]
    return value.split(", ")


def _valid_response(response: Any) -> bool:
    if not isinstance(response, ArkuiKbSearchResponse):
        return False
    if not isinstance(response.results, list) or not isinstance(response.total_count, int):
        return False
    if isinstance(response.total_count, bool) or response.total_count < len(response.results):
        return False
    if not isinstance(response.truncated, bool) or response.truncated != (len(response.results) < response.total_count):
        return False
    try:
        return all(
            isinstance(entry, ArkuiKbEntry) and ArkuiKbEntry.model_validate(entry.model_dump()) == entry
            for entry in response.results
        )
    except (TypeError, ValueError, ValidationError):
        return False


def _failure(
    code: str,
    message: str,
    *,
    query: str | None,
    backend_name: str = "",
    output: str = "",
    stderr: str = "",
    returncode: int | None = None,
    details: dict[str, Any] | None = None,
) -> ToolResult:
    diagnostic_details = {
        "returncode": returncode,
        "output": output,
        "stderr": stderr,
        **(details or {}),
    }
    return ToolResult(
        success=False,
        data={"query": query, "results": [], "count": 0, "total_count": 0, "truncated": False},
        summary=message,
        diagnostics=[Diagnostic(code=code, message=message, details=diagnostic_details)],
        provenance=[_query_provenance(query, backend_name)],
    )


def _query_provenance(query: str | None, backend_name: str) -> Provenance:
    return Provenance(
        source="arkui_kb",
        location=_REGISTRY_LOCATION,
        metadata={
            "backend": backend_name,
            "query": query,
            "script": _SCRIPT_LOCATION,
            "fact_scope": "domain_navigation",
        },
    )


def _backend_name(backend: ArkuiKbBackend) -> str:
    name = getattr(backend, "name", "")
    return name if isinstance(name, str) and name.strip() else type(backend).__name__


def _exception_text(value: str | bytes | None) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value or ""


__all__ = [
    "ArkuiKbBackend",
    "ArkuiKbBackendError",
    "ArkuiKbEntry",
    "ArkuiKbSearchResponse",
    "KnowledgeTools",
    "LocalArkuiKbBackend",
    "register_knowledge_tools",
]
