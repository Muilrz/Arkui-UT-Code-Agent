# 当前任务（CURRENT_TASK）

## 1. Slice

**Stage 3 / Slice 3C — Failure Compression & Source Snippet Control**

所属清单：`PLAN.md` 的 Stage 3。本文件已替换完成验收的 Stage 3 / Slice 3B 交接状态。

## 2. Objective / In Scope

完成 Stage 3 剩余的确定性 Context 压缩能力：

- 对相关 `TaskMemory.failed_attempts` 与历史 Build/Test failure Evidence 做有界压缩；
- 只针对仓库已有 `read_file` / `rg_search` 实际 payload 进行 source snippet 选择、长度控制和重复消除；
- 保持 3B current-step Evidence → Build/Test diagnostics → relevant Evidence 优先级；
- 保持 3A Unicode character hard budget 与固定 section contract；
- 所有变化只发生在 Context 派生视图，不修改 Stage 2 Memory/Evidence、identity 或 dedup contract；
- deterministic tests 覆盖长失败历史、重复/超长 snippet、历史 failure Evidence 和预算压力。

## 3. Out of Scope

本 Slice 不实现 `DefaultAgent.query()` runtime 接线、Planner / Replanner / Diagnose / Stop Policy、
RetrievalRouter、SemanticProvider、Task Relation Graph、LLM summarization、repository index、RAG、persistent
memory，也不为未来工具建立通用 source parser。

## 4. 实际 Compression Contract

### Historical Failure Compression

- 3B focus 选择后，`failed_attempts` 先折叠空白并按 case-folded exact text 去重；使用最新条目作为展示
  文本，并附加 deterministic repeated count。
- 最多保留最近 5 类相关 failure；其余使用一条 `older relevant failure(s) omitted` count marker 表示。
- 每条展示文本（含 repeated suffix）最多 240 characters；不做语义改写或 LLM summarization。
- current-step Evidence 数量不受历史 failure limit 影响；非 current-step 的失败 `build` / `test`
  Evidence 最多保留最新 3 条，diagnostics、identity、step_id、provenance 继续保留。
- `build` / `test` failure envelope 中的 `data.output` 最多 1,200 characters，使用确定性 head/tail marker；
  diagnostics 不摘要、不分类。

### Established Source Payload Control

只识别已由 `RepositoryReadTools` 产生并经 Stage 2 envelope 包装的两种结构；其他内容保持原样：

```text
read_file: content.data.{path, content, start_line, end_line, total_lines}
rg_search: content.data.matches[{path, line, column, text}]
```

`read_file`：

- 有 focus match 时保留每个匹配行及前后各一行；无 match 时保留 head/tail；
- 最多 40 行、1,200 characters，gap 与字符截断均使用显式 omission marker；
- 在已按 3B 优先级排序的 Context Evidence 中，对相同 path/range/raw content 的后续 snippet 使用
  duplicate marker，优先记录的 snippet 保留。

`rg_search`：

- 按 `(path, line, column, raw text)` exact key 在当前 Context 内去重；
- 最多保留 20 条 match，每条 text 最多 240 characters；
- Context view 的 `count` 反映实际展示数量；发生原始或 Context 截断时 `truncated=true`。

所有输出 Evidence 仍携带基于原 Stage 2 record 计算的 `identity`。压缩后的 Context payload 不写回
EvidenceMemory，因此不会改变 identity、first-wins dedup、provenance 或 trajectory snapshot。

### Budget / Priority Interaction

- section hard bound 与 3B fitting 顺序不变：`task_memory` → `evidence_memory` → `working_memory` →
  `current_plan` → `user_task`；
- current-step Evidence 始终排在历史 diagnostics 和普通 Evidence 之前；历史数量限制与 source control
  在最终 JSON / suffix fitting 前执行，使普通历史不能通过无限增长挤掉可容纳的关键 Evidence；
- 极端小于关键 Evidence 自身的预算仍可能触发最终 suffix truncation，不伪造无条件保留保证。

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

Stage 3 / Slice 3C 实现完成，当前无 Blocker。已完成验证：

```text
Stage 3 deterministic context tests: 18 passed
Context + Stage 2 contract regression: 192 passed
Agent regression: 374 passed
Full pytest: 726 passed, 4 skipped, 1 warning
Ruff: All checks passed
git diff --check: passed
```

全量测试进程按既有 Windows 验证前置条件加入已安装 console script 目录
`D:\Work\Python\Scripts`；未修改测试规避环境问题。Warning 为既有 cache-control deprecated 参数提示。

## 7. Slice / Stage Boundary

- 没有修改 Stage 2 Evidence identity/dedup 或 Memory schema；
- 没有 LLM summary、failure diagnosis 或 failure classification；
- 没有解析 C++ 语义，只对 Tool 已有结构做 text/line/match 控制；
- 没有 runtime model-call 接线、Plan/PlanStep 或 control-loop contract；
- Stage 3 Context Builder checklist 已完成；runtime integration 明确保留给 Stage 4。

## 8. Completion Checklist

- [x] relevant historical failure deterministic compression；
- [x] historical Build/Test failure count/output control；
- [x] actual read_file relevance window / line / char control；
- [x] actual rg_search exact dedup / match / text control；
- [x] Context-local duplicate snippet elimination；
- [x] current-step / diagnostics priority 与 hard budget 保持；
- [x] Stage 2 Memory/Evidence 不变且相关回归通过；
- [x] 未接入 runtime 或越界实现后续 Stage；
- [x] 全量 pytest、Ruff、diff check 最终验证。
