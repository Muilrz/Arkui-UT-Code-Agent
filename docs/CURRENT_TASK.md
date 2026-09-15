# 当前任务（CURRENT_TASK）

## 1. Slice

**Stage 2 / Slice 2D — Step / Provenance Integration & State Persistence**

所属清单：`PLAN.md` 的 Stage 2。本文件在代码实现前替换已验收的 2C 状态。

## 2. Objective / In Scope

最小集成现有 DefaultAgent：task-local deterministic step_id、实际 Tool execution 的 Observation →
Evidence 显式确定性映射、EvidenceMemory 更新、Memory Update Event、trajectory AgentState snapshot
与独立恢复。复用 Stage 1 Provenance 和现有命令归一化，不重写 Loop 或工具业务实现。

保留 Working Memory 单一来源、2A–2C update/serialization/mutation/isolation contract、identity/dedup
规则和 legacy state 默认值。以 deterministic tests 验证 Stage 2 Acceptance。

## 3. Out of Scope

Context Builder、Planner/Replanner、Diagnose/新 Stop Policy、RetrievalRouter、SemanticProvider、
Task Relation Graph、ArkUI UT Workflow、persistent repository memory/index、Vector DB/RAG、
EventBus/event sourcing/replay/store、cross-task event history。完成后不进入 Stage 3。

## 4. Verification

```text
py -m pytest -q tests/agents/test_state_integration.py
py -m pytest -q tests/agents/test_state.py tests/agents/test_task_memory.py tests/agents/test_evidence_memory.py tests/agents/test_state_integration.py
py -m pytest -q tests/agents tests/run/test_save.py tests/utils/test_serialize.py tests/tools
py -m pytest -q
py -m ruff check src tests
git diff --check
```

## 5. Status / Blockers

本地代码、Slice/相关回归及全量验证已完成，无 Blocker。提交推送后停止等待人工验收，
不进入 Stage 3。CI 结果不作为本地测试结果冒报。

实际验证结果：

```text
Slice 2D: 48 passed in 0.59s
2A–2D contract regression: 174 passed in 1.14s
Related regression (agents / save / serialize / tools): 513 passed, 1 skipped in 103.22s
Full pytest: 708 passed, 4 skipped, 1 warning in 124.60s
Ruff: All checks passed!
git diff --check: passed
```

全量验证进程 PATH 加入已安装项目的 `D:\Work\Python\Scripts`，确保既有 console script 被 CLI tests
找到；未修改测试规避环境问题。Warning 为既有 cache-control deprecated 参数提示。

## 6. 实际 Contract / Integration 边界

### Step

- 每次通过现有 limit checks、开始 query 时分配 task-local `step-1`、`step-2` …；模型 parse failure
  仍属于已开始 step，未通过 limit checks 的尝试不分配。一个 query 的所有实际 actions 共用该 ID。
- ordinal 与现有 query/step 边界一致；Interactive human query 使用同一入口，避免伪造 n_calls。
  `n_calls`、cost/time/format-error policy 保持原有 accounting/行为，不新增 stop policy。
- ID 从 step 开始保持 active 到下一次 query；退出保留最后 step。`current_step` 只是 opaque string，
  未升级为 PlanStep。模型/人类 query 消息中的 step_id 由 execution 覆盖，不信任模型提供的 ID。
- run 开始清空 task-local state/events/tool records/ordinal；保留原有 agent-instance cost/call accounting。
  空的 legacy run task 用明确 `Unspecified task` goal；不从 chat history 推断 goal。

### Tool / Observation / Evidence

- DefaultAgent / InteractiveAgent 共用 `_execute_action`，仍调用 Environment.execute 一次。
  原 shell output 使用 Stage 1 已有 execution normalizer → ToolResult → Observation，不重跑命令，
  不修改 Registry、Tool business implementation、submission sentinel 或 confirmation 行为。
- execution shell adapter 是已执行 command 的 provenance producer，保留 command、configured cwd（未知为
  None）、returncode、timeout、backend/dialect、env override names，不保存 env 值。对于实际返回的
  ToolResult / Observation 直接复用其 provenance；不补造缺失 provenance。
- project-owned internal `_evidence_from_tool_observation` 只由 Tool execution 边界调用。
  source = tool_name，content 为完整 `{success, data, diagnostics}` envelope，summary 为 Tool summary，
  provenance 使用 Stage 1 原模型并复制深层 metadata，step_id 来自 active execution。
- 所有 provenance/location 保留；若只有一个非 None location 则使用它作为顶层定位，否则顶层为 None。
  不提炼事实、不猜字段语义、不提供模型文本 → Evidence 的公开入口；结构验证不证明真实性。
