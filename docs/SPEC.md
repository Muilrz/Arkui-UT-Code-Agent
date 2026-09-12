# ArkUI UT Code Agent 技术规格（SPEC）

## 1. 项目定位

开发一个面向 **ArkUI Ace Engine 大型 C++ 代码库**的源码分析、UT 开发与 UT Repair Code Agent。

项目基于 `mini-swe-agent` 的轻量运行骨架进行二次开发，优先复用：

- Model abstraction / adapters
- Environment abstraction
- CLI / Configuration
- 基础 trajectory serialization
- step / cost / time limit

本项目主要自主设计 Agent Control Plane，而不是从零重复建设通用 Code Agent 基础设施。

## 2. 核心目标

最终系统必须真实支持：

- Planning / Tool Call / Observation Agent Loop
- Planning / Replanning
- Working / Task / Evidence Memory
- Context Builder
- 基于 ArkUI KB、`rg`/source、外部 clangd MCP 的分层 Retrieval
- 轻量 RetrievalRouter
- Task Relation Graph
- Source Reading / Editing
- Build / Test
- UT Development / Iterative Repair
- Execution Trace
- Evaluation

所有核心能力都必须能在实际代码、测试、Execution Trace 或 Evaluation 结果中找到对应证据。

## 3. 目标 Runtime Model

```text
TASK
 ↓
UNDERSTAND
 ↓
PLAN
 ↓
RETRIEVE / ACT
 ↓
OBSERVE
 ↓
UPDATE MEMORY
 ↓
DIAGNOSE
 ├─ 信息不足        → RETRIEVE
 ├─ 原计划失效      → REPLAN
 ├─ 修改失败        → REPAIR
 ├─ Build/Test 失败 → DIAGNOSE → REPAIR
 └─ 已满足目标      → VERIFY
                         ↓
                       FINISH
```

Agent 必须显式维护任务状态，不得把完整 message history 当成唯一状态来源。

最低状态包括：

```text
goal
current_plan
current_step
information_gap
next_action
blocking_issue
stop/retry state
```

## 4. Memory Architecture

Memory 是任务执行记忆，不是 Repository Knowledge Database。

### 4.1 Working Memory

保存短期可变执行状态：

```text
goal
current_plan
current_step
next_action
open_questions
hypotheses
blocking_issue
```

回答：Agent 当前正在做什么？

### 4.2 Task Memory

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

回答：当前任务已经知道什么？

### 4.3 Evidence Memory

保存 Tool 产生的工程事实：

```text
KB result
rg result
source snippet
git diff
semantic result
compiler diagnostic
test result
```

每条 Evidence 至少包括：

```text
source
location
content / summary
provenance
step_id
```

模型推测不得直接记为 confirmed evidence。

## 5. Context Builder

每轮模型调用只构造当前任务需要的上下文，不直接携带完整历史。

典型结构：

```text
Agent Instructions
+ User Task
+ Current Plan
+ Working Memory
+ Relevant Task Memory
+ Relevant Evidence
+ Recent Observation
```

职责：

- 历史压缩；
- Evidence 去重；
- Source snippet 选择；
- 旧失败摘要；
- 无关信息过滤；
- 当前 Step Evidence 优先；
- Build/Test Error 优先；
- 控制 Context 大小不随 trajectory 无限增长。

Memory 负责“保存”，Context Builder 负责“这一轮给模型看什么”。

## 6. 分层 Retrieval Strategy

本项目不建设自有多级索引系统。

不创建：

```text
Component Index
File Index
Class Index
Function Index
Test Index
Symbol SQLite
Vector Index
Search Database
```

所谓 Component / File / Class / Function / Test 多级检索，是使用不同信息源完成不同粒度的信息需求。

### 6.1 ArkUI KB：领域导航层

依赖 ArkUI 官方 KB，例如：

```text
docs/kb/
context_registry.json
kb_search
```

主要解决：

- Component 在哪里；
- source_paths / test_paths；
- API / Spec；
- ArkUI 子系统归属；
- 稳定领域知识。

定位：

> 找到值得看的区域。

本项目不复制或重新维护 ArkUI 官方 KB。

### 6.2 `rg` / Source：当前源码事实层

主要使用：

