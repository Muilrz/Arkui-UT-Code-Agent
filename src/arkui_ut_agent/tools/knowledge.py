"""Thin wrapper around the repository-owned ``docs/kb_search.py`` CLI."""

import os
import re
import subprocess
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from arkui_ut_agent.tools.contracts import Diagnostic, Provenance, ToolResult
from arkui_ut_agent.tools.registry import ToolRegistry

ProcessRunner = Callable[..., subprocess.CompletedProcess[str]]
_TOOL_NAMES = ("kb_search",)
_SCRIPT_LOCATION = "docs/kb_search.py"
_REGISTRY_LOCATION = "docs/context_registry.json"
_SUMMARY_PATTERN = re.compile(r"^找到 (?P<total>\d+) 个匹配条目(?:（显示前 (?P<shown>\d+)，用 --all 查看全部）)?:$")
_DETAIL_ENTRY_PATTERN = re.compile(
    r"^--- \[(?P<index>\d+)\] (?P<name>.*) \((?P<name_cn>.*)\) \[score:(?P<score>\d+)\] ---$"
)
_NUMBERED_ENTRY_PATTERN = re.compile(r"^\s*(?P<index>\d+)\.\s+(?P<entry>.*)$")
_CATEGORY_PATTERN = re.compile(r"^\s*(?P<category>.+) \((?P<count>\d+) 个\)$")


class ArkuiKbRequest(BaseModel):
    """Validated options mapped one-for-one to the official KB CLI."""

    model_config = ConfigDict(extra="forbid")

    query: str | None = None
    detail: bool = False
    all_results: bool = False
    field: str | None = None
    category: str | None = None
    list_categories: bool = False
    list_all: bool = False

    @property
    def mode(self) -> Literal["search", "list_categories", "list_all"]:
        if self.list_categories:
            return "list_categories"
        if self.list_all:
            return "list_all"
        return "search"


class ArkuiKbEntry(BaseModel):
    """Fields present in compact, detail, or list-all output from the real CLI."""

    model_config = ConfigDict(extra="forbid")

    name: str
    name_cn: str = ""
    category: str = ""
    type: str = ""
    kb_path: str | None = None
    spec_domain: str | None = None
    func_id: str | None = None
    source_paths: dict[str, str] = Field(default_factory=dict)
    api_paths: dict[str, str] = Field(default_factory=dict)
    test_paths: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    aliases: list[str] = Field(default_factory=list)
    score: int | None = None
    detail: str


class ArkuiKbCategory(BaseModel):
    """One category/count row emitted by ``--list-categories``."""

    model_config = ConfigDict(extra="forbid")

    category: str
    count: int
    detail: str


class ArkuiKbSearchResponse(BaseModel):
    """Normalized output passed from a KB backend into the Tool wrapper."""

    model_config = ConfigDict(extra="forbid")

    mode: Literal["search", "list_categories", "list_all"] = "search"
    results: list[ArkuiKbEntry | ArkuiKbCategory] = Field(default_factory=list)
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
    """Replaceable boundary for an ArkUI KB CLI implementation."""

    name: str

    def search(self, request: ArkuiKbRequest) -> ArkuiKbSearchResponse: ...


class LocalArkuiKbBackend:
    """Invoke ``docs/kb_search.py`` inside one configured repository root."""

    name = "local_arkui_kb"

    def __init__(
        self,
        repository_root: str | Path,
        *,
        runner: ProcessRunner | None = None,
        timeout: float = 30,
    ) -> None:
        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= 0:
            raise ValueError("ArkUI KB timeout must be greater than zero.")
        self.repository_root = Path(repository_root).expanduser()
        self._runner = runner if runner is not None else subprocess.run
        self._timeout = timeout

    def search(self, request: ArkuiKbRequest) -> ArkuiKbSearchResponse:
        """Run the official CLI without duplicating its search or ranking logic."""
        root = self.repository_root
        if not root.is_dir():
            raise ArkuiKbBackendError("kb_root_not_found", "Configured repository root does not exist.")

        script = root / _SCRIPT_LOCATION
        if not script.is_file():
            raise ArkuiKbBackendError(
                "kb_unavailable",
                "Configured repository does not contain docs/kb_search.py.",
            )

        command = _build_command(script, request)
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
            return _parse_output(output, request)
        except ValueError as exc:
            raise ArkuiKbBackendError(
                "invalid_kb_output",
                f"ArkUI KB search returned an unexpected format: {exc}",
                output=output,
                stderr=stderr,
                returncode=completed.returncode,
            ) from exc


