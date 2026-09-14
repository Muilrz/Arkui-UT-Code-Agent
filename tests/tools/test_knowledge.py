import os
import subprocess
import sys
from pathlib import Path

import pytest

from arkui_ut_agent.tools import (
    ArkuiKbBackendError,
    ArkuiKbCategory,
    ArkuiKbEntry,
    ArkuiKbRequest,
    ArkuiKbSearchResponse,
    KnowledgeTools,
    LocalArkuiKbBackend,
    ToolRegistry,
    register_knowledge_tools,
)

COMPACT_OUTPUT = """找到 2 个匹配条目:

   1. Button (按钮组件) -> docs/kb/components/basic/button.md
   2. ContentModifier (Form) (表单类组件自定义内容) -> docs/kb/capabilities/content-modifier-form.md
"""

TRUNCATED_COMPACT_OUTPUT = """找到 12 个匹配条目（显示前 2，用 --all 查看全部）:

   1. Button (按钮组件) -> docs/kb/components/basic/button.md
   2. ArcButton (圆弧按钮组件) -> docs/kb/components/selector/arc-button.md
"""

DETAIL_OUTPUT = """找到 1 个匹配条目:

--- [1] Button (按钮组件) [score:100] ---
  分类: basic | 类型: component
  知识库: docs/kb/components/basic/button.md
  Spec: 05-04-01 -> specs/05-ui-components/04-input-form-components/01-button
  源码: pattern: frameworks/core/components_ng/pattern/button/button_pattern.cpp, model: frameworks/core/components_ng/pattern/button/button_model_ng.cpp
  API: dynamic: <OH_ROOT>/interface/sdk-js/api/@internal/component/ets/button.d.ts
  测试: test/unittest/core/pattern/button/
  关键词: Button, 按钮, click
  别名: Button组件, 按钮
"""

NO_MATCH_OUTPUT = """未找到匹配 '__no_such_entry__' 的知识库条目。
提示: 使用 --list-all 查看所有条目，或使用 --list-categories 查看分类。
      使用 --field source_paths 按源码路径搜索。
"""

CATEGORIES_OUTPUT = """  basic (22 个)
  container (31 个)
"""

LIST_ALL_OUTPUT = """   1. Layout Framework     | 布局框架            | system
   2. Button               | 按钮组件            | basic
"""


class FakeBackend:
    name = "fake_arkui_kb"

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def search(self, request):
        self.calls.append(request)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def kb_entry(name="Button", *, detail="compact row"):
    return ArkuiKbEntry(
        name=name,
        name_cn="按钮组件",
        category="basic",
        type="component",
        kb_path="docs/kb/components/basic/button.md",
        spec_domain="specs/05-ui-components/04-input-form-components/01-button",
        func_id="05-04-01",
        source_paths={"pattern": "frameworks/core/components_ng/pattern/button/button_pattern.cpp"},
        api_paths={"dynamic": "<OH_ROOT>/interface/sdk-js/api/@internal/component/ets/button.d.ts"},
        test_paths=["test/unittest/core/pattern/button/"],
        keywords=["Button", "按钮"],
        aliases=["Button组件"],
        score=100,
        detail=detail,
    )


def search_response(entries=None, *, total_count=None, truncated=None):
    entries = entries if entries is not None else [kb_entry()]
    total_count = len(entries) if total_count is None else total_count
    truncated = len(entries) < total_count if truncated is None else truncated
    return ArkuiKbSearchResponse(results=entries, total_count=total_count, truncated=truncated)


def make_repository(tmp_path):
    root = tmp_path / "arkui_ace_engine"
    docs = root / "docs"
    docs.mkdir(parents=True)
    (docs / "kb_search.py").write_text("# fixture only\n", encoding="utf-8")
    return root


def completed(output="", *, returncode=0, stderr=""):
    return subprocess.CompletedProcess([sys.executable, "docs/kb_search.py"], returncode, output, stderr)


def assert_failure(result, code):
    assert result.success is False
    assert result.data["results"] == []
    assert result.diagnostics[0].code == code
    assert result.provenance[0].source == "arkui_kb"


def test_tool_success_preserves_results_options_and_kb_provenance():
    backend = FakeBackend([search_response()])

    result = KnowledgeTools(backend).kb_search({"query": "Button"})

    assert result.success is True
    assert backend.calls == [ArkuiKbRequest(query="Button")]
    assert result.data["query"] == "Button"
    assert result.data["mode"] == "search"
    assert result.data["options"] == {
        "detail": False,
        "all": False,
        "field": None,
        "category": None,
        "list_categories": False,
        "list_all": False,
    }
    assert result.data["results"][0]["source_paths"]["pattern"].endswith("button_pattern.cpp")
    assert result.provenance[0].location == "docs/kb/components/basic/button.md"
    assert result.provenance[0].metadata["fact_scope"] == "domain_navigation"


