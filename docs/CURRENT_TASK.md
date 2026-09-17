# 当前任务（CURRENT_TASK）

## 1. Slice

**Stage 4 / Slice 4C — Control Decision & Diagnose**

所属清单：`PLAN.md` 的 Stage 4。本文件已替换完成验收的 Stage 4 / Slice 4B 交接状态。

## 2. Objective / Result

已建立正式、可验证、可序列化的 runtime `ControlDecision`，支持：

```text
retrieve / act / diagnose / replan / repair / verify / finish
```

decision 由 validated `kind` 和非空 `rationale` 组成；rationale 是 diagnosis/control state，不是
confirmed Evidence。它进入 `AgentState.current_decision`、trajectory snapshot 和现有 bounded
`ContextBuilder` Working Memory。

## 3. Diagnose Runtime Flow

```text
normal action query
→ allocate runtime step-N
→ execute Tool
→ normalize Observation
→ record Tool execution / Evidence / provenance
→ enqueue actual Tool record for Diagnose
→ bounded control-only model call
→ parse action.command as ControlDecision JSON data
→ AgentState.current_decision = validated decision
→ next normal action query sees decision through ContextBuilder
```

Diagnose 默认由 `AgentConfig.diagnose_after_observation=True` 启用。旧 Stage 2/3/baseline tests 在只验证
原职责时显式关闭它；4C runtime 和 end-to-end tests 使用默认开启路径。

## 4. Model / Accounting / Error Contract

- Diagnose 复用现有 `Model.query()`、`_check_query_limits()` 与 `_call_model()`，没有 provider-specific API；
- input 只包含固定 system/task prefix、最大 16,000 字符的 AgentState Context、最大 4,000 字符的真实
  Tool execution/Observation records，以及至多一条既有 bounded runtime feedback；不重放完整 history；
- response 复用 normalized single-action transport，但 `action.command` 只作为 JSON 数据解析，永不传入
  Environment；
- successful、invalid-payload 和 Model-level `FormatError` 路径都由既有 call/cost accounting 精确计数；
- invalid/ambiguous/non-JSON/invalid-kind/empty-rationale output 不写入 decision、不执行 action，并使用既有
  consecutive FormatError 上限；pending Observation 保留供有界重试。

## 5. Identity / Evidence Boundary

- Diagnose 不调用 `_begin_step()`，response/feedback 只有 `phase=diagnose` 与 `source_step_ids`，没有新的
  producing `step_id`；
- `Plan.active_step_id` 仍只表示 plan-local identity；Diagnose 不修改 Plan，也不建立到 runtime step 的映射；
- Tool execution、Evidence 与 Memory Update Event 继续共享原 runtime `step-N`；
- model-produced rationale 只保存在 `current_decision`，不会进入 EvidenceMemory；
- Evidence identity、first-wins dedup、原始 provenance 与 producing `step_id` 保持不变。

## 6. 实际修改文件

```text
src/arkui_ut_agent/agents/control.py
src/arkui_ut_agent/agents/state.py
src/arkui_ut_agent/agents/context.py
src/arkui_ut_agent/agents/default.py
src/arkui_ut_agent/agents/interactive.py
src/arkui_ut_agent/agents/__init__.py
tests/agents/test_control.py
tests/agents/test_diagnose.py
tests/agents/test_state.py
tests/agents/test_task_memory.py
tests/agents/test_context_runtime.py
tests/agents/test_state_integration.py
tests/agents/test_default.py
tests/agents/test_interactive.py
tests/conftest.py
tests/run/test_local.py
docs/PLAN.md
docs/CURRENT_TASK.md
```

## 7. Tests / Evidence

- `test_control.py` 覆盖七种 kind 的 validation/JSON serialization、invalid kind/empty rationale/extra field
  拒绝，以及 AgentState restore 与 bounded Context；
- `test_diagnose.py` 覆盖真实 Observation→Evidence→Diagnose→next context、control-only response 不执行、
  call/cost accounting、invalid output、Model-level FormatError、无伪造 runtime step、Evidence identity/dedup/
  provenance 不变、model rationale 不晋升为 Evidence；
- `test_local.py` 的真实 deterministic flow 已更新为 planning→action→diagnose→submit；
- `test_default.py`、`test_interactive.py`、Stage 2/3 regression fixtures 显式隔离其原有测试关注点。

## 8. Verification

```text
py -m pytest -q tests/agents/test_control.py tests/agents/test_diagnose.py tests/agents/test_initial_planning.py
20 passed

py -m pytest -q tests/agents
414 passed

$env:Path = 'D:\Work\Python\Scripts;' + $env:Path
py -m pytest -q
766 passed, 4 skipped, 1 warning

py -m ruff check src tests
All checks passed

git diff --check
passed
```

第一次未补 Scripts PATH 的全量运行得到 `764 passed, 2 failed, 4 skipped`：一项是 Windows 无法定位
`arkui-ut-agent` console script；另一项暴露旧 end-to-end fixture 缺少 Diagnose response。fixture 随后改为
真实 4C 调用序列，且在补入仓库既有 Windows Scripts PATH 后全量通过。warning 为既有 cache-control
deprecated 参数提示。

## 9. Status / Scope

Stage 4 / Slice 4C 实现与验证完成，当前无 Blocker。`PLAN.md` 只勾选 decision-state checklist；没有实现
或勾选 Replanner、repeated detection、Stop Policy 或完整主循环接线。`retrieve` / `replan` / `repair` /
`verify` / `finish` 目前只被持久化为 next-control intention，不触发未来 workflow。未引入 Router、Semantic、
Graph、UT-specific classification 或完整 Trace framework；没有 scope creep。未修改 `SPEC.md` /
`DECISIONS.md`，因为本 Slice 没有改变既有长期 identity、Evidence 或 bounded Context 边界。