class KnowledgeTools:
    """Expose repository-local KB navigation through the shared ToolResult contract."""

    def __init__(self, backend: ArkuiKbBackend) -> None:
        self._backend = backend

    def register(self, registry: ToolRegistry) -> None:
        """Register ``kb_search`` atomically with the existing registry."""
        duplicate_names = sorted(set(_TOOL_NAMES).intersection(registry.names))
        if duplicate_names:
            names = ", ".join(duplicate_names)
            raise ValueError(f"Knowledge tool names are already registered: {names}.")
        registry.register("kb_search", self.kb_search)

    def kb_search(self, arguments: Mapping[str, Any]) -> ToolResult:
        """Map explicit arguments to the official CLI and normalize its facts."""
        request = _validate_arguments(arguments)
        if isinstance(request, ToolResult):
            return request
        backend_name = _backend_name(self._backend)

        try:
            response = self._backend.search(request)
        except ArkuiKbBackendError as exc:
            return _failure(
                exc.code,
                str(exc),
                request=request,
                backend_name=backend_name,
                output=exc.output,
                stderr=exc.stderr,
                returncode=exc.returncode,
            )
        except Exception as exc:
            return _failure(
                "kb_search_failed",
                f"ArkUI KB backend raised {type(exc).__name__}: {exc}",
                request=request,
                backend_name=backend_name,
                details={"exception_type": type(exc).__name__},
            )

        if not _valid_response(response, request.mode):
            return _failure(
                "invalid_backend_result",
                "ArkUI KB backend returned an invalid result.",
                request=request,
                backend_name=backend_name,
                details={"returned_type": type(response).__name__},
            )

        results = [item.model_dump(mode="json") for item in response.results]
        provenance = [_item_provenance(item, backend_name) for item in response.results]
        if not provenance:
            provenance.append(_request_provenance(request, backend_name))

        return ToolResult(
            success=True,
            data={
                "query": request.query,
                "mode": response.mode,
                "options": _public_options(request),
                "results": results,
                "count": len(results),
                "total_count": response.total_count,
                "truncated": response.truncated,
            },
            summary=f"ArkUI KB {response.mode} returned {len(results)} of {response.total_count} result(s).",
            provenance=provenance,
        )


def register_knowledge_tools(
    registry: ToolRegistry,
    repository_root: str | Path,
    *,
    backend: ArkuiKbBackend | None = None,
    **backend_kwargs: Any,
) -> KnowledgeTools:
    """Register the KB tool against the same repository root used by other tools."""
    if backend is not None and backend_kwargs:
        raise ValueError("Backend options cannot be used with an injected ArkUI KB backend.")
    resolved_backend = backend if backend is not None else LocalArkuiKbBackend(repository_root, **backend_kwargs)
    tools = KnowledgeTools(resolved_backend)
    tools.register(registry)
    return tools


def _validate_arguments(arguments: Mapping[str, Any]) -> ArkuiKbRequest | ToolResult:
    allowed = {"query", "detail", "all", "field", "category", "list_categories", "list_all"}
    unknown = sorted(set(arguments).difference(allowed), key=str)
    if unknown:
        return _invalid_arguments(f"Unknown argument(s): {', '.join(map(str, unknown))}.")

    for name in ("detail", "all", "list_categories", "list_all"):
        value = arguments.get(name, False)
        if not isinstance(value, bool):
            return _invalid_arguments(f"'{name}' must be a boolean.")
    for name in ("field", "category"):
        value = arguments.get(name)
        if value is not None and (not isinstance(value, str) or not value.strip()):
            return _invalid_arguments(f"'{name}' must be a non-empty string when provided.")

    query = arguments.get("query")
    if query is not None and (not isinstance(query, str) or not query.strip()):
        return _invalid_arguments("'query' must be a non-empty string when provided.")

    list_categories = arguments.get("list_categories", False)
    list_all = arguments.get("list_all", False)
    if list_categories and list_all:
        return _invalid_arguments("'list_categories' and 'list_all' are mutually exclusive.")
    if list_categories or list_all:
        conflicting = query is not None or any(
            (
                arguments.get("detail", False),
                arguments.get("all", False),
                arguments.get("field") is not None,
                arguments.get("category") is not None,
            )
        )
        if conflicting:
            return _invalid_arguments("List modes cannot be combined with query search options.")
    elif query is None:
        return _invalid_arguments("'query' is required unless a list mode is selected.")

    return ArkuiKbRequest(
        query=query,
        detail=arguments.get("detail", False),
        all_results=arguments.get("all", False),
        field=arguments.get("field"),
        category=arguments.get("category"),
        list_categories=list_categories,
        list_all=list_all,
    )


