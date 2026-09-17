# 当前任务（CURRENT_TASK）

## 1. Slice

**Stage 4 / Slice 4B — Initial Planning**

所属清单：`PLAN.md` 的 Stage 4。本文件已替换完成验收的 Stage 4 / Slice 4A 交接状态。

## 2. Objective / In Scope

在新任务进入正常 model-driven action execution 前，通过现有 Model abstraction 真实生成并保存一个
通过 Slice 4A contract 验证的 structured `Plan`：

- Initial Planning model call 进入现有 call / cost accounting 和 trajectory；
- 有效 Plan 写入 `AgentState.current_plan`，并选择有效的 plan-local active step；
- 后续正常 model query 通过现有 bounded `ContextBuilder` 看到 structured Plan；
- planning-only response 不执行 Tool action，不分配 runtime producing `step_id`，不产生 Evidence；
- invalid planning output 使用既有 bounded `FormatError` 语义重试或终止；
- 保持 AgentState serialization、Stage 2 provenance、Stage 3 bounded input、4A identity boundary 和
  `InteractiveAgent` 继承路径。

## 3. Out of Scope

本 Slice 没有实现 Replanner、Observation 后 Diagnose、control decision states、repeated-call detection、
Stop Policy、完整 control-loop 重写、Retrieval Intent / Router、SemanticProvider、Task Relation Graph、
UT-specific failure classification 或完整 Execution Trace framework。

## 4. Runtime Flow

```text
run(task)
→ initialize AgentState(current_plan=None, current_step=None)
→ bounded initial-planning model input
→ existing Model.query()
→ parse normalized single action.command as Plan JSON data
→ Plan.model_validate()
→ select first PlanStep when active_step_id is null
→ AgentState.current_plan = validated Plan
→ next loop iteration
→ normal query builds ContextBuilder view containing Plan
→ _begin_step() allocates runtime step-N
→ execute normal action
```

`AgentConfig.initial_planning` 默认为 `True`；显式设为 `False` 只用于 baseline/单一职责 regression。
`InteractiveAgent` 的 model-driven `confirm` / `yolo` 路径继承 Initial Planning；纯 human mode 保持 model-free，
从 human 切换到 model-driven mode 时会先建立 Plan。

## 5. Model Input / Output Contract

- 没有新增 provider-specific API，也没有复制 Model provider stack；
- planning input 使用固定 system / instance prefix，加一条 planning instruction 与
  `ContextBuilder.build(current AgentState)`，不重放增长的 trajectory；
- 现有 Model 已将 text/tool-call response 统一为 `message.extra.actions`；Initial Planning 要求恰好一个 action，
  并仅把其中 `command` 字符串当作 Plan JSON 数据；
- 该 action 永不传入 `execute_actions()` / Environment；payload 必须完整通过 Slice 4A `Plan` validation；
- planning response 以 `phase=initial_planning` 保存在 messages/trajectory，后续正常 model input 只通过
  AgentState + ContextBuilder 看见 Plan，不回放 planning response。

## 6. Accounting / Provenance Boundary

- planning 和 normal query 共用 `_check_query_limits()` 与 `_call_model()`；每次实际 Model call 对
  `n_calls` 和 `cost` 各计一次；Model-level `FormatError` 的已计费 cost 继续由既有异常路径接收；
- successful-response / invalid-Plan 路径在 `_call_model()` 计费，FormatError feedback 不重复计费；
- planning 不调用 `_begin_step()`，不设置 message `step_id`，不产生 `MemoryUpdateEvent`、Tool execution 或 Evidence；
- 第一次正常 action 仍从 runtime `step-1` 开始，Tool execution、Evidence 和 MemoryUpdateEvent 继续共享该 ID；
- planning 只设置 `Plan.active_step_id`，不把 PlanStep identity 映射为 runtime step identity。

## 7. Invalid Output

以下情况均不生成 Plan，也不执行 response 中的 action：

- action transport 缺失或不止一个；
- `action.command` 不是字符串或不是 JSON；
- JSON 不是 object；
- payload 不满足 Plan / PlanStep validation。

错误被转换为带 `phase=initial_planning` 的既有 `FormatError` feedback；重试受
`max_consecutive_format_errors`、call/cost/time limits 约束，不引入 Stop Policy。

## 8. 实际修改文件

```text
src/arkui_ut_agent/agents/planning.py
src/arkui_ut_agent/agents/default.py
src/arkui_ut_agent/agents/interactive.py
tests/agents/test_initial_planning.py
tests/agents/test_planning.py
tests/agents/test_default.py
tests/agents/test_interactive.py
tests/agents/test_state_integration.py
tests/agents/test_context_runtime.py
tests/run/test_cli_integration.py
tests/run/test_local.py
docs/PLAN.md
docs/CURRENT_TASK.md
```

## 9. Tests / Evidence

- `test_initial_planning.py` 验证 planning 先于正常 action、Plan 写入 state 和进入实际 bounded context、
  planning action 不执行、planning 不分配 runtime step/Evidence、call/cost 精确计数、trajectory restore、
  invalid payload 与 Model-level FormatError 的 bounded handling，以及 Interactive model/human 路径；
- `test_planning.py` 验证 normalized action transport parsing、null active step 的 deterministic first-step selection，
  以及 missing / ambiguous / non-JSON / wrong-shape / invalid-Plan output 拒绝；
- `test_cli_integration.py` / `test_local.py` 验证真实 CLI/end-to-end fixture 按
  planning → action 顺序运行，且 Environment 只收到 normal action；
- 既有 DefaultAgent、InteractiveAgent、Stage 2 state/provenance 和 Stage 3 bounded Context regression
  显式关闭 Initial Planning 后保持原测试关注点与行为不变。

## 10. Verification

```text
py -m pytest -q tests/run/test_cli_integration.py::test_output_file_is_created tests/run/test_local.py::test_local_end_to_end tests/agents/test_initial_planning.py tests/agents/test_planning.py
26 passed

py -m pytest -q tests/agents
399 passed

$env:Path = 'D:\Work\Python\Scripts;' + $env:Path
py -m pytest -q
751 passed, 4 skipped, 1 warning

py -m ruff check src tests
All checks passed

git diff --check
passed
```

全量第一次运行在两个旧 end-to-end fixtures 上得到 `749 passed, 2 failed, 4 skipped`；失败原因是 fixtures
仍直接返回 action、缺少新的初始 Plan。fixtures 随后改为真实 planning → action 序列，未通过关闭功能规避。
Warning 为既有 cache-control deprecated 参数提示。

## 11. Status / Scope

Stage 4 / Slice 4B 实现与验证完成，当前无 Blocker。`PLAN.md` 只勾选 `Initial Planning`；decision states、
repeated detection、Stop Policy 和完整主 Agent Loop integration 保持未开始。没有修改 `SPEC.md` /
`DECISIONS.md`，因为本 Slice 没有形成超出既有 4A identity boundary 的新长期架构约束。没有 scope creep，
未开始 Slice 4C。
