# 当前任务（CURRENT_TASK）

## 1. Slice

**Stage 3 / Slice 3A — Context Contract & Budget Foundation**

所属清单：`PLAN.md` 的 Stage 3。本文件已替换完成验收的 Stage 2 / Slice 2D 交接状态。

## 2. Objective / In Scope

在不接入现有 Agent Loop 的前提下，建立 Context Builder 的最小稳定 contract：

- 只以 Stage 2 已有 `AgentState`（内含 `TaskMemory` / `EvidenceMemory`）为事实输入；
- 固定 `user_task`、`current_plan`、`working_memory`、`task_memory`、`evidence_memory` section 顺序；
- 将 section 内容确定性序列化为 compact canonical JSON；
- 建立明确的 Unicode character hard budget，保证渲染 context 有界；
- 预算不足时保留全部 section header，并按逆 section 顺序确定性截断内容；
- 继续把 `current_plan` / `current_step` 当作 Stage 2 `JsonValue` opaque contract；
- 以 deterministic tests 验证顺序、事实来源、opaque 值、确定性、hard bound、最小预算和非法输入。

## 3. Out of Scope

本 Slice 不实现 Task/Evidence relevance ranking、current-step Evidence 选择、Build/Test diagnostics
优先级、failure summarization、source snippet 选择/压缩、额外 Evidence/source dedup 策略、recent
observation section、`DefaultAgent.query()` runtime 接线、Planner/Plan/PlanStep、Replanner、Diagnose、
Stop Policy、RetrievalRouter、SemanticProvider、Task Relation Graph、repository index、RAG 或 persistent
memory。

## 4. 实际 Contract

### Input / Sections

- `ContextBuilder.build()` 接收现有 `AgentState` 并在读取前通过该模型完整重校验；不定义第二套 state、
  working/task/evidence memory。
- `user_task` 来自 `AgentState.goal`；`current_plan` 原样使用 opaque JSON 值；`working_memory` 使用
  其余 Stage 2 execution fields，并包含 opaque `current_step`；后两段直接使用现有 Task/Evidence
  Memory 的 JSON snapshot。
- 固定顺序由 `CONTEXT_SECTION_ORDER` 声明。3A 不根据内容重排字段或 facts；Evidence 继续使用
  `EvidenceMemory` 已有的 identity dedup 与 first-wins insertion order。
- 每个 section 的完整内容先使用 `sort_keys=True`、compact separators、Unicode 保留的 canonical JSON；
  超预算 suffix 可被 truncation marker 替代。不推断 Plan/PlanStep schema，不把模型消息或 conversation
  history 加入 context。

### Budget / Bounded Context

- `ContextBudget.max_chars` 是 strict integer，默认 16,000；单位明确为 Python Unicode character，
  不是 tokenizer/token 估算。
- `BuiltContext.char_count == len(BuiltContext.text)`，并强制不超过 `max_chars`。
- 预算压缩只做基础 suffix truncation：从 `evidence_memory` 向前依次压缩，较前 section 最晚被压缩；
  截断处使用 `…`，并由 `ContextSection.truncated` 显式表示。
- 最小合法预算 `MIN_CONTEXT_CHARS` 可容纳五个固定 header 及各一个 truncation marker；低于该值直接
  validation failure，避免静默丢掉 section contract。
- 这是确定性 hard bound foundation，不是 relevance selection、diagnostic priority、failure compression
  或 source snippet policy。

### Runtime Boundary

- 新模块不被 `DefaultAgent` / `InteractiveAgent` 调用，不改变现有 query、tool execution、trajectory、
  model message 或 Stage 2 state persistence 行为。
- Context models 是本轮派生视图，不写回 Memory，不做跨任务持久化。

## 5. Verification

```text
py -m pytest -q tests/agents/test_context.py
py -m pytest -q tests/agents/test_state.py tests/agents/test_task_memory.py tests/agents/test_evidence_memory.py tests/agents/test_state_integration.py
py -m pytest -q tests/agents
py -m pytest -q
py -m ruff check src tests
git diff --check
```

## 6. Status / Results / Blockers

Stage 3 / Slice 3A 实现完成，当前无 Blocker。已完成的验证：

```text
Slice 3A deterministic tests: 10 passed
Stage 2 contract baseline before edit: 174 passed
Agent regression: 366 passed
Full pytest: 718 passed, 4 skipped, 1 warning
Ruff: All checks passed
git diff --check: passed
```

首次全量 pytest 为 717 passed / 4 skipped / 1 environment failure：测试子进程 `PATH` 未找到已安装的
`arkui-ut-agent` console script。按既有 Windows 验证前置条件，仅为验证进程加入
`D:\Work\Python\Scripts` 后全量重跑通过；未修改代码或测试规避环境问题。Warning 为既有
cache-control deprecated 参数提示。

## 7. 未进入 Slice 3B 的依据

- `ContextBuilder` 当前完整序列化 Task/Evidence Memory，未实现“相关性”判断或筛选接口；
- Evidence 只沿用 Stage 2 已有 dedup/insertion order，未选择 current-step Evidence；
- 没有识别 Tool 类型、success/failure 或 diagnostics，因此不存在 Build/Test diagnostics priority；
- `failed_attempts` 与 source content 仅按普通 JSON 处理，没有摘要或 snippet 选择；
- 没有 runtime 调用点，也没有新增 Plan、PlanStep、decision 或 stop schema；
- `PLAN.md` 仅将 context sections / budget 标记完成，deterministic tests 标记进行中，其余 Stage 3
  checklist 保持未完成。

## 8. Completion Checklist

- [x] 复用现有 AgentState / TaskMemory / EvidenceMemory；
- [x] 固定 section contract 与 canonical rendering；
- [x] strict character budget、最小预算与确定性 truncation；
- [x] `current_plan` / `current_step` 保持 opaque JSON；
- [x] section order、budget、determinism、validation tests；
- [x] 未接入 runtime，未实现 3B+ 策略或 Stage 4 contract；
- [x] 全量 pytest、Ruff、diff check 最终验证。
