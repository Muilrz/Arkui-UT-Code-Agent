# 当前任务（CURRENT_TASK）

## 1. Slice

**Stage 5 / Slice 5A — Retrieval Intent 与确定性路由契约**

所属清单：`PLAN.md` 的 Stage 5。本文件已替换已完成的 Stage 4 / Slice 4E 交接状态。

## 2. Objective / Status

已完成 Stage 5 的最小 Retrieval contract：调用方先明确当前信息需求，`RetrievalRouter` 再把一个
`RetrievalIntent` 确定性映射到单一 retrieval destination。Router 不判断“缺什么信息”，不执行工具，
不做 Planner reasoning，也不编排多步 retrieval flow。

本 Slice 已完成且当前无 Blocker。

## 3. Retrieval Contract

支持的 intent：

```text
find_component
find_source
find_test
read_implementation
search_text
resolve_symbol
find_references
find_callers
find_callees
find_implementations
```

固定映射：

```text
find_component / find_source / find_test
→ kb_search

read_implementation
→ read_file

search_text
→ rg_search

resolve_symbol / find_references / find_callers / find_callees / find_implementations
→ semantic_provider
```

`semantic_provider` 在本 Slice 中只是稳定的 destination 标识，不存在 Provider、MCP、clangd/LSP、
health/capability detection 或外部调用实现。调用方必须传入 `RetrievalIntent`；raw string、未知 intent 和
其他无效对象均显式失败，不进行模糊匹配、默认路由或静默猜测。

## 4. Implementation / Tests

- `src/arkui_ut_agent/agents/retrieval.py`：定义 `RetrievalIntent`、`RetrievalDestination` 和无状态的
  `RetrievalRouter`，使用只读固定映射完成纯路由；
- `src/arkui_ut_agent/agents/__init__.py`：导出 Retrieval contract；
- `tests/agents/test_retrieval.py`：逐项覆盖全部 intent、四类 destination 区分、invalid/unsupported 显式失败、
  重复输入稳定性，以及 Router 无状态且不依赖 Planner/Model；
- `docs/PLAN.md`：仅勾选已经由本 Slice 证明的 Retrieval Intent 与 mapping 两项。

## 5. Out of Scope / Boundary Confirmation

未接入默认 Agent runtime loop，未修改 Planner，未实现 KB → source verification、Test localization、
semantic escalation criteria 或 Stage 5 Acceptance。未实现任何 Stage 6 `SemanticProvider` / MCP / clangd/LSP
backend，也未引入 Repository Index、Vector DB、RAG、Persistent Graph、Repository Snapshot、持久索引或
其他 Repository Intelligence 基础设施。Stage 0–4 的 planning/runtime step identity 与 Evidence contract 未变。

## 6. Verification

实际验证：

```text
py -m pytest -q tests/agents/test_retrieval.py
27 passed

py -m pytest -q tests/agents
461 passed

$agentScripts = py -c "import sysconfig; print(sysconfig.get_path('scripts'))"
$env:PATH = "$agentScripts;$env:PATH"
py -m pytest -q
813 passed, 4 skipped, 1 existing deprecation warning

py -m ruff check src tests
All checks passed!

git diff --check
passed（仅 Git 的 LF→CRLF working-copy notices）
```

未检查 CI，未创建 commit。
