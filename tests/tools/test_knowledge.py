import os
import subprocess
from pathlib import Path

import pytest

from arkui_ut_agent.config import get_config_from_spec
from arkui_ut_agent.tools import (
    ArkuiKbBackendError,
    ArkuiKbEntry,
    ArkuiKbSearchResponse,
    KnowledgeTools,
    LocalArkuiKbBackend,
    ToolRegistry,
    register_knowledge_tools,
)

DETAIL_OUTPUT = """找到 2 个匹配条目:

--- [1] Button (按钮组件) [score:100] ---
  分类: basic | 类型: component
  知识库: docs/kb/components/basic/button.md
  Spec: 05-04-01 -> specs/05-ui-components/04-input-form-components/01-button
  源码: pattern: frameworks/core/components_ng/pattern/button/button_pattern.cpp, model: frameworks/core/components_ng/pattern/button/button_model_ng.cpp
  API: dynamic: <OH_ROOT>/interface/sdk-js/api/@internal/component/ets/button.d.ts
  测试: test/unittest/core/pattern/button/
  关键词: Button, 按钮, click
  别名: Button组件, 按钮

--- [2] ArcButton (圆弧按钮组件) [score:70] ---
  分类: selector | 类型: component
  知识库: docs/kb/components/selector/arc-button.md
  源码: source: advanced_ui_component/arcbutton/source/arcbutton.ets
  API: dynamic: <OH_ROOT>/interface/sdk-js/api/@ohos.arkui.advanced.ArcButton.d.ets
  关键词: ArcButton, 圆弧按钮, arc, button...
"""

NO_MATCH_OUTPUT = """未找到匹配 '__no_such_entry__' 的知识库条目。
提示: 使用 --list-all 查看所有条目，或使用 --list-categories 查看分类。
      使用 --field source_paths 按源码路径搜索。
"""


class FakeBackend:
    name = "fake_arkui_kb"

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def search(self, query):
        self.calls.append(query)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def kb_entry(name="Button"):
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
        detail="real detail block",
    )


def kb_response(entries=None, *, total_count=None):
    entries = entries if entries is not None else [kb_entry()]
    total_count = len(entries) if total_count is None else total_count
    return ArkuiKbSearchResponse(
        results=entries,
        total_count=total_count,
        truncated=len(entries) < total_count,
    )


def make_checkout(tmp_path):
    root = tmp_path / "arkui_ace_engine"
    docs = root / "docs"
    docs.mkdir(parents=True)
    (docs / "kb_search.py").write_text("# fixture only\n", encoding="utf-8")
    return root


def completed(output="", *, returncode=0, stderr=""):
    return subprocess.CompletedProcess(["python", "docs/kb_search.py"], returncode, output, stderr)


def assert_failure(result, code):
    assert result.success is False
    assert result.data["results"] == []
    assert result.diagnostics[0].code == code
    assert result.provenance[0].source == "arkui_kb"


def test_tool_success_preserves_real_fields_and_kb_provenance():
    backend = FakeBackend([kb_response()])

    result = KnowledgeTools(backend).kb_search({"query": "Button"})

    assert result.success is True
    assert backend.calls == ["Button"]
    assert result.data["query"] == "Button"
    assert result.data["count"] == 1
    assert result.data["total_count"] == 1
    assert result.data["truncated"] is False
    assert result.data["results"][0]["source_paths"]["pattern"].endswith("button_pattern.cpp")
    assert result.data["results"][0]["test_paths"] == ["test/unittest/core/pattern/button/"]
    assert result.provenance[0].location == "docs/kb/components/basic/button.md"
    assert result.provenance[0].metadata == {
        "backend": "fake_arkui_kb",
        "identity": "Button",
        "script": "docs/kb_search.py",
        "fact_scope": "domain_navigation",
    }


def test_tool_preserves_multiple_and_truncated_backend_results():
    response = kb_response([kb_entry(), kb_entry("ArcButton")], total_count=9)

    result = KnowledgeTools(FakeBackend([response])).kb_search({"query": "Button"})

    assert result.success is True
    assert [entry["name"] for entry in result.data["results"]] == ["Button", "ArcButton"]
    assert result.data["count"] == 2
    assert result.data["total_count"] == 9
    assert result.data["truncated"] is True


