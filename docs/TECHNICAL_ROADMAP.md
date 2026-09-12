# 技术路线（TECHNICAL_ROADMAP）

## 1. 目标

把 `arkui-ut-code-agent` 的简单线性 Action Loop 演进为一个面向 ArkUI/Ace Engine 的、Evidence-driven 的 UT Code Agent。

工程投入优先放在：

```text
Agent Control Plane
Memory / Context
Retrieval Strategy
UT Feedback Loop
Trace / Evaluation
```

而不是重复开发 clangd、LSP、全仓索引或持久化 Code Graph。

本文件描述**目标架构和实现阶段**；实时开发状态写在 `CURRENT_TASK.md`。

## 2. 目标架构

```text
CLI / Config / Environment / Model
        ↑ 复用 arkui-ut-code-agent

Agent Control Plane
├── AgentState
├── Planner / Replanner
├── Decision / Diagnose
└── Stop Policy

Task Runtime
├── Working Memory
├── Task Memory
├── Evidence Memory
├── Context Builder
└── Task Relation Graph

Retrieval & Tools
├── Tool Registry / Observation Normalization
├── RetrievalRouter
├── ArkUI KB Client
├── Repository Tools (`rg` / read / git)
├── SemanticProvider
│   └── McpSemanticProvider
├── Editing Tools
└── Build / Test Tools

External Semantic Dependency
├── preferred: felipeerias/clangd-mcp-server
├── fallback/reference: mpsm/mcp-cpp
└── clangd + compile_commands.json

Workflow
└── ArkUI UT Development / Repair

Observability & Quality
├── Execution Trace
└── Evaluation / Gold Cases
```

## 3. Semantic Dependency Policy

语义路径固定为外部依赖：

```text
ArkUI Code Agent
        ↓
project-owned SemanticProvider
        ↓
McpSemanticProvider
        ↓ MCP
existing clangd MCP server
        ↓ LSP
clangd
        ↓
compile_commands.json + source checkout
```

首选：

```text
felipeerias/clangd-mcp-server
https://github.com/felipeerias/clangd-mcp-server
```

备用/参考：

```text
mpsm/mcp-cpp
https://github.com/mpsm/mcp-cpp
```

约束：

- Agent 高层不得直接耦合某个 MCP Server schema；
- 实际集成时 pin 已验证 revision/version；
- 高质量 clangd 语义要求有效 `compile_commands.json` 或等价配置；
- 项目允许调用已有 ArkUI/GN 能力获取 compilation database；
- 项目不建设 compile-command inference/indexing subsystem；
- 首选实现不适配时，先验证备用开源实现，而不是直接进入自研 Server。

## Stage 0 — Baseline Fork 与边界确认

### 目标

- fork/import `arkui-ut-code-agent`；
- 确认哪些 upstream 模块直接复用；
- 建立项目自主模块边界；
- 保留可运行的 baseline agent；
- 固化“不自研 clangd MCP Server”的边界。

### Exit Criteria

- baseline 能通过 inherited Model/Environment 跑通最小任务；
- reused vs project-owned 模块边界明确；
- Semantic ownership 明确为 provider/adapter only；
- 未引入新的 Repository Index System。

## Stage 1 — Tool Contract 与 Observation Layer

先建立稳定 Tool 基础，再做复杂 Planning。

### Deliverables

- `ToolResult`
- `Observation`
- provenance model
- Tool Registry / dispatch
- Repository Tools：`rg_search`、`read_file`、`list_files`、`git_diff`、`git_status`
- Editing Tools：`apply_patch`、`write_file`
- Execution Tools：`run_command`、`build`、`test`
- ArkUI `kb_search` wrapper

### Exit Criteria

- success/failure 都能稳定归一化；
- provenance 在 ToolResult → Observation 后仍存在；
- Tool failure 作为数据进入 Agent，而不是非预期异常直接打断流程。

## Stage 2 — Explicit Agent State 与 Memory

### Deliverables

- `AgentState`
- Working Memory
- Task Memory
- Evidence Memory
- Evidence identity / dedup
- step_id / memory update event

### Exit Criteria

- 不依赖完整 chat history 也能序列化当前任务关键状态；
- Evidence 可追溯到产生它的 Tool/Step；
- 模型推测不会自动写入 confirmed facts。

## Stage 3 — Context Builder

### Deliverables

- 固定上下文 section 顺序；
- Task/Evidence relevance selection；
- source snippet budget；
- diagnostic prioritization；
- failure summarization；
- Evidence dedup。

### Exit Criteria

