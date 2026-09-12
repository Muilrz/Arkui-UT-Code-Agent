# 当前任务（CURRENT_TASK）

## 1. 本文件用途

这是**唯一的实时任务交接文档**。

Codex 每次开始开发时应优先读取本文件，但长期边界仍以 `SPEC.md` / `DECISIONS.md` 为准。

保持简短：

- 只保存当前目标、范围、Blocker、真实验证结果；
- 旧状态直接替换，不追加成流水账；
- 完成一个 Slice 后立即更新下一个 Slice。

## 2. Current Objective

**保持 baseline runtime 在 Windows 与 POSIX 上具有明确、可验证的 shell 执行契约。**

当前已完成的 runtime slice：

```text
LocalEnvironment
→ native ShellBackend
→ PowerShell (Windows) / Bash or sh (POSIX)
```

## 3. 当前 In Scope

1. 显式 PowerShell/POSIX shell backend。
2. OS 与 shell dialect 进入 Agent prompt context。
3. 跨平台 LocalEnvironment 公共能力测试。
4. prompt_toolkit session 惰性创建，不在 import/collection 时要求真实 TTY。

## 4. 当前 Out of Scope

本 Slice 暂不实现：

- Vector Retrieval / Repository Index；
- Persistent Graph；
- 完整 Planner/Replanner；
- clangd MCP Server；
- 成熟 Context Builder ranking；
- 完整 ArkUI UT Repair Loop；
- 完整 benchmark/evaluation suite；
- Stage 6 的真实 clangd MCP 集成。

## 5. Expected Deliverables

- `ShellBackend` compatibility boundary；
- Windows PowerShell backend；
- Linux/macOS Bash/sh backend；
- runtime shell metadata in prompt context；
- platform-aware LocalEnvironment tests；
- non-TTY-safe prompt input initialization。

## 6. Verification

2026-09-13 Windows 验证结果：

```text
pytest -q
→ 396 passed, 3 skipped, 0 failed, 0 errors

ruff check src tests
→ passed
```

仍需在 Linux/macOS CI 或实际环境验证 Bash/sh selection、POSIX process-group timeout cleanup 及平台路径行为。

在这些命令真实执行成功，或写明具体 Blocker 前，不得把本任务标为完成。

## 7. Known Blockers / Unknowns

当前 Windows 验证无 blocker。POSIX backend 的实现和测试契约已存在，但仍等待真实 Linux/macOS 环境验证。

## 8. Completion Checklist

- [x] Windows 默认 PowerShell；
- [x] POSIX 默认 Bash/sh；
- [x] 上层 Agent 不依赖具体 backend；
- [x] prompt 明确 OS 与 shell dialect；
- [x] runtime 不做命令字符串翻译；
- [x] Windows pytest 0 failed / 0 errors；
- [x] ruff passed；
- [ ] Linux/macOS runtime verification。
