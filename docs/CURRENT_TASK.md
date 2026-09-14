# 当前任务（CURRENT_TASK）

## 1. 本文件用途

这是**唯一的实时任务交接文档**。

Codex 每次开始开发时应优先读取本文件，但长期边界仍以 `SPEC.md` / `DECISIONS.md` 为准。

保持简短：

- 只保存当前目标、范围、Blocker、真实验证结果；
- 旧状态直接替换，不追加成流水账；
- 完成一个 Slice 后立即更新下一个 Slice。

## 2. Current Objective

**Stage 1A：Tool Contract 基础层已完成。**

已定义所有后续 Tool 共用的结果、观测、来源与诊断模型，并提供可预测的 Tool Registry / dispatch 行为。

下一 Slice 为 Stage 1B 具体 Tool 实现，尚未开始。

## 3. 当前 In Scope

1. `ToolResult`。
2. `Observation` 与 `ToolResult → Observation` normalization。
3. provenance / diagnostic 基础模型。
4. Tool Registry、注册与 dispatch。
5. success/failure normalization、provenance 保留、重复/未知 Tool、Tool 执行异常的 deterministic unit tests。

## 4. 当前 Out of Scope

本 Slice 不实现：

- Repository / Editing / Execution / ArkUI KB 具体 Tool；
- Memory / Context Builder；
- Planner / Replanner / Diagnose / Stop Policy；
- RetrievalRouter；
- SemanticProvider / clangd MCP 集成；
- Task Relation Graph；
- ArkUI UT Workflow；
- 新的 Repository Index、Persistent Graph 或跨任务知识系统。

## 5. Expected Deliverables

- 稳定且可序列化的 Tool result / observation 基础模型；
- 保留 provenance 与 diagnostics 的 normalization；
- 拒绝重复名称、标准化未知 Tool 与执行异常的 Tool Registry；
- 对成功路径和失败路径的 deterministic unit tests。

## 6. Verification

Baseline 已在 Windows 与 Ubuntu CI 通过。

2026-09-14 Windows Stage 1A 验证结果：

```text
py -m pytest -q
→ 408 passed, 3 skipped, 1 warning, 0 failed, 0 errors

py -m ruff check src tests
→ passed
```

warning 为既有 `last_n_messages_offset` deprecation warning，与本 Slice 无关。

## 7. Known Blockers / Unknowns

当前无已知 blocker。

## 8. Completion Checklist

- [x] `ToolResult` / `Observation` 已实现；
- [x] provenance / diagnostic 基础模型已实现；
- [x] Tool Registry / dispatch 已实现；
- [x] success/failure normalization 已测试；
- [x] provenance 保留已测试；
- [x] 重复/未知 Tool 已测试；
- [x] Tool 执行异常标准化已测试；
- [x] pytest passed；
- [x] ruff passed。
