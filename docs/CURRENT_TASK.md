# 当前任务（CURRENT_TASK）

## 1. Slice

**Stage 4 / Slice 4D — Replanning & Repeat Detection**

所属清单：`PLAN.md` 的 Stage 4。本文件已替换完成验收的 Stage 4 / Slice 4C 交接状态。

## 2. Objective / Result

已实现真正的 Replanner、显式 Replan trigger，以及 deterministic：

```text
same Tool + same Input + same Observation
```

检测。显式 `AgentState.current_decision.kind == replan` 或确认重复 triplet 后，Replan 在任何下一次普通
action query 前执行。

## 3. Replan Runtime Flow

```text
current_decision = replan
→ bounded Replan model input
→ existing Model.query()
→ parse normalized action.command as Plan JSON data
→ validate complete Plan contract
→ require new.revision == old.revision + 1
→ atomically replace AgentState.current_plan
→ clear consumed current_decision / next_action
→ next normal action sees replacement Plan
```

Replan 复用现有 call/cost/time/format-error handling 和 Model abstraction。输入只包含固定 system/task
prefix、现有最大 16,000 字符的 AgentState Context，以及至多一条 bounded runtime feedback；不重放完整
trajectory。`active_step_id=null` 时 deterministic 选择 replacement Plan 的第一个 step。

## 4. Revision / Atomicity Contract

- Initial Plan 从自身 validated revision 开始；
- 每次成功 Replan 必须严格推进一个 revision：`expected = current.revision + 1`；
- unchanged revision、跳号、invalid Plan、非 JSON 或 ambiguous action transport 均被拒绝；
- replacement 只有完整 validation 与 revision check 都通过后才原子写入 AgentState；
- invalid Replan 保留旧 Plan、`current_decision=replan` 和原 runtime state，供既有 bounded error retry；
- Replan response 只作为数据解析，永不执行 Environment。

## 5. Repeat Detection Rule

每次真实 Tool Observation 完成 normalization、Evidence mapping 和 execution record 构造后，对以下字段做
canonical JSON exact comparison：

```text
tool_name
+ semantic tool_input
+ structured observation
```

规则：

- `step_id`、Evidence identity、dedup status、`repeat_of_step_id` 不参与比较；
- provider transport correlation `tool_call_id` 不属于 semantic Tool input，比较时剔除；
- Observation 缺失的 interrupted/invalid record 不足以确认完整 triplet，不触发；
- Tool、Input、Observation 任一不同都不算重复；
- Evidence dedup 与 repeat detection 独立：相同 Evidence 但 Input 不同仍不会触发 Replan；
- 确认重复后，execution record 写入 `repeat_of_step_id`，并设置 deterministic `replan` decision；
- pending Diagnose 被清除，Replan 优先于下一普通 action；
- 同一 action batch 中确认第二个重复后，剩余第三个相同 action 不再执行。

Replan 后仍重复且没有新 Evidence 时如何终止属于 Stage 4 / Slice 4E Stop Policy，本 Slice 不处理。

## 6. Identity / Provenance Boundary

- Replan 不调用 `_begin_step()`，response/feedback 只有 `phase=replan` 与 revision metadata，没有 runtime
  producing `step_id`；
- `PlanStep.id` / `Plan.active_step_id` 仍为 plan-local identity，不映射 runtime step；
- 重复的第二次 Tool execution 仍保留自己的 runtime step record；Evidence first-wins dedup 继续保留第一条
  Evidence 的 identity、provenance 与 producing step；
- Replan 不产生 Tool execution、Observation、Evidence 或 Memory Update Event。

## 7. 实际修改文件

```text
src/arkui_ut_agent/agents/planning.py
src/arkui_ut_agent/agents/default.py
src/arkui_ut_agent/agents/interactive.py
tests/agents/test_replanning.py
tests/agents/test_diagnose.py
tests/agents/test_context_runtime.py
tests/agents/test_state_integration.py
tests/agents/test_default.py
tests/agents/test_interactive.py
docs/PLAN.md
docs/CURRENT_TASK.md
```

## 8. Tests / Evidence

- `test_replanning.py` 验证 exact revision increment、null active-step selection、显式 decision 在 next action
  前触发、replacement Plan 进入下一 bounded context、invalid atomicity、control-only response 不执行/不分配
  step、完整 triplet detection、三种 partial-match 非重复边界、transport id 排除、同 batch 第三次调用跳过、
  Evidence dedup/identity/provenance 独立；
- 4C duplicate-Evidence 单一职责测试显式关闭 repeat detection，继续证明 Diagnose/Evidence 原 contract；
- Stage 2/3 和 baseline fixtures 与 4B/4C 相同，显式关闭不属于各自测试目标的 control feature；
- Initial Planning、Control Decision、Diagnose、InteractiveAgent 与全部 Agent regression 保持通过。

## 9. Verification

```text
py -m pytest -q tests/agents/test_replanning.py tests/agents/test_planning.py tests/agents/test_initial_planning.py tests/agents/test_diagnose.py tests/agents/test_control.py
45 passed

py -m pytest -q tests/agents
420 passed

$env:Path = 'D:\Work\Python\Scripts;' + $env:Path
py -m pytest -q
772 passed, 4 skipped, 1 warning

py -m ruff check src tests
All checks passed

git diff --check
passed
```

warning 为既有 cache-control deprecated 参数提示。

## 10. Status / Scope

Stage 4 / Slice 4D 实现与验证完成，当前无 Blocker。`PLAN.md` 只勾选 repeated detection；Stop Policy 与
完整主 Agent Loop 接线保持未开始。未实现 `no_new_evidence` / `repeated_failure` 终止、Router、Semantic、
Graph、UT-specific classification 或完整 Trace framework；没有 scope creep，未开始 Slice 4E。未修改
`SPEC.md` / `DECISIONS.md`，因为实现遵循既有 Replan、repeat rule、identity、Evidence 与 bounded Context
长期边界。