def _build_command(script: Path, request: ArkuiKbRequest) -> list[str]:
    command = [sys.executable, str(script)]
    if request.query is not None:
        command.append(request.query)
    if request.detail:
        command.append("--detail")
    if request.all_results:
        command.append("--all")
    if request.field is not None:
        command.extend(["--field", request.field])
    if request.category is not None:
        command.extend(["--category", request.category])
    if request.list_categories:
        command.append("--list-categories")
    if request.list_all:
        command.append("--list-all")
    return command


def _parse_output(output: str, request: ArkuiKbRequest) -> ArkuiKbSearchResponse:
    if request.list_categories:
        return _parse_categories(output)
    if request.list_all:
        return _parse_all_entries(output)
    return _parse_search(output, detail=request.detail)


def _parse_search(output: str, *, detail: bool) -> ArkuiKbSearchResponse:
    text = output.strip()
    if text.startswith("未找到匹配 "):
        return ArkuiKbSearchResponse()
    if not text:
        raise ValueError("output is empty")

    blocks = re.split(r"\n\s*\n", text) if detail else text.splitlines()
    summary = _SUMMARY_PATTERN.fullmatch(blocks[0].strip())
    if summary is None:
        raise ValueError("result summary is missing")
    total_count = int(summary.group("total"))
    entries = [
        _parse_detail_entry(block) if detail else _parse_compact_entry(block)
        for block in blocks[1:]
        if block.strip()
    ]
    _validate_summary_count(summary, total_count, len(entries))
    return ArkuiKbSearchResponse(
        results=entries,
        total_count=total_count,
        truncated=len(entries) < total_count,
    )


def _parse_compact_entry(line: str) -> ArkuiKbEntry:
    numbered = _NUMBERED_ENTRY_PATTERN.fullmatch(line)
    if numbered is None:
        raise ValueError("compact result entry is invalid")
    identity, separator, kb_path = numbered.group("entry").partition(" -> ")
    if not separator or not kb_path:
        raise ValueError("compact result KB path is missing")
    name, name_cn = _split_identity(identity)
    return ArkuiKbEntry(name=name, name_cn=name_cn, kb_path=kb_path, detail=line.strip())


def _parse_detail_entry(block: str) -> ArkuiKbEntry:
    lines = [line.strip() for line in block.splitlines() if line.strip()]
    if not lines:
        raise ValueError("empty detail result entry")
    header = _DETAIL_ENTRY_PATTERN.fullmatch(lines[0])
    if header is None:
        raise ValueError("detail result entry header is invalid")

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


def _parse_categories(output: str) -> ArkuiKbSearchResponse:
    lines = [line for line in output.splitlines() if line.strip()]
    if not lines:
        raise ValueError("category list is empty")
    results = []
    for line in lines:
        match = _CATEGORY_PATTERN.fullmatch(line)
        if match is None:
            raise ValueError("category row is invalid")
        results.append(
            ArkuiKbCategory(
                category=match.group("category"),
                count=int(match.group("count")),
                detail=line.strip(),
            )
        )
    return ArkuiKbSearchResponse(mode="list_categories", results=results, total_count=len(results))