```text
rg_search
read_file
list_files
git_diff
git_status
```

解决：

- 当前 revision 真正写了什么；
- 函数/类/UT 在哪里；
- Fixture / Mock 如何使用；
- 当前 patch 修改了什么。

KB 如与当前源码冲突，以当前源码事实为准。

定位：

> 验证当前 checkout 的真实代码事实。

### 6.3 External clangd MCP：精确语义层

仅在需要精确语义时调用，例如：

- 同名 Symbol 无法确认；
- overload ambiguity；
- exact definition / references；
- callers / callees；
- implementations；
- inheritance / type relation。

定位：

> 证明精确 C++ Symbol 关系。

最终策略：

```text
KB
→ where to look

rg/source
→ what the code currently says

clangd MCP
→ what this exact symbol means and relates to
```

## 7. Semantic MCP 集成边界

本项目**不实现 clangd MCP Server**。

首选直接集成现成开源实现：

```text
felipeerias/clangd-mcp-server
https://github.com/felipeerias/clangd-mcp-server
```

备用/参考：

```text
mpsm/mcp-cpp
https://github.com/mpsm/mcp-cpp
```

### 7.1 项目内部抽象

项目只维护薄的：

```text
SemanticProvider
```

建议接口：

```text
find_definition(...)
find_references(...)
find_callers(...)
find_callees(...)
find_implementations(...)
get_hover(...) / symbol_at(...)
```

至少提供：

```text
McpSemanticProvider
UnavailableSemanticProvider
```

Provider 状态至少支持：

```text
available
degraded
unavailable
```

### 7.2 Provider 职责

`McpSemanticProvider` 只负责：

- 启动/连接配置好的外部 MCP Server；
- 把项目的 semantic operation 映射到 pin 版本的 MCP Tool；
- 将外部结果转换为 `ToolResult` / `Observation` / Evidence；
- 统一错误与 capability/health 状态；
- 隐藏 Server-specific schema。

Planner、RetrievalRouter、Memory、Task Relation Graph 不得直接依赖外部 MCP schema。

### 7.3 compile_commands.json 前置条件

高质量 clangd 语义依赖目标源码环境具备 clangd 需要的编译信息，通常是：

```text
compile_commands.json
+ 必要 clangd args/config
```

对于 ArkUI/Ace Engine，这属于**环境集成前置条件**，不是新的 Repository Intelligence 子系统。

项目允许：

- 检测现有 compilation database；
- 配置其路径；
- 调用 ArkUI/GN 已有工具生成 compilation database；
- 记录必要 clangd 参数。

项目禁止：

- 自建全仓编译命令推断系统；
- 自建持久语义索引；
- 为了 Semantic MCP 再实现一套源码知识数据库。

当 Semantic backend 或 compilation database 不可用时：

```text
semantic_state = unavailable/degraded
```

Agent 必须继续以：

```text
ArkUI KB + rg/source
```

工作。

## 8. RetrievalRouter

`RetrievalRouter` 是轻量映射层，不是第二个 Planner。

Planner 决定：

> 当前缺什么信息？

Router 决定：

> 这个信息需求优先调用哪个 Tool？

建议 Retrieval Intent：

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

典型映射：

```text
find_component
→ kb_search

find_source
→ kb_search
→ 必要时 rg

find_test
→ kb_search 获取 test_paths
→ rg 查 Fixture / Case / Mock

read_implementation
→ read_file

search_text
→ rg_search

resolve_symbol
→ rg/source
→ 必要时 SemanticProvider

find_references/callers/callees/implementations
→ SemanticProvider
```

## 9. Task Relation Graph

保留任务级 Code Graph 思想，但不建设 Persistent Full-repository Graph。

节点初始类型：

```text
Component
File
Class
Function
Test
```

关系初始类型：

```text
LOCATED_IN
DEFINES
REFERENCES
CALLS
IMPLEMENTED_BY
RELATED_TEST
KB_RELATED
CHANGED_IN
```

关系来源：

```text
KB result
rg/source Evidence
SemanticProvider Evidence
git diff
```

所有关系必须保留 provenance。

Graph：

- 只存在于当前任务；
- 只记录已经实际发现的关系；
- 不主动扫描全仓；
- 不建 Graph Database；
- 不做 snapshot / refresh。

