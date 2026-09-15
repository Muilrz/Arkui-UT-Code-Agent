# 当前任务（CURRENT_TASK）

## 1. Slice

**Stage 2 / Slice 2C — Evidence Memory & Identity/Dedup**

所属实施清单：`PLAN.md` 中的 **Stage 2 — AgentState 与 Memory**。
本文件只保留本 Slice；按用户要求在代码实现前替换已验收的 2B 状态。

## 2. Objective

实现最小 task-scoped Evidence contract、Evidence Memory、deterministic identity/dedup，并接入
AgentState 的 snapshot/serialization 数据契约。Working Memory 单一来源及 Task Memory contract 不变，
不接入主 Agent Loop。

## 3. In Scope

1. 检查现有 state、2A/2B tests 和 Stage 1 ToolResult / Observation / Provenance。
2. Evidence 表达 source、location、content/summary、复用的 Provenance、显式 step_id。
3. canonical JSON + SHA-256 identity；事实内容与 provenance 参与，step_id 不参与。
4. 同 identity 保留首次事实记录/step_id，保持首次插入顺序；不创建 occurrence history。
5. 支持显式 caller 提供记录，不自动从模型文本、hypotheses/messages 或 arbitrary Observation 晋升 Evidence。
6. AgentState 集成、legacy payload 恢复、full revalidation、snapshot isolation 和 round-trip tests。

## 4. Out of Scope

- step lifecycle / generator / 自动 step 分配；
- ToolCall → Observation → Evidence 自动链路或 step 自动绑定；
- memory update event / execution trace linkage；
- Agent Loop 接入、Context Builder、Planner / Replanner、Diagnose / Stop Policy；
- RetrievalRouter、SemanticProvider、Task Relation Graph、UT Workflow；
- persistent repository memory/index/cache、cross-task Evidence store；
- Vector DB / embedding / RAG、fuzzy merge / ranking / similarity search；
- Slice 2D。

## 5. Expected Deliverables

- Evidence 与 task-scoped EvidenceMemory；不复制 Stage 1 Provenance，不建立 taxonomy。
- deterministic identity 与 first-wins dedup，语义由测试固定。
- AgentState 可独立保存 Working / Task / Evidence Memory。
- deterministic tests 不依赖 LLM、网络、MCP 或 ArkUI repo。
- 仅更新 CURRENT_TASK / PLAN，无真实冲突时不修改长期架构文档。

## 6. Verification

规定执行：

```text
py -m pytest -q tests/agents/test_evidence_memory.py
py -m pytest -q tests/agents/test_state.py tests/agents/test_task_memory.py tests/agents/test_evidence_memory.py tests/agents/test_init.py tests/run/test_save.py tests/utils/test_serialize.py tests/tools/test_contracts.py
py -m pytest -q
py -m ruff check src tests
git diff --check
```

实际验证结果：

```text
py -m pytest -q tests/agents/test_evidence_memory.py
65 passed in 0.78s

py -m pytest -q tests/agents/test_state.py tests/agents/test_task_memory.py tests/agents/test_evidence_memory.py tests/agents/test_init.py tests/run/test_save.py tests/utils/test_serialize.py tests/tools/test_contracts.py
159 passed in 1.06s（包含全部 61 项 Slice 2A / 2B tests）

py -m pytest -q
660 passed, 4 skipped, 1 warning in 129.37s

py -m ruff check src tests
All checks passed!

git diff --check
passed
```

全量测试运行前把已安装项目的 `D:\Work\Python\Scripts` 加入本次验证进程 PATH，确保现有 console
script 可被 CLI test 找到；未修改测试规避环境问题。Warning 为既有 cache-control deprecated 参数提示。

## 7. Blockers

无 Blocker。代码与本地验证已完成，人工验收待进行；按要求提交推送后停止，不进入 Slice 2D。

## 8. Completion Checklist

- [x] Evidence required fields / content-summary / provenance 已实现并测试；
- [x] identity 字段语义 / canonicalization / round-trip 稳定性已测试；
- [x] dedup first-wins / insertion order / task isolation 已测试；
- [x] AgentState update / legacy payload / round-trip 已测试；
- [x] nested mutation 与 full revalidation 已测试；
- [x] hypotheses/messages 不自动晋升 Evidence 已测试；
- [x] 2A / 2B 回归、全量 pytest、Ruff、diff check 通过；
- [x] PLAN 按真实 Evidence 更新。