def test_tool_no_match_is_success_with_empty_results():
    result = KnowledgeTools(FakeBackend([search_response([])])).kb_search({"query": "no match"})

    assert result.success is True
    assert result.data["count"] == 0
    assert result.data["total_count"] == 0
    assert result.data["truncated"] is False
    assert result.provenance[0].location == "docs/context_registry.json"


def test_tool_normalizes_category_list_results():
    response = ArkuiKbSearchResponse(
        mode="list_categories",
        results=[ArkuiKbCategory(category="basic", count=22, detail="basic (22 个)")],
        total_count=1,
    )
    backend = FakeBackend([response])

    result = KnowledgeTools(backend).kb_search({"list_categories": True})

    assert result.success is True
    assert backend.calls == [ArkuiKbRequest(list_categories=True)]
    assert result.data["mode"] == "list_categories"
    assert result.data["results"] == [{"category": "basic", "count": 22, "detail": "basic (22 个)"}]


def test_tool_maps_explicit_search_options_to_backend_request():
    backend = FakeBackend([search_response()])

    result = KnowledgeTools(backend).kb_search(
        {
            "query": "Button",
            "detail": True,
            "all": True,
            "field": "name",
            "category": "basic",
        }
    )

    assert result.success is True
    assert backend.calls == [
        ArkuiKbRequest(
            query="Button",
            detail=True,
            all_results=True,
            field="name",
            category="basic",
        )
    ]


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"query": ""},
        {"query": "   "},
        {"query": 7},
        {"query": "Button", "detail": 1},
        {"query": "Button", "all": "yes"},
        {"query": "Button", "field": ""},
        {"query": "Button", "category": 1},
        {"list_categories": True, "list_all": True},
        {"list_categories": True, "query": "Button"},
        {"list_all": True, "detail": True},
        {"query": "Button", "limit": 3},
    ],
)
def test_invalid_arguments_are_structured_without_calling_backend(arguments):
    backend = FakeBackend([])

    result = KnowledgeTools(backend).kb_search(arguments)

    assert_failure(result, "invalid_arguments")
    assert backend.calls == []


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (ArkuiKbBackendError("kb_unavailable", "script missing"), "kb_unavailable"),
        (ArkuiKbBackendError("kb_root_not_found", "root missing"), "kb_root_not_found"),
        (
            ArkuiKbBackendError(
                "kb_search_failed",
                "process failed",
                output="partial",
                stderr="bad",
                returncode=2,
            ),
            "kb_search_failed",
        ),
        (ArkuiKbBackendError("invalid_kb_output", "bad format", output="unexpected"), "invalid_kb_output"),
    ],
)
def test_backend_failures_are_structured_and_preserve_execution_evidence(error, code):
    result = KnowledgeTools(FakeBackend([error])).kb_search({"query": "Button", "detail": True})

    assert_failure(result, code)
    assert result.data["options"]["detail"] is True
    assert result.diagnostics[0].details["output"] == error.output
    assert result.diagnostics[0].details["stderr"] == error.stderr
    assert result.diagnostics[0].details["returncode"] == error.returncode


def test_unexpected_backend_error_is_structured():
    result = KnowledgeTools(FakeBackend([RuntimeError("broken adapter")])).kb_search({"query": "Button"})

    assert_failure(result, "kb_search_failed")
    assert result.diagnostics[0].details["exception_type"] == "RuntimeError"


@pytest.mark.parametrize(
    "response",
    [
        {"results": []},
        ArkuiKbSearchResponse.model_construct(results=[{"name": "Button"}], total_count=1, truncated=False),
        ArkuiKbSearchResponse.model_construct(results=[], total_count=1, truncated=False),
        ArkuiKbSearchResponse(mode="list_all", results=[], total_count=0),
    ],
)
def test_invalid_backend_result_is_structured(response):
    result = KnowledgeTools(FakeBackend([response])).kb_search({"query": "Button"})

    assert_failure(result, "invalid_backend_result")


