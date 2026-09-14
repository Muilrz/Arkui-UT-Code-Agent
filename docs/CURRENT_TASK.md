# 当前任务（CURRENT_TASK）

## 1. 本文件用途

这是**唯一的实时任务交接文档**。

Codex 每次开始开发时应优先读取本文件，但长期边界仍以 `SPEC.md` / `DECISIONS.md` 为准。

保持简短：

- 只保存当前目标、范围、Blocker、真实验证结果；
- 旧状态直接替换，不追加成流水账；
- 完成一个 Slice 后立即更新下一个 Slice。

## 2. Current Objective

**Stage 1B：Repository Read Tools 已完成。**

已基于 Stage 1A 的统一契约，实现当前 checkout 的只读源码与 Git 事实查询。

后续 Stage 1 Slice 尚未开始。

## 3. 当前 In Scope

1. `rg_search`。
2. `read_file`。
3. `list_files`。
4. `git_diff`。
5. `git_status`。
6. Repository Tool 注册与 dispatch 后的 `Observation` normalization。
7. 主要成功/失败路径的 deterministic unit tests。

## 4. 当前 Out of Scope

本 Slice 不实现：

- Editing / Execution / ArkUI KB Tool；
- Memory / Context Builder；
- Planner / Replanner / Diagnose / Stop Policy；
- RetrievalRouter；
- SemanticProvider / clangd MCP 集成；
- Task Relation Graph；
- ArkUI UT Workflow；
- 新的 Repository Index、Persistent Graph 或跨任务知识系统。

## 5. Expected Deliverables

- Windows / POSIX 兼容的 Repository Read Tool wrappers；
- 所有结果使用 `ToolResult`，经 Registry dispatch 后生成 `Observation`；
- 正常失败返回结构化 diagnostics 与 provenance；
- 对文件、搜索、Git 成功/失败行为的 deterministic unit tests。

## 6. Verification

2026-09-14 Windows Stage 1B 验证结果：

```text
py -m pytest -q
→ 430 passed, 3 skipped, 1 warning, 0 failed, 0 errors

py -m ruff check src tests
→ passed

git diff --check
→ passed
```

warning 为既有 `last_n_messages_offset` deprecation warning，与本 Slice 无关。

## 7. Known Blockers / Unknowns

当前无已知 blocker。

## 8. Completion Checklist

- [x] `rg_search` 已实现并测试；
- [x] `read_file` 已实现并测试；
- [x] `list_files` 已实现并测试；
- [x] `git_diff` 已实现并测试；
- [x] `git_status` 已实现并测试；
- [x] Repository Tool 注册与 normalization 已测试；
- [x] Windows / POSIX 路径与进程调用保持兼容；
- [x] pytest passed；
- [x] ruff passed；
- [x] git diff --check passed。