主要服务：

- Context Builder
- Planning
- Explainability
- Evaluation

## 10. Tool System

Agent 至少具备：

```text
Knowledge
└── kb_search

Repository
├── rg_search
├── read_file
├── list_files
├── git_diff
└── git_status

Semantic
└── semantic_provider

Editing
├── apply_patch
└── write_file

Execution
├── run_command
├── build
└── test
```

统一结果模型：

```text
ToolResult
- success
- data
- summary
- diagnostics
- provenance
```

统一转换为 `Observation`，进入：

```text
Agent Loop
+ Evidence Memory
+ Execution Trace
```

## 11. UT Development / Repair Workflow

基本流程：

```text
Target / Requirement
        ↓
识别 Component / Function / Behavior
        ↓
RetrievalRouter
        ↓
ArkUI KB
        ↓
source_paths / test_paths
        ↓
rg + source
        ↓
目标实现 + Existing Test
        ↓
Fixture / Mock / Similar Case
        ↓
必要时 SemanticProvider
        ↓
形成 UT Plan
        ↓
生成 / 修改 UT
        ↓
运行最小 Test Target
        ↓
Observation
```

失败后先分类：

```text
Compile Error
Mock Missing
Assertion Failure
Wrong API Usage
Wrong Fixture
Wrong Assumption
Environment / Build Failure
```

然后：

```text
Diagnose
 ↓
判断缺失 Evidence
 ↓
RetrievalRouter
 ↓
补充 KB / source / semantic evidence
 ↓
Update Memory
 ↓
Replan / Repair
 ↓
重新执行最小 Target
```

最终必须：

```text
PASS
```

或由 Stop Policy 明确终止。

## 12. Stop / Retry

至少支持：

```text
success
step_limit
cost_limit
repeated_failure
no_new_evidence
tool_unavailable
irrecoverable_failure
```

相同 Tool + Input + Observation 不允许机械重试。

## 13. Execution Trace

每个任务至少记录：

```text
Task
Initial Plan

每个 Step
├── current_goal
├── retrieval/action intent
├── selected_tool
├── tool_input
├── observation
├── evidence_added
├── memory_update
└── decision

Code Changes
Build Result
Test Result
Final Result
Model Calls
Token / Cost
```

Trace 用于：

- Agent Debugging
- Failure Analysis
- Evaluation
- Demo / 能力证明

## 14. Evaluation

保留或建设：

```text
benchmarks/
tests/fixtures/
docs/evaluation/
```

作为 ArkUI Gold Data / Evaluation Assets。

建议 Gold Cases 覆盖：

```text
Button
Text
Menu
Creation
Property
Layout
Overlay
```

Ablation：

```text
Baseline Agent
        vs
KB + rg/source
        vs
KB + rg/source + Semantic MCP
        vs
完整 Memory / Context Agent
```

主要指标：

- Source Localization Accuracy
- Test Localization Accuracy
- Symbol / Call Relation Accuracy
- Context Precision
- Tool Success Rate
- UT First-pass Rate
- UT Final Pass Rate
- Build Success Rate
- Iteration Count
- Token Usage
- Cost

确定性自动指标优先，其次人工校验，必要时再使用 LLM-as-a-Judge。

## 15. 明确不做

本项目禁止重新演化为：

```text
Repository
    ↓
Full Scan
    ↓
Custom Index
    ↓
Persistent Graph
    ↓
Snapshot
    ↓
Incremental Refresh
```

明确不建设：

- 自建 Component / File / Class / Function / Test Index
- Persistent Symbol Database
- Persistent Full-repository Code Graph
- Retrieval Database / Search Ranking Engine
- Vector Database / Embedding RAG
- Repository Knowledge Snapshot
- Dependency-driven Incremental Refresh
- 自研 C++ Parser
- 自研 clangd / LSP / clangd MCP Server
- 长期跨任务 Repository Memory
- Multi-Agent Framework

最终所有权原则：

```text
稳定 ArkUI 领域知识 → 官方 KB
当前 revision 事实 → rg / source
精确 C++ 语义 → 外部 clangd MCP
当前任务状态 → Agent Memory
```