def test_registry_dispatch_normalizes_success_failure_and_provenance(tmp_path):
    backend = FakeBackend([search_response(), ArkuiKbBackendError("kb_unavailable", "offline")])
    registry = ToolRegistry()
    register_knowledge_tools(registry, tmp_path, backend=backend)

    success = registry.dispatch("kb_search", {"query": "Button"})
    failure = registry.dispatch("kb_search", {"query": "Text", "field": "name"})

    assert registry.names == ("kb_search",)
    assert success.tool_name == "kb_search"
    assert success.success is True
    assert success.provenance[0].location == "docs/kb/components/basic/button.md"
    assert failure.tool_name == "kb_search"
    assert failure.success is False
    assert failure.diagnostics[0].code == "kb_unavailable"
    assert failure.provenance[0].metadata["query"] == "Text"


def test_registration_is_atomic_on_name_collision(tmp_path):
    registry = ToolRegistry()
    registry.register("kb_search", lambda _arguments: None)

    with pytest.raises(ValueError, match="already registered"):
        register_knowledge_tools(registry, tmp_path, backend=FakeBackend([]))

    assert registry.names == ("kb_search",)


@pytest.mark.parametrize(
    ("request_case", "expected_tail", "output", "expected_mode"),
    [
        (ArkuiKbRequest(query="Button"), ["Button"], COMPACT_OUTPUT, "search"),
        (ArkuiKbRequest(query="Button", detail=True), ["Button", "--detail"], DETAIL_OUTPUT, "search"),
        (ArkuiKbRequest(query="Button", all_results=True), ["Button", "--all"], COMPACT_OUTPUT, "search"),
        (
            ArkuiKbRequest(query="button_pattern", field="source_paths"),
            ["button_pattern", "--field", "source_paths"],
            COMPACT_OUTPUT,
            "search",
        ),
        (
            ArkuiKbRequest(query="Button", category="basic"),
            ["Button", "--category", "basic"],
            COMPACT_OUTPUT,
            "search",
        ),
        (
            ArkuiKbRequest(
                query="Button",
                detail=True,
                all_results=True,
                field="name",
                category="basic",
            ),
            ["Button", "--detail", "--all", "--field", "name", "--category", "basic"],
            DETAIL_OUTPUT,
            "search",
        ),
        (ArkuiKbRequest(list_categories=True), ["--list-categories"], CATEGORIES_OUTPUT, "list_categories"),
        (ArkuiKbRequest(list_all=True), ["--list-all"], LIST_ALL_OUTPUT, "list_all"),
    ],
)
def test_local_backend_maps_every_official_cli_option(
    tmp_path,
    request_case,
    expected_tail,
    output,
    expected_mode,
):
    root = make_repository(tmp_path)
    calls = []

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        return completed(output)

    response = LocalArkuiKbBackend(root, runner=runner).search(request_case)

    command, options = calls[0]
    assert command == [sys.executable, str(root / "docs" / "kb_search.py"), *expected_tail]
    assert options["cwd"] == root
    assert "shell" not in options
    assert options["env"]["PYTHONIOENCODING"] == "utf-8"
    assert response.mode == expected_mode


def test_compact_detail_truncation_and_list_outputs_are_parsed(tmp_path):
    root = make_repository(tmp_path)
    backend = LocalArkuiKbBackend(root, runner=lambda *_args, **_kwargs: completed(TRUNCATED_COMPACT_OUTPUT))
    compact = backend.search(ArkuiKbRequest(query="Button"))

    backend._runner = lambda *_args, **_kwargs: completed(DETAIL_OUTPUT)
    detail = backend.search(ArkuiKbRequest(query="Button", detail=True))
    backend._runner = lambda *_args, **_kwargs: completed(CATEGORIES_OUTPUT)
    categories = backend.search(ArkuiKbRequest(list_categories=True))
    backend._runner = lambda *_args, **_kwargs: completed(LIST_ALL_OUTPUT)
    all_entries = backend.search(ArkuiKbRequest(list_all=True))

    assert compact.total_count == 12
    assert compact.truncated is True
    assert compact.results[0].name == "Button"
    assert compact.results[1].name == "ArcButton"
    assert detail.results[0].source_paths["pattern"].endswith("button_pattern.cpp")
    assert detail.results[0].api_paths["dynamic"].endswith("button.d.ts")
    assert detail.results[0].test_paths == ["test/unittest/core/pattern/button/"]
    assert categories.results[0] == ArkuiKbCategory(category="basic", count=22, detail="basic (22 个)")
    assert all_entries.results[1].name == "Button"
    assert all_entries.results[1].category == "basic"
    assert all_entries.results[1].kb_path is None


def test_compact_parser_preserves_parentheses_in_identity(tmp_path):
    root = make_repository(tmp_path)
    backend = LocalArkuiKbBackend(root, runner=lambda *_args, **_kwargs: completed(COMPACT_OUTPUT))

    response = backend.search(ArkuiKbRequest(query="Button"))

    assert response.results[1].name == "ContentModifier (Form)"
    assert response.results[1].name_cn == "表单类组件自定义内容"


