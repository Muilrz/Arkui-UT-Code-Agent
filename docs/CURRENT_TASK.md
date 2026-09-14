# 当前任务（CURRENT_TASK）

## 1. 本文件用途

这是**唯一的实时任务交接文档**。

Codex 每次开始开发时应优先读取本文件，但长期边界仍以 `SPEC.md` / `DECISIONS.md` 为准。

保持简短：

- 只保存当前目标、范围、Blocker、真实验证结果；
- 旧状态直接替换，不追加成流水账；
- 完成一个 Slice 后立即更新下一个 Slice。

## 2. Current Objective

**Stage 1D：Execution Tools 已完成。**

已复用现有 `LocalEnvironment` / `ShellBackend` 执行机制，实现薄的 command、build、test Tool wrapper。

后续 Stage 1 Slice 尚未开始。

## 3. 当前 In Scope

1. `run_command`。
2. 显式 command 驱动的 `build` / `test`。
3. repository-root 内 cwd 与显式 env override。
4. execution result / timeout / failure normalization 与 provenance。
5. 不触发 submission sentinel 的底层 execution primitive。
6. Execution Tool 原子注册与 deterministic unit tests。

## 4. 当前 Out of Scope

本 Slice 不实现：

- ArkUI KB Tool；
- Memory / Context Builder；
- Planner / Replanner / Diagnose / Stop Policy；
- RetrievalRouter；
- SemanticProvider / clangd MCP 集成；
- Task Relation Graph；
- ArkUI UT Workflow；
- 新的 Repository Index、Persistent Graph 或跨任务知识系统。

## 5. Expected Deliverables

- 复用 `LocalEnvironment` / `ShellBackend` 的统一执行路径；
- `run_command` / `build` / `test` 共用薄执行实现；
- command、cwd、output、return code 与失败状态稳定进入 `ToolResult`；
- env 只记录 override key，不把 value 写入 result / provenance；
- dispatch 后统一归一化为 `Observation`。

## 6. Verification

2026-09-14 Windows Stage 1D 验证结果：

```text
py -m pytest -q
→ 492 passed, 3 skipped, 1 warning, 0 failed, 0 errors

py -m ruff check src tests
→ passed

git diff --check
→ passed
```

warning 为既有 `last_n_messages_offset` deprecation warning，与本 Slice 无关。

## 7. Known Blockers / Unknowns

当前无已知 blocker。

## 8. Completion Checklist

- [x] `run_command` success / non-zero / timeout 已实现并测试；
- [x] `build` / `test` success / failure 已实现并测试；
- [x] cwd / env override 契约已实现并测试；
- [x] backend unavailable / 启动失败 / 非法参数已结构化并测试；
- [x] submission sentinel 不逃逸 Tool contract；
- [x] Execution Tool 原子注册与 dispatch normalization 已测试；
- [x] Windows / POSIX backend 复用边界已测试；
- [x] pytest passed；
- [x] ruff passed；
- [x] git diff --check passed。