- model input 大小不随 trajectory 无限增长；
- 当前 Build/Test Error 不会在压缩时丢失；
- 当前 Step 关键 Evidence 优先保留；
- Context 构造存在 deterministic tests。

## Stage 4 — Planner / Replanner / Diagnose / Stop Policy

### Deliverables

- structured Plan / PlanStep
- initial planning
- current_step / next_action
- diagnose states
- Replan trigger
- repeated-call detection
- stop reasons

### Exit Criteria

Agent 能明确区分：

```text
retrieve
act
diagnose
replan
repair
verify
finish
```

并能自动打断：

```text
same tool + same input + same observation
```

的死循环。

## Stage 5 — RetrievalRouter 与 Layered Retrieval

### Deliverables

- Retrieval Intent model
- Intent → Tool mapping
- KB → source → semantic escalation policy
- Test localization：KB test_paths + `rg` + source

### Exit Criteria

- Router 保持 lightweight/deterministic；
- Planner 负责判断“缺什么”，Router 只负责“交给谁”；
- source fact 可覆盖 stale KB metadata；
- Semantic 只在精确性需要时调用。

## Stage 6 — External clangd MCP Integration

本阶段是**集成语义能力**，不是开发 clangd MCP Server。

### 6.1 Integration Prerequisite Spike

先对一个真实 ArkUI/Ace Engine checkout 做最小验证：

1. 使用已有构建能力定位或生成 `compile_commands.json`；
2. 验证 `clangd` 能解析至少一个代表性 ArkUI Symbol；
3. 启动首选 clangd MCP Server；
4. 完成至少一次 definition/reference 查询；
5. 验证至少一次 caller/callee 或 implementation 查询；
6. 记录 workspace root、clangd path、MCP invocation、compilation database path、必要参数；
7. pin 实际验证通过的 MCP Server revision/version。

若首选实现不兼容：

```text
验证 mpsm/mcp-cpp
→ 仍不满足再重新评估方案
```

不能直接默认进入自研 MCP Server。

### 6.2 Project Deliverables

- `SemanticProvider`
- `McpSemanticProvider`
- `UnavailableSemanticProvider`
- config / health / capability detection
- Provider operation → pinned MCP tool mapping
- `ToolResult` / `Observation` / Evidence normalization
- available / degraded / unavailable handling

期望 Provider operation：

```text
find_definition
find_references
find_callers
find_callees
find_implementations
get_hover / symbol_at
```

### Exit Criteria

- 有 compilation database 时，可以对代表性 ArkUI Symbol 完成精确语义查询；
- Semantic backend 缺失时，Agent 仍可以 KB + source 工作；
- Semantic failure 不导致整个任务崩溃；
- exact semantic evidence 能与 text-search inference 区分；
- 没有实现项目自有 clangd/LSP/MCP Server 或 Persistent Symbol Index。

## Stage 7 — Task Relation Graph

### Deliverables

- in-memory task graph
- node/edge types
- provenance-bearing edge
- Evidence → graph update
- Context Builder / Planner query helper

### Exit Criteria

- Graph 仅包含当前任务发现的事实；
- 不存在 Repository-wide population job；
- 每条 Graph relation 都可解释其 Evidence 来源。

## Stage 8 — ArkUI UT Workflow

### Deliverables

- target localization
- existing test / Fixture / Mock discovery
- UT Plan generation
- minimal target execution
- failure classification
- evidence-driven repair loop
- verification / stop

### Exit Criteria

- 至少一个代表性 ArkUI Gold Case end-to-end 跑通；
- Build/Test 失败后先分类再编辑；
- 最终 success 必须有 Test Evidence 支撑。

## Stage 9 — Execution Trace 与 Evaluation

### Deliverables

- complete trace serialization
- step/token/cost accounting
- benchmark runner
- deterministic metrics
- ablation configuration
- ArkUI Gold Cases

### Exit Criteria

任一 benchmark 任务可以从：

```text
Task
→ Plan
→ Evidence
→ Edit
→ Build/Test
→ Result
```

完整审计。

支持比较：

```text
Baseline
vs KB + source
vs KB + source + Semantic MCP
vs Full Memory / Context Agent
```

## 4. 每阶段架构检查

Stage 结束前都回答：

```text
这个能力是否尽可能 task-scoped？
是否重复了 ArkUI KB、source、clangd 或外部 MCP 的职责？
Provenance 是否保留？
Degraded / failure 是否可表示、可测试？
能否通过 Trace/Test/Evaluation 证明能力存在？
新抽象是否真的比把逻辑放错层更简单？
```

若答案不成立，先重新设计，不要继续扩展。