def test_local_backend_no_match_is_empty_success(tmp_path):
    root = make_repository(tmp_path)
    backend = LocalArkuiKbBackend(root, runner=lambda *_args, **_kwargs: completed(NO_MATCH_OUTPUT))

    response = backend.search(ArkuiKbRequest(query="__no_such_entry__"))

    assert response == ArkuiKbSearchResponse()


def test_local_backend_reports_missing_root_and_script(tmp_path):
    missing_root = tmp_path / "missing"
    repository_without_script = tmp_path / "repository"
    repository_without_script.mkdir()

    with pytest.raises(ArkuiKbBackendError) as missing:
        LocalArkuiKbBackend(missing_root).search(ArkuiKbRequest(query="Button"))
    with pytest.raises(ArkuiKbBackendError) as no_script:
        LocalArkuiKbBackend(repository_without_script).search(ArkuiKbRequest(query="Button"))

    assert missing.value.code == "kb_root_not_found"
    assert no_script.value.code == "kb_unavailable"


def test_missing_repository_root_and_script_normalize_through_dispatch(tmp_path):
    missing_registry = ToolRegistry()
    register_knowledge_tools(missing_registry, tmp_path / "missing")
    missing = missing_registry.dispatch("kb_search", {"query": "Button"})

    empty_repository = tmp_path / "empty"
    empty_repository.mkdir()
    no_script_registry = ToolRegistry()
    register_knowledge_tools(no_script_registry, empty_repository)
    no_script = no_script_registry.dispatch("kb_search", {"query": "Button"})

    assert_failure(missing, "kb_root_not_found")
    assert_failure(no_script, "kb_unavailable")


def test_local_backend_execution_failures_and_invalid_output(tmp_path):
    root = make_repository(tmp_path)
    backend = LocalArkuiKbBackend(
        root,
        runner=lambda *_args, **_kwargs: completed("partial output", returncode=3, stderr="registry error"),
    )
    with pytest.raises(ArkuiKbBackendError) as failed:
        backend.search(ArkuiKbRequest(query="Button"))

    def timeout_runner(*_args, **_kwargs):
        raise subprocess.TimeoutExpired("kb_search", 2, output=b"partial", stderr=b"slow")

    with pytest.raises(ArkuiKbBackendError) as timed_out:
        LocalArkuiKbBackend(root, runner=timeout_runner, timeout=2).search(ArkuiKbRequest(query="Button"))
    with pytest.raises(ArkuiKbBackendError) as invalid:
        LocalArkuiKbBackend(root, runner=lambda *_args, **_kwargs: completed("unexpected output")).search(
            ArkuiKbRequest(query="Button")
        )

    assert failed.value.code == "kb_search_failed"
    assert failed.value.output == "partial output"
    assert failed.value.stderr == "registry error"
    assert failed.value.returncode == 3
    assert timed_out.value.code == "kb_search_timeout"
    assert timed_out.value.output == "partial"
    assert timed_out.value.stderr == "slow"
    assert invalid.value.code == "invalid_kb_output"


def test_repository_root_does_not_leak_into_observation(tmp_path):
    root = make_repository(tmp_path)
    registry = ToolRegistry()
    register_knowledge_tools(
        registry,
        root,
        runner=lambda *_args, **_kwargs: completed(COMPACT_OUTPUT),
    )

    observation = registry.dispatch("kb_search", {"query": "Button"})

    assert observation.success is True
    assert str(root) not in str(observation.model_dump(mode="json"))


def test_real_arkui_kb_default_detail_and_field_smoke_when_repository_is_configured():
    configured_root = os.getenv("ARKUI_REPOSITORY_ROOT")
    if not configured_root:
        pytest.skip("ARKUI_REPOSITORY_ROOT is not configured for this machine.")
    tools = KnowledgeTools(LocalArkuiKbBackend(Path(configured_root)))

    default = tools.kb_search({"query": "Button"})
    detail = tools.kb_search({"query": "Button", "detail": True})
    field = tools.kb_search({"query": "button_pattern", "field": "source_paths"})

    assert default.success is True
    assert any(item["name"] == "Button" for item in default.data["results"])
    assert detail.success is True
    assert detail.data["results"][0]["source_paths"]
    assert detail.data["results"][0]["test_paths"]
    assert field.success is True
    assert field.data["options"]["field"] == "source_paths"
    assert field.data["count"] >= 1