def test_no_match_is_success_with_empty_results():
    result = KnowledgeTools(FakeBackend([kb_response([])])).kb_search({"query": "no match"})

    assert result.success is True
    assert result.data == {
        "query": "no match",
        "results": [],
        "count": 0,
        "total_count": 0,
        "truncated": False,
    }
    assert result.provenance[0].location == "docs/context_registry.json"


@pytest.mark.parametrize(
    "arguments",
    [{}, {"query": ""}, {"query": "   "}, {"query": 7}, {"query": "Button", "limit": 3}],
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
        (ArkuiKbBackendError("kb_search_failed", "process failed", output="partial", stderr="bad", returncode=2), "kb_search_failed"),
        (ArkuiKbBackendError("invalid_kb_output", "bad format", output="unexpected"), "invalid_kb_output"),
    ],
)
def test_backend_failures_are_structured_and_preserve_execution_evidence(error, code):
    result = KnowledgeTools(FakeBackend([error])).kb_search({"query": "Button"})

    assert_failure(result, code)
    assert result.diagnostics[0].details["output"] == error.output
    assert result.diagnostics[0].details["stderr"] == error.stderr
    assert result.diagnostics[0].details["returncode"] == error.returncode


def test_unconfigured_backend_and_unexpected_backend_error_are_structured():
    unavailable = KnowledgeTools().kb_search({"query": "Button"})
    failed = KnowledgeTools(FakeBackend([RuntimeError("broken adapter")])).kb_search({"query": "Button"})

    assert_failure(unavailable, "kb_unavailable")
    assert_failure(failed, "kb_search_failed")
    assert failed.diagnostics[0].details["exception_type"] == "RuntimeError"


@pytest.mark.parametrize(
    "response",
    [
        {"results": []},
        ArkuiKbSearchResponse.model_construct(results=[{"name": "Button"}], total_count=1, truncated=False),
        ArkuiKbSearchResponse.model_construct(results=[], total_count=1, truncated=False),
    ],
)
def test_invalid_backend_result_is_structured(response):
    result = KnowledgeTools(FakeBackend([response])).kb_search({"query": "Button"})

    assert_failure(result, "invalid_backend_result")


def test_mutated_backend_entry_is_structured():
    invalid_entry = kb_entry()
    invalid_entry.kb_path = None
    response = ArkuiKbSearchResponse.model_construct(results=[invalid_entry], total_count=1, truncated=False)

    result = KnowledgeTools(FakeBackend([response])).kb_search({"query": "Button"})

    assert_failure(result, "invalid_backend_result")


def test_registry_dispatch_normalizes_success_failure_and_provenance():
    backend = FakeBackend([kb_response(), ArkuiKbBackendError("kb_unavailable", "offline")])
    registry = ToolRegistry()
    register_knowledge_tools(registry, backend=backend)

    success = registry.dispatch("kb_search", {"query": "Button"})
    failure = registry.dispatch("kb_search", {"query": "Text"})

    assert registry.names == ("kb_search",)
    assert success.tool_name == "kb_search"
    assert success.success is True
    assert success.provenance[0].location == "docs/kb/components/basic/button.md"
    assert failure.tool_name == "kb_search"
    assert failure.success is False
    assert failure.diagnostics[0].code == "kb_unavailable"
    assert failure.provenance[0].metadata["query"] == "Text"


def test_registration_is_atomic_on_name_collision():
    registry = ToolRegistry()
    registry.register("kb_search", lambda _arguments: None)

    with pytest.raises(ValueError, match="already registered"):
        register_knowledge_tools(registry, backend=FakeBackend([]))

    assert registry.names == ("kb_search",)


def test_builtin_configs_define_unconfigured_arkui_root():
    assert get_config_from_spec("default")["tools"]["arkui_ace_engine_root"] is None
    assert get_config_from_spec("mini")["tools"]["arkui_ace_engine_root"] is None


