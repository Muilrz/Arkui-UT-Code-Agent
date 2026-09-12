# AGENTS.md

## 项目目的

本仓库开发一个面向 **ArkUI Ace Engine 大型 C++ 代码库**的 UT Code Agent，基于 `arkui-ut-code-agent` 的轻量运行骨架进行二次开发。

项目重点是 Agent 自身的控制与任务闭环，而不是重复建设 C++ 语义基础设施或全仓知识平台。

目标运行链路：

```text
Task
→ Agent State
→ Memory
→ Context Builder
→ Planner
→ Retrieval / Action Intent
→ RetrievalRouter / Tool Registry
→ Tool
→ Observation
→ Evidence + Memory Update
→ Diagnose
→ Replan / Continue / Verify / Finish
```

## 开发前阅读顺序

进行非简单修改前，按顺序阅读：

1. `docs/SPEC.md`：稳定需求、系统边界、必须满足的约束。
2. `docs/DECISIONS.md`：已经确认、不得静默推翻的架构决策。
3. `docs/TECHNICAL_ROADMAP.md`：目标架构与阶段性技术路线。
4. `docs/CURRENT_TASK.md`：当前实际开发目标、范围和阻塞项。
5. `docs/PLAN.md`：里程碑、依赖和验收检查点。

若文档冲突，优先级为：

```text
SPEC.md
> DECISIONS.md
> TECHNICAL_ROADMAP.md
> PLAN.md
> CURRENT_TASK.md
```

`CURRENT_TASK.md` 可以缩小当前范围，但不得突破 `SPEC.md` 的长期边界。

## 本项目负责的能力

项目自主维护：

- `Agent Loop` / `AgentState`
- Planning / Replanning
- Diagnose / Stop Policy
- Tool Routing
- Working / Task / Evidence Memory
- Context Builder
- 轻量 `RetrievalRouter`
- ArkUI KB 接入
- `rg` / source / git 当前源码事实查询
- 外部 clangd MCP 的 `SemanticProvider` 适配层
- Task Relation Graph
- Source Editing
- Build / Test
- UT Development / Repair Loop
- Execution Trace
- Evaluation

## 明确不做

禁止为了“方便”引入以下重型能力：

- Vector DB / Embedding RAG
- 自建 Component/File/Class/Function/Test 持久化索引
- Persistent Symbol Database
- Persistent Full-repository Code Graph
- Repository Snapshot / Incremental Refresh Framework
- 自研 C++ Parser
- 自研 clangd / LSP Server
- 自研 clangd MCP Server
- 长期跨任务 Repository Memory
- Multi-Agent Framework

若确实需要改变这些边界，必须先修改 `docs/SPEC.md`，并在 `docs/DECISIONS.md` 中记录新的架构决策。

## Retrieval 原则

根据当前信息缺口，优先使用最窄、最直接的信息源：

```text
ArkUI KB
→ 稳定领域导航：Component、source_paths、test_paths、API / Spec

rg / source
→ 当前 revision 事实：文件、实现、已有 UT、Fixture、Mock、当前 patch

External clangd MCP
→ 精确语义：definition、references、callers、callees、implementations、type relation
```

基本原则：

```text
KB        → where to look
rg/source → what the current code says
clangd    → what the exact symbol means and relates to
```

Semantic MCP 是升级路径，不是默认第一步。

不要建立一个隐藏的 Repository Knowledge Layer 去重复 KB、源码或 clangd 的职责。

## Semantic Integration 规则

本项目只集成现成 clangd MCP，不开发 Server。

首选集成目标：

```text
felipeerias/clangd-mcp-server
https://github.com/felipeerias/clangd-mcp-server
```

备用/参考：

```text
mpsm/mcp-cpp
https://github.com/mpsm/mcp-cpp
```

项目内部必须通过 `SemanticProvider` 隔离外部 MCP 实现：

```text
Planner / RetrievalRouter
        ↓
SemanticProvider
        ↓
McpSemanticProvider
        ↓ MCP
external clangd MCP server
        ↓ LSP
clangd
```

要求：

- Planner、Router、Memory、Task Relation Graph 不得直接依赖某个 MCP Server 的请求/响应 schema。
- 集成时必须 pin 已验证的外部 Server revision/version。
- 精确语义查询依赖可用的 `compile_commands.json` 或 clangd 等价编译配置。
- 可以调用 ArkUI/GN 已有能力生成/定位 compilation database，但不得自建新的全仓编译信息推断/索引系统。
- 外部结果统一归一化为项目自己的 `ToolResult` / `Observation` / Evidence。
- Semantic backend 至少支持 `available` / `degraded` / `unavailable`。
- Semantic 不可用时必须自动退化到 KB + `rg`/source，不能让整个 Agent 失效。

