# 当前任务（CURRENT_TASK）

## 1. 本文件用途

这是**唯一的实时任务交接文档**。

Codex 每次开始开发时应优先读取本文件，但长期边界仍以 `SPEC.md` / `DECISIONS.md` 为准。

保持简短：

- 只保存当前目标、范围、Blocker、真实验证结果；
- 旧状态直接替换，不追加成流水账；
- 完成一个 Slice 后立即更新下一个 Slice。

## 2. Current Objective

**Stage 1C：Editing Tools 已完成。**

已基于 Stage 1A/1B 的统一契约和 repository-root 边界，实现确定性的文本写入与精确补丁修改。

后续 Stage 1 Slice 尚未开始。

## 3. 当前 In Scope

1. `write_file` 创建或覆写 UTF-8 文本文件。
2. `apply_patch` 对唯一精确匹配执行文本替换。
3. repository-root、相对路径及 symlink 安全边界。
4. Editing Tool 原子注册与 dispatch normalization。
5. 主要成功/失败路径的 deterministic unit tests。

## 4. 当前 Out of Scope

本 Slice 不实现：

- Execution / ArkUI KB Tool；
- Memory / Context Builder；
- Planner / Replanner / Diagnose / Stop Policy；
- RetrievalRouter；
- SemanticProvider / clangd MCP 集成；
- Task Relation Graph；
- ArkUI UT Workflow；
- 新的 Repository Index、Persistent Graph 或跨任务知识系统。

## 5. Expected Deliverables

- Windows / POSIX 兼容的 Editing Tool wrappers；
- 明确且可测试的父目录创建与覆写行为；
- 越界、冲突、编码和 IO 失败返回结构化 diagnostics 与 provenance；
- 成功结果包含文件级修改信息，并经 Registry dispatch 归一化为 `Observation`。

## 6. Verification

2026-09-14 Windows Stage 1C 验证结果：

```text
py -m pytest -q
→ 464 passed, 3 skipped, 1 warning, 0 failed, 0 errors

py -m ruff check src tests
→ passed

git diff --check
→ passed
```

warning 为既有 `last_n_messages_offset` deprecation warning，与本 Slice 无关。

## 7. Known Blockers / Unknowns

当前无已知 blocker。

## 8. Completion Checklist

- [x] `write_file` create / overwrite 已实现并测试；
- [x] `apply_patch` success / mismatch / conflict 已实现并测试；
- [x] repository 越界与 symlink 越界已阻止并测试；
- [x] 非法参数、编码与 IO failure 已结构化并测试；
- [x] Editing Tool 原子注册与 dispatch normalization 已测试；
- [x] Windows / POSIX 文件内容与路径行为保持兼容；
- [x] pytest passed；
- [x] ruff passed；
- [x] git diff --check passed。