def test_local_backend_uses_argv_fixed_cwd_and_real_detail_format(tmp_path):
    root = make_checkout(tmp_path)
    calls = []

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        return completed(DETAIL_OUTPUT)

    response = LocalArkuiKbBackend(root, runner=runner, python_executable="fixture-python").search("Button")

    command, options = calls[0]
    assert command == ["fixture-python", str(root / "docs" / "kb_search.py"), "Button", "--detail"]
    assert options["cwd"] == root
    assert "shell" not in options
    assert options["env"]["PYTHONIOENCODING"] == "utf-8"
    assert response.total_count == 2
    assert response.truncated is False
    button = response.results[0]
    assert button.name == "Button"
    assert button.category == "basic"
    assert button.type == "component"
    assert button.kb_path == "docs/kb/components/basic/button.md"
    assert button.func_id == "05-04-01"
    assert button.source_paths["pattern"].endswith("button_pattern.cpp")
    assert button.api_paths["dynamic"].endswith("button.d.ts")
    assert button.test_paths == ["test/unittest/core/pattern/button/"]
    assert "源码:" in button.detail


def test_local_backend_no_match_is_empty_success(tmp_path):
    root = make_checkout(tmp_path)
    backend = LocalArkuiKbBackend(root, runner=lambda *_args, **_kwargs: completed(NO_MATCH_OUTPUT))

    response = backend.search("__no_such_entry__")

    assert response == ArkuiKbSearchResponse()


def test_local_backend_reports_unconfigured_missing_root_and_missing_script(tmp_path):
    missing_root = tmp_path / "missing"
    checkout_without_script = tmp_path / "checkout"
    checkout_without_script.mkdir()

    with pytest.raises(ArkuiKbBackendError) as unconfigured:
        LocalArkuiKbBackend(None).search("Button")
    with pytest.raises(ArkuiKbBackendError) as missing:
        LocalArkuiKbBackend(missing_root).search("Button")
    with pytest.raises(ArkuiKbBackendError) as no_script:
        LocalArkuiKbBackend(checkout_without_script).search("Button")

    assert unconfigured.value.code == "kb_unavailable"
    assert missing.value.code == "kb_root_not_found"
    assert no_script.value.code == "kb_unavailable"


def test_local_backend_nonzero_exit_preserves_output_and_stderr(tmp_path):
    root = make_checkout(tmp_path)
    backend = LocalArkuiKbBackend(
        root,
        runner=lambda *_args, **_kwargs: completed("partial output", returncode=3, stderr="registry error"),
    )

    with pytest.raises(ArkuiKbBackendError) as raised:
        backend.search("Button")

    assert raised.value.code == "kb_search_failed"
    assert raised.value.output == "partial output"
    assert raised.value.stderr == "registry error"
    assert raised.value.returncode == 3


def test_local_backend_timeout_and_invalid_format_are_structured(tmp_path):
    root = make_checkout(tmp_path)

    def timeout_runner(*_args, **_kwargs):
        raise subprocess.TimeoutExpired("kb_search", 2, output=b"partial", stderr=b"slow")

    with pytest.raises(ArkuiKbBackendError) as timed_out:
        LocalArkuiKbBackend(root, runner=timeout_runner, timeout=2).search("Button")
    with pytest.raises(ArkuiKbBackendError) as invalid:
        LocalArkuiKbBackend(root, runner=lambda *_args, **_kwargs: completed("unexpected output")).search("Button")

    assert timed_out.value.code == "kb_search_timeout"
    assert timed_out.value.output == "partial"
    assert timed_out.value.stderr == "slow"
    assert invalid.value.code == "invalid_kb_output"
    assert invalid.value.output == "unexpected output"


def test_local_backend_does_not_expose_configured_absolute_root_in_observation(tmp_path):
    root = make_checkout(tmp_path)
    registry = ToolRegistry()
    register_knowledge_tools(
        registry,
        arkui_ace_engine_root=root,
        runner=lambda *_args, **_kwargs: completed(DETAIL_OUTPUT),
    )

    observation = registry.dispatch("kb_search", {"query": "Button"})

    assert observation.success is True
    assert str(root) not in str(observation.model_dump(mode="json"))


def test_real_arkui_kb_smoke_when_checkout_is_configured():
    configured_root = os.getenv("ARKUI_ACE_ENGINE_ROOT")
    if not configured_root:
        pytest.skip("ARKUI_ACE_ENGINE_ROOT is not configured for this machine.")

    response = LocalArkuiKbBackend(Path(configured_root)).search("Button")

    assert response.total_count >= 1
    assert any(entry.name == "Button" for entry in response.results)
    assert all(entry.kb_path.startswith("docs/kb/") for entry in response.results)