## 9. 实际 Contract 与更新边界

代码位于 project-owned `agents/state.py`；未新增 MemoryManager、registry、持久索引或 taxonomy。

### Evidence

- required：`source`（非空 Tool 来源标签）、`location`（非空定位或显式 None）、非空
  `list[Provenance]`、`step_id`（非空 caller trace 标签）。
- `content` 为 optional JSON value，`summary` 为 optional 非空字符串，至少一个非 None。
  提供的空字符串/空 list/空 dict 被拒绝；`0`、`False` 是有效 content。content 保留源码空白，
  summary/source/定位/step 标签去除首尾空白。所有 JSON number 必须有限。
- 直接复用 Stage 1 Provenance。每次校验从其原始字段重新构造现有类型，要求非空 source、有效
  optional location 和 JSON-compatible metadata，复制深层容器；不改变 Stage 1 contract。
- caller 必须从 producing Tool 显式提供 provenance；结构校验不证明其真实性或事实真伪。
- `identity` 是重新计算的 property，不是 persisted/cache 字段，不接受 caller 传入或伪造 identity。

### Identity

- 参与字段：source、location、content、summary、完整 provenance（source/location/metadata）。
  step_id 不参与，但仍须合法；不存储 occurrence history。
- 使用 JSON `sort_keys=True`、`separators=(",", ":")`、`ensure_ascii=False`、`allow_nan=False`，
  UTF-8 编码后计算标准 SHA-256，返回 `sha256:<64 hex>`。
- mapping 插入顺序不影响 identity；provenance 按每条 canonical JSON 排序后参与 digest，列表顺序
  不影响 identity，但重复 provenance 的数量保留语义。content 数组顺序仍有事实语义，变化会改变 identity。
- tests 固定 canonical 字段/形式、内容/provenance 修改、跨进程/hash seed、Unicode 和 round-trip 稳定性。

### Evidence Memory / AgentState

- `records: list[Evidence]` 默认独立空列表；construction、updated、恢复都按 identity first-wins dedup。
  所有记录先完成 validation，再去重，非法重复记录不能被隐藏。
- `add(record)` 是显式入口，返回 `(new_snapshot, inserted_bool)`；首次为 True，重复为 False。
  两种情况均返回隔离的新 memory；同 identity 保留首次完整记录及其 step_id，不合并后续记录。
- 不同 identity 保留，各记录按首次加入顺序排列；dedup 只在当前 task memory 实例中运行。
- `updated(**changes)` 整字段替换，不是 nested patch；parent 的入口仍是原有 `AgentState.updated()`：

  ```python
  next_memory, inserted = state.evidence_memory.add(explicit_tool_evidence)
  next_state = state.updated(evidence_memory=next_memory)
  ```

- `AgentState.evidence_memory` 使用 default_factory；旧 2A/2B dict/JSON payload 自动获得空 memory。
  Working Memory 无重复来源，Task Memory schema 不变，hypotheses/messages 不自动进入 Evidence。
- dump/恢复继续使用 Pydantic dump/validate APIs，父子快照、content、Provenance metadata 与原始输入隔离。
  顶层赋值禁止；原地 mutation、model_copy(update=...)、model_construct() 不是支持的更新接口。
  更新/identity/恢复时完整重校验，失败为 ValidationError；所有 dump（含 exclude）在转换前重校验，
  非法状态为 PydanticSerializationError。不接入 DefaultAgent 或 trajectory format。

## 10. Slice 2D 接口约束

- step_id 仅由 caller 显式提供，不定义 PlanStep，不生成或分配 step。
- 同事实跨 step first-wins，不保存 occurrence/event history。
- ToolCall/Observation/step/Evidence 的关联与 memory events 留给 2D。
- 不从 arbitrary Observation 猜测事实或自动晋升；provenance 必须由 caller 从 Tool 来源显式提供。
- 结构校验不证明事实真实性，不新增平行 provenance 系统。
