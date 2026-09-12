# 当前任务（CURRENT_TASK）

## 1. 本文件用途

这是**唯一的实时任务交接文档**。

Codex 每次开始开发时应优先读取本文件，但长期边界仍以 `SPEC.md` / `DECISIONS.md` 为准。

保持简短：

- 只保存当前目标、范围、Blocker、真实验证结果；
- 旧状态直接替换，不追加成流水账；
- 完成一个 Slice 后立即更新下一个 Slice。

## 2. Current Objective

**基于选定的 `mini-swe-agent` baseline 建立可运行仓库，并确认项目模块边界；不要过早实现复杂 Agent 功能。**

当前目标是先获得一个干净、可验证的 baseline，然后进入第一个真正开发 Slice：

```text
ToolResult
+ Observation
+ Tool Registry
```

## 3. 当前 In Scope

1. Fork/import 并 pin `mini-swe-agent` baseline。
2. 阅读真实 upstream package/module 结构。
3. 标记 reused vs project-owned modules。
4. 建立初始 package layout，至少覆盖：
   - control plane；
   - memory/context；
   - retrieval/tools；
   - semantic provider；
   - task graph；
   - UT workflow；
   - trace/evaluation。
5. 保留一套 runnable baseline config，供后续 ablation。
6. 确认项目正常 install/test/lint/type/smoke 命令。
7. 在文档/配置层确认 Semantic 边界：
   - external clangd MCP；
   - local `SemanticProvider` adapter；
   - 不实现 MCP Server。

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

- runnable baseline repository；
- upstream revision 已 pin；
- reused vs project-owned module boundary；
- 初始 package/directory skeleton；
- 一条正常 developer verification path；
- 下一 Slice 确认：`ToolResult + Observation + Tool Registry`。

## 6. Verification

仓库 baseline 建立后，把真实命令写在这里：

```text
TODO: baseline install command
TODO: baseline unit-test command
TODO: lint/type command（如 upstream 有）
TODO: minimal CLI smoke-test command
```

在这些命令真实执行成功，或写明具体 Blocker 前，不得把本任务标为完成。

## 7. Known Blockers / Unknowns

当前待确认：

- `mini-swe-agent` 最终 pin 的 revision；
- 目标环境里 ArkUI KB 的真实调用接口/命令；
- ArkUI/Ace Engine 环境中 `compile_commands.json` 的稳定获取方式；
- Stage 6 集成时 `felipeerias/clangd-mcp-server` 的最终 pin revision 与真实 Tool schema。

注意：后两项现在是**已知的后续集成前置问题**，但不阻塞 Milestone 0/1。

## 8. Completion Checklist

- [ ] upstream baseline 已 pin；
- [ ] baseline 可运行；
- [ ] reused vs project-owned modules 已记录；
- [ ] 初始 package skeleton 已建立；
- [ ] verification commands 已真实执行；
- [ ] 未引入任何被 `SPEC.md` 禁止的 Repository Intelligence subsystem；
- [ ] `PLAN.md` Milestone 0 可以标记为 `[x]`。
