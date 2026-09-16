# 当前任务（CURRENT_TASK）

## 1. Slice

**Stage 3 / Slice 3B — Relevant Memory & Evidence Selection**

所属清单：`PLAN.md` 的 Stage 3。本文件已替换完成验收的 Stage 3 / Slice 3A 交接状态。

## 2. Objective / In Scope

在 3A 固定 section / character budget contract 上增加最小、确定性的 Task/Evidence 选择：

- 继续只读取 Stage 2 `AgentState`、`TaskMemory`、`EvidenceMemory`；
- 根据当前 User Task 与 Working Memory focus 选择下一步相关 Task Memory；
- 精确匹配 current-step Evidence 并置于 Evidence section 最前；
- 无论词项是否相关，优先保留实际失败的 `build` / `test` diagnostic Evidence；
- 普通 Evidence 仅保留与 focus 有确定性词项交集的记录，无匹配时保留最新一条作为 fallback；
- Context Evidence view 显式带出派生 `identity`，并原样保留 `step_id`、`provenance` 和 Stage 2 记录字段；
- 调整 3A budget fitting，使 Task Memory 先于已排序 Evidence 尾部被截断；
- 通过 crowded-history tests 验证关键 Evidence 不被普通历史 Evidence 挤掉。

## 3. Out of Scope

本 Slice 不实现 Planner / Replanner / Diagnose / Stop Policy、`DefaultAgent.query()` runtime 接线、
failure summarization、source snippet selection/compression、RetrievalRouter、SemanticProvider、Task Relation
Graph、repository index、RAG、persistent memory 或新的 state/memory/evidence model。

## 4. 实际 Selection Contract

### Focus / Task Memory

- Focus 只来自 `goal`、opaque `current_plan` / `current_step`、`information_gap`、`next_action`、
  `open_questions`、`blocking_issue`；不使用未确认的 `hypotheses`。
- 字符串先确定性拆分 camel case，再 case-fold；过滤 `pattern`、`test`、`mock`、`fixture`、`step`、
  常见文件后缀等少量结构性通用词，避免仅因代码类型后缀产生伪相关。
- 已确认的 `target_component` / `build_target` 作为两个小型任务锚点保留；Task Memory 的十个 collection
  fields 只保留与 focus 有词项交集的原始条目，并保持字段与条目顺序。
- `completed_steps` / `failed_attempts` 只按普通完整条目筛选，不摘要、不改写。

### Evidence Priority / Preservation

Evidence 选择顺序固定为：

1. `AgentState.current_step` 为非空字符串且与 `Evidence.step_id` 精确相等；
2. `Evidence.source` 为 `build` / `test`，且 Stage 2 envelope 明确为 `success=false` 并含 diagnostics；
3. 排除 `step_id` 后，Evidence 其余字段与 focus 有词项交集；
4. 前三类均为空时，仅保留 EvidenceMemory 最新一条。

同一优先级内使用 EvidenceMemory 中的较新记录优先，规则无模型调用、无 fuzzy/embedding ranking。
`current_step` 为 object/list 等 opaque JSON 时不猜测其中的 step ID，只参与普通 focus 提取。

输出记录为 `{identity, source, location, content, summary, provenance, step_id}`；`identity` 使用 Stage 2
原有 property 计算，其余字段来自原 Evidence JSON snapshot。Context Builder 不修改 Memory，也不把推测
升级为事实。

### Budget Interaction

- section 顺序与 `ContextBudget.max_chars` hard bound 不变；
- budget fitting 顺序变为 `task_memory` → `evidence_memory` → `working_memory` → `current_plan` →
  `user_task`，不再让 3A 的机械逆序优先于 Evidence policy；
- Evidence section 内 current-step / diagnostic records 位于普通记录之前，suffix truncation 优先影响普通
  历史尾部；当预算足以容纳关键记录本身时，普通历史无法将其挤出；
- 仍未实现 record-aware snippet compression；极端小预算可截断关键记录，这是 hard bound 下不可避免的
  3C+ 问题，不在本 Slice 伪造无条件保留保证。

## 5. Verification

```text
py -m pytest -q tests/agents/test_context.py
py -m pytest -q tests/agents/test_context.py tests/agents/test_state.py tests/agents/test_task_memory.py tests/agents/test_evidence_memory.py tests/agents/test_state_integration.py
py -m pytest -q tests/agents
py -m pytest -q
py -m ruff check src tests
git diff --check
```

## 6. Status / Results / Blockers

Stage 3 / Slice 3B 实现完成，当前无 Blocker。已完成验证：

```text
Slice 3A+3B deterministic context tests: 14 passed
Context + Stage 2 contract regression: 188 passed
Agent regression: 370 passed
Full pytest: 722 passed, 4 skipped, 1 warning
Ruff: All checks passed
git diff --check: passed
```

全量测试进程按既有 Windows 验证前置条件加入已安装 console script 目录
`D:\Work\Python\Scripts`；未修改测试规避环境问题。Warning 为既有 cache-control deprecated 参数提示。

## 7. 未进入后续 Slice 的依据

- 没有对 `failed_attempts` 或 diagnostics 做摘要、分类或 Diagnose；
- 没有按语言结构选择/压缩 source snippet，仍只做 section suffix fitting；
- 没有新增 recent observation section 或 Evidence occurrence store；
- 没有 runtime 调用点、Plan/PlanStep 或 control-loop contract；
- `PLAN.md` 的 failure compression、Evidence/source snippet dedup 仍未完成，Stage 3 deterministic tests
  保持进行中。

## 8. Completion Checklist

- [x] relevant Task Memory deterministic selection；
- [x] current-step Evidence exact-match priority；
- [x] failing Build/Test diagnostics priority；
- [x] Evidence identity / step_id / provenance preservation；
- [x] crowded ordinary history budget protection test；
- [x] Stage 2 Memory/Evidence contract 回归通过；
- [x] 未接入 runtime，未提前实现 summarization / snippet compression；
- [x] 全量 pytest、Ruff、diff check 最终验证。