def _parse_all_entries(output: str) -> ArkuiKbSearchResponse:
    lines = [line for line in output.splitlines() if line.strip()]
    if not lines:
        raise ValueError("KB entry list is empty")
    results = []
    for line in lines:
        numbered = _NUMBERED_ENTRY_PATTERN.fullmatch(line)
        if numbered is None:
            raise ValueError("list-all row is invalid")
        columns = [column.strip() for column in numbered.group("entry").split("|")]
        if len(columns) != 3 or not columns[0]:
            raise ValueError("list-all columns are invalid")
        results.append(
            ArkuiKbEntry(
                name=columns[0],
                name_cn=columns[1],
                category=columns[2],
                detail=line.strip(),
            )
        )
    return ArkuiKbSearchResponse(mode="list_all", results=results, total_count=len(results))


def _validate_summary_count(summary: re.Match[str], total_count: int, entry_count: int) -> None:
    if not entry_count or entry_count > total_count:
        raise ValueError("result count does not match the summary")
    shown_count = summary.group("shown")
    if shown_count is not None and int(shown_count) != entry_count:
        raise ValueError("shown result count does not match the summary")
    if shown_count is None and entry_count != total_count:
        raise ValueError("result count does not match the summary")


def _split_identity(identity: str) -> tuple[str, str]:
    if identity.endswith(")"):
        name, separator, name_cn = identity.rpartition(" (")
        if separator and name:
            return name, name_cn[:-1]
    if not identity:
        raise ValueError("result identity is missing")
    return identity, ""


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


def _valid_response(response: Any, expected_mode: str) -> bool:
    if not isinstance(response, ArkuiKbSearchResponse) or response.mode != expected_mode:
        return False
    if not isinstance(response.results, list) or not isinstance(response.total_count, int):
        return False
    if isinstance(response.total_count, bool) or response.total_count < len(response.results):
        return False
    if not isinstance(response.truncated, bool) or response.truncated != (len(response.results) < response.total_count):
        return False
    expected_type = ArkuiKbCategory if expected_mode == "list_categories" else ArkuiKbEntry
    try:
        return all(
            isinstance(item, expected_type) and expected_type.model_validate(item.model_dump()) == item
            for item in response.results
        )
    except (TypeError, ValueError, ValidationError):
        return False


def _invalid_arguments(message: str) -> ToolResult:
    return _failure("invalid_arguments", message, request=None)


def _failure(
    code: str,
    message: str,
    *,
    request: ArkuiKbRequest | None,
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
        data={
            "query": request.query if request else None,
            "mode": request.mode if request else "search",
            "options": _public_options(request) if request else {},
            "results": [],
            "count": 0,
            "total_count": 0,
            "truncated": False,
        },
        summary=message,
        diagnostics=[Diagnostic(code=code, message=message, details=diagnostic_details)],
        provenance=[_request_provenance(request, backend_name)],
    )


def _item_provenance(item: ArkuiKbEntry | ArkuiKbCategory, backend_name: str) -> Provenance:
    if isinstance(item, ArkuiKbCategory):
        identity = item.category
        location = _REGISTRY_LOCATION
    else:
        identity = item.name
        location = item.kb_path or _REGISTRY_LOCATION
    return Provenance(
        source="arkui_kb",
        location=location,
        metadata={
            "backend": backend_name,
            "identity": identity,
            "script": _SCRIPT_LOCATION,
            "fact_scope": "domain_navigation",
        },
    )


def _request_provenance(request: ArkuiKbRequest | None, backend_name: str) -> Provenance:
    return Provenance(
        source="arkui_kb",
        location=_REGISTRY_LOCATION,
        metadata={
            "backend": backend_name,
            "query": request.query if request else None,
            "mode": request.mode if request else "search",
            "script": _SCRIPT_LOCATION,
            "fact_scope": "domain_navigation",
        },
    )


def _public_options(request: ArkuiKbRequest) -> dict[str, Any]:
    return {
        "detail": request.detail,
        "all": request.all_results,
        "field": request.field,
        "category": request.category,
        "list_categories": request.list_categories,
        "list_all": request.list_all,
    }


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
    "ArkuiKbCategory",
    "ArkuiKbEntry",
    "ArkuiKbRequest",
    "ArkuiKbSearchResponse",
    "KnowledgeTools",
    "LocalArkuiKbBackend",
    "register_knowledge_tools",
]