## Memory 规则

Memory 是**当前任务运行记忆**，不是代码仓长期知识库。

### Working Memory

保存当前执行状态：

```text
goal
current_plan
current_step
next_action
open_questions
hypotheses
blocking_issue
```

### Task Memory

保存当前任务已经确认的稳定事实：

```text
target_component
target_files
target_classes
target_functions
changed_files
relevant_tests
fixtures
mocks
build_target
completed_steps
failed_attempts
important_decisions
```

### Evidence Memory

只保存工具产生或验证过的工程事实，例如：

```text
KB result
rg result
source snippet
git diff
semantic result
compiler diagnostic
test result
```

重要结论必须可以追溯到 Evidence。模型推测不能直接升级为 confirmed fact。

每条 Evidence 至少保留：

```text
source
location
summary/content
provenance
step_id
```

## Context Builder 规则

不要把持续增长的原始 conversation history 整体传给模型。

每轮上下文优先级：

1. 当前 User Task 与 Current Plan；
2. 当前 Step 直接相关 Evidence；
3. 最新 Build/Test Diagnostics；
4. 下一步必要的 Task Memory；
5. 相关历史失败的压缩摘要。

必须进行：

- Evidence 去重；
- Source snippet 选择；
- 旧失败压缩；
- 无关历史过滤；
- Context size 控制。

## Agent 行为规则

Agent 必须显式区分：

```text
缺 Evidence      → Retrieve
原 Plan 不成立    → Replan
Edit 有问题       → Repair
Build/Test 失败   → Diagnose，再决定下一步
目标可能完成      → Verify
验证通过          → Finish
```

如果出现：

```text
相同 Tool
+ 相同 Input
+ 相同 Observation
```

不得机械重试，应触发 Replan；若 Replan 后仍没有新 Evidence，则 Stop。

Stop reason 至少包括：

- success
- step_limit
- cost_limit
- repeated_failure
- no_new_evidence
- tool_unavailable
- irrecoverable_failure

## UT Workflow 规则

UT 开发优先使用最小可验证闭环：

```text
识别目标行为
→ 定位源码/测试区域
→ 阅读当前实现和相似 UT
→ 确认 Fixture / Mock / Build Target
→ 编写或修改 UT
→ 运行最小相关 Target
→ 分类失败
→ 补充 Evidence
→ Repair / Replan
→ 重跑
→ Verify
```

失败至少区分：

- Compile Error
- Mock Missing
- Assertion Failure
- Wrong API Usage
- Wrong Fixture
- Wrong Assumption
- Environment / Build Failure

禁止看到失败后直接盲目重写测试。

## Tool Contract

优先统一 Tool 结果结构：

```text
ToolResult
- success
- data
- summary
- diagnostics
- provenance
```

所有 ToolResult 进入 Memory、Trace 或 Diagnose 前，先归一化为 `Observation`。

Tool wrapper 应保持薄：

- Planner 决定“下一步缺什么”。
- `RetrievalRouter` 决定“这个信息需求交给哪个 Tool”。
- Tool 只负责执行并返回结果。

## Task Relation Graph

只记录当前任务中**已经通过 Evidence 实际发现**的关系。

初始节点类型：

- Component
- File
- Class
- Function
- Test

初始关系类型：

- LOCATED_IN
- DEFINES
- REFERENCES
- CALLS
- IMPLEMENTED_BY
- RELATED_TEST
- KB_RELATED
- CHANGED_IN

每条关系必须带 provenance。

禁止：

- 主动扫描全仓构图；
- 建 Graph DB；
- 生成 Repository Snapshot；
- 为了“以后可能有用”长期保存跨任务 Graph。

## 实现与修改原则

- 优先修改已有模块，不为未来假想需求提前建立抽象。
- 新抽象必须有明确调用方和测试价值。
- 确定性逻辑优先写单元测试。
- 错误/降级路径必须与成功路径一样可测试。
- 对外部事实保留 provenance。
- 修改后优先运行最小相关测试，再扩大验证范围。
- 不允许通过“架构名词存在”代替真实可运行能力。

## Definition of Done

一个开发 Slice 只有满足以下条件才算完成：

1. 代码实现与 `SPEC.md` / `DECISIONS.md` 一致；
2. 有对应测试或可重复验证命令；
3. Error / degraded path 被处理；
4. Execution Trace 或日志能解释关键决策；
5. 没有引入被明确禁止的全仓知识系统；
6. 若长期契约发生变化，相关文档已同步；
7. `CURRENT_TASK.md` 已更新为真实状态，而不是计划状态。
