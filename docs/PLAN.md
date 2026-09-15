# 实施计划（PLAN）

## 1. 使用方式

本文件维护 Codex 和人工开发共同遵循的 Stage 实施顺序与验收点。

每个 **Stage N** 对应 `TECHNICAL_ROADMAP.md` 的同名长期阶段，本文件只维护该 Stage 的 checklist 和
Acceptance，不引入额外任务层级。实际开发从未完成 checklist 中按依赖选取可独立实现、测试、验收且
范围可控的 **Slice NA / NB / NC ...**；Slice 只记录在 `CURRENT_TASK.md`，不作为新的 Stage。

不要把这里写成每天的工作日志：

- 当前实际 Slice / Blocker 写在 `CURRENT_TASK.md`；
- 长期需求边界写在 `SPEC.md`；
- 架构决策写在 `DECISIONS.md`；
- 本文件只在阶段顺序、依赖或验收标准变化时更新。

状态：

```text
[ ] 未开始
[~] 进行中
[x] 已验证完成
[!] 阻塞
```

## Stage 0 — Repository Baseline

- [x] Fork/import `arkui-ut-code-agent`。
- [x] Pin upstream baseline revision（`SWE-agent/mini-swe-agent@04d809ce`）。
- [x] 识别可直接复用的 upstream modules。
- [x] 建立 project-owned package/module 边界。
- [x] 保留可运行 baseline agent config，用于未来 ablation。
- [x] 记录正常 install/test/lint/type/smoke 命令。
- [x] 明确 Semantic 采用 external clangd MCP + local adapter，不实现 Server。

**Acceptance：** baseline 可运行；reused/project-owned 边界可读；无自建 Repository Index。

## Stage 1 — ToolResult / Observation / Tool Registry

- [x] 定义 `ToolResult`。
- [x] 定义 `Observation`。
- [x] 定义 provenance model。
- [x] 建立 Tool Registry / dispatch。
- [x] 实现 `rg_search` / `read_file` / `list_files`。
- [x] 实现 `git_diff` / `git_status`。
- [x] 实现 `apply_patch` / `write_file`。
- [x] 实现 `run_command` / `build` / `test`。
- [x] 封装基础 `kb_search`。
- [x] 添加 success/failure normalization tests。

**Acceptance：** 所有 Tool 都通过统一结果模型进入 Observation，失败不会绕过 Agent 状态机。

## Stage 2 — AgentState 与 Memory

- [x] 定义 `AgentState`。
- [x] 实现 Working Memory。
- [x] 实现 Task Memory。
- [ ] 实现 Evidence Memory。
- [ ] 定义 Evidence identity / dedup。
- [ ] 关联 `step_id`、Tool Call 与 Evidence provenance。
- [~] 添加 serialization / update tests。

**Acceptance：** 不重放完整 chat history 也可以恢复当前任务的关键状态与 Evidence。

## Stage 3 — Context Builder

- [ ] 定义 context sections 和预算策略。
- [ ] 选择 relevant Task Memory。
- [ ] 选择 current-step Evidence。
- [ ] Build/Test diagnostics 优先。
- [ ] 压缩历史失败。
- [ ] Evidence / source snippet 去重。
- [ ] deterministic context tests。

**Acceptance：** Context 大小有界，同时保留下一步决策所需 Evidence。

## Stage 4 — Planning 与 Control Loop

- [ ] 定义 `Plan` / `PlanStep`。
- [ ] Initial Planning。
- [ ] 决策状态：retrieve / act / diagnose / replan / repair / verify / finish。
- [ ] repeated tool/input/observation detection。
- [ ] Stop Policy。
- [ ] 将 state/memory/context 接入主 Agent Loop。

**Acceptance：** Trace 中每个 Step 都能看到 current_goal、decision、action、observation、state update。

## Stage 5 — RetrievalRouter

- [ ] 定义少量 Retrieval Intent。
- [ ] Intent → KB/source/SemanticProvider mapping。
- [ ] 实现 KB → source verification flow。
- [ ] 实现 Test localization flow。
- [ ] 定义 semantic escalation criteria。

**Acceptance：** Router 只做映射；“当前缺什么”仍由 Planner/Agent 决定。

## Stage 6 — External Semantic Provider

### 6.1 前置 Spike

- [ ] 在代表性 ArkUI checkout 上确认 `compile_commands.json` 获取方式。
- [ ] 验证 clangd 可解析代表性 ArkUI Symbol。
- [ ] 启动 `felipeerias/clangd-mcp-server`。
- [ ] 验证 definition/reference 查询。
- [ ] 验证 caller/callee 或 implementation 查询。
- [ ] 记录 workspace/clangd/compilation database/MCP config。
- [ ] Pin 验证通过的 MCP Server revision/version。
- [ ] 若首选实现不适配，再验证 `mpsm/mcp-cpp`。

### 6.2 项目实现

- [ ] 定义 `SemanticProvider`。
- [ ] 实现 `UnavailableSemanticProvider`。
- [ ] 实现 `McpSemanticProvider`。
- [ ] 将外部 MCP 结果/错误归一化为项目 Evidence。
- [ ] 支持 available/degraded/unavailable。
- [ ] 添加 fallback / failure tests。

**Acceptance：** Semantic backend 缺失时，任务仍可以 KB + source 执行；项目中不存在自研 clangd/LSP/MCP Server。

## Stage 7 — Task Relation Graph

- [ ] 定义 task-local node/edge types。
- [ ] 由 Evidence 更新 Graph。
- [ ] Edge 保存 provenance。
- [ ] 提供 Context Builder / Planner 所需 query helper。
- [ ] 添加防止误做 repository-wide graph 的测试/约束。

**Acceptance：** Graph 为 Evidence-driven、current-task-only。

## Stage 8 — UT Development / Repair Loop

- [ ] Target / Component / Function localization。
- [ ] Existing Test / Fixture / Mock discovery。
- [ ] UT-specific Plan。
- [ ] 优先运行最小相关 Target。
- [ ] Build/Test failure classification。
- [ ] Repair 前先补缺失 Evidence。
- [ ] Replan / Edit / Rerun。
- [ ] Verify final pass。

**Acceptance：** 至少一个 ArkUI Gold Case end-to-end 成功，并能从失败的第一次尝试通过 Evidence-driven Repair 恢复。

## Stage 9 — Trace 与 Evaluation

- [ ] 完整 Execution Trace serialization。
- [ ] 记录 model calls / token / cost / step / edit / build / test。
- [ ] ArkUI Gold Cases benchmark runner。
- [ ] deterministic localization/tool/pass metrics。
- [ ] ablation config。
- [ ] Baseline vs Enhanced Agent 对比。

**Acceptance：** 项目核心能力由 Trace 或 Evaluation 证明，不仅存在于架构描述中。

## Cross-Cutting Quality Gates

任何 Stage checklist 标记 `[x]` 前检查：

- [ ] 新增 deterministic logic 有对应测试。
- [ ] Error / degraded path 已测试。
- [ ] 未引入 Persistent Repository Index / Full Graph。
- [ ] 外部事实保留 provenance。
- [ ] 修改只同步到真正受影响的长期文档。
- [ ] `CURRENT_TASK.md` 记录了实际验证命令和结果。