- 成功/失败 Observation，只要具有实际 data、diagnostics 或非空 Tool summary，都可记录。
  failure Evidence 明确保留 success=False，描述实际失败结果，不声称 action 成功或任务已验证。
- 缺失 provenance → `missing_provenance`；非法 provenance/非 JSON payload → `invalid_provenance_or_payload`；
  无 data/diagnostics/summary → `empty_result`。skip 不更新 EvidenceMemory，记录 warning 和 trace skip reason，
  不强制 stringify 非 JSON 工程事实。Stage 1 adapter 自己报告的 invalid raw result/timeout/backend failure
  diagnostics 是真实失败结果，可以入 Evidence；malformed raw output 给 formatter 显式 failure view。
- Submitted / interruption / 未归一化抛出的异常没有可用 raw Tool result：保留原 control flow，记录
  `interrupted` 调用，不把 exit/exception text 晋升 Evidence。已有成功 Tool 的证据仍保留。
- assistant extras、messages、hypotheses 永不作为 Observation/Evidence 输入。TaskMemory 不自动更新。

### Identity / Memory Update Event

- 2C identity、first-wins dedup、插入顺序规则不变。首次插入更新 AgentState 并记录 Evidence event；
  duplicate 不追加事实/不伪造 Evidence update，首次 step_id 保留。本次 step 的实际 Observation 仍在
  tool_executions 中关联相同 identity；这里是现有 trajectory trace，不是新的 occurrence store。
- `MemoryUpdateEvent`：required nonempty step_id、非空 regions（working_memory/task_memory/evidence_memory）、
  optional evidence_identities（sha256 digest 列表）。frozen model + immutable tuples，JSON 使用 arrays，
  unknown/invalid fields 拒绝；dump 完整重校验。无随机 ID、timestamp、EventBus、replay 或 persistent store。
- step/action Working snapshot 更新及真实 EvidenceMemory 插入成功之后才追加 event。
  events 只记录已应用的区域更新，不是 patch/replay command；当前没有自动 TaskMemory 事实写入策略。
- 状态更新仍只使用 AgentState.updated() / EvidenceMemory.add()；父子 snapshot 隔离及 2A–2C
  full validation/mutation boundary 不变。原地 mutation / unsafe copy 不是支持的更新接口。

### Persistence / Compatibility

- 保留 `trajectory_format = arkui-ut-code-agent-1.1` 和原 messages/info/config；仅 additive 增加
  `agent_state`（完整 JSON snapshot）、`memory_updates`、`tool_executions`。
- pre-run `agent_state=None`，不伪造已执行 task state。三个新字段由 agent 权威生成，extra dict 不允许
  递归覆盖/patch；其它字段保持原 recursive_merge 行为。
- `AgentState.from_trajectory()` 只读取 snapshot，完全不读取 messages/info/events；缺失/None snapshot
  返回 None（legacy/pre-run），present malformed state 确定性 ValidationError。
  旧 2A/2B/2C state payload 的 default/validation 不变。
- restore 是关键状态恢复，不是模型/environment/完整执行自动 resume。state/provenance/step_id 与
  identity round-trip 保持，event 独立可序列化；不建设 replay engine。
- trajectory dump 通过原 Pydantic serializer 重新校验完整 AgentState，非法嵌套 mutation 即使 extra 试图
  覆盖 snapshot 也失败（PydanticSerializationError）；trace JSON/events 也验证后输出隔离快照。

## 7. Stage 2 Acceptance / 后续边界

tests/agents/test_state_integration.py 使用 fake environment、实际 ToolRegistry dispatch 与 deterministic
model，覆盖独立保存/恢复 Working、Task、Evidence Memory、producing step/provenance linkage、模型推测不
晋升、dedup、snapshot isolation、mutation failure 和 legacy trajectory/state compatibility。

不进入 Stage 3。后续 Context Builder 的选择/压缩、Planner schema、Diagnose/Stop Policy、完整 Stage 9 trace
与 execution resume 不在本 Slice；本 Slice 不推断 confirmed TaskMemory 内容。

## 8. Completion Checklist

- [x] deterministic step/query lifecycle 与同 step / 不同 step linkage；
- [x] 实际 Tool output → Observation → Evidence 映射与 provenance 保留；
- [x] failure / skip / interrupted 规则与模型文本不晋升；
- [x] EvidenceMemory dedup 与 AgentState snapshot 更新；
- [x] 最小 Memory Update Event validation / serialization；
- [x] trajectory snapshot / 独立恢复 / legacy compatibility；
- [x] 2A–2C mutation / isolation / serialization tests 保持通过；
- [x] Slice、相关回归、全量 pytest、Ruff、diff check 通过；
- [x] PLAN Stage 2 checklist / Acceptance 按真实结果收口，未进入 Stage 3。
