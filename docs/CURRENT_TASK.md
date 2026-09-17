# 当前任务（CURRENT_TASK）

## 1. Slice

**Stage 4 / Slice 4A — Planning Contracts**

所属清单：`PLAN.md` 的 Stage 4。本文件已替换完成验收的 Stage 3 / Slice 3D 交接状态。

## 2. Objective / In Scope

完成 Stage 4 的 structured planning contract foundation：

- 定义项目自己的 immutable、validated `Plan` / `PlanStep`；
- 将 `AgentState.current_plan` 从 opaque JSON 收窄为 `Plan | None`；
- 将 `AgentState.current_step` 明确收窄为 runtime producing-step label；
- 让 structured plan 支持 validated replacement update、JSON serialization / restore；
- 让现有 `ContextBuilder` 稳定渲染 structured plan 并使用其内容做 relevance selection；
- 保持 Stage 2 Evidence identity / dedup / provenance 与 Stage 3 bounded Context / model-input contract 不变。

## 3. Out of Scope

本 Slice 没有实现 Initial Planner、Replanner、Diagnose、control decision、repeated-call detection、Stop Policy、
主 Agent Loop control-flow 改造、RetrievalRouter、Retrieval Intent、SemanticProvider、Task Relation Graph、
UT failure classification、完整 Execution Trace 或 Stage 5+ 能力。

## 4. 实际 Planning Contract

```text
Plan
├── revision: positive integer, caller-managed
├── steps: non-empty ordered tuple[PlanStep, ...]
└── active_step_id: optional plan-local reference

PlanStep
├── id: non-empty, unique within Plan
└── description: non-empty
```

- `Plan` / `PlanStep` 均禁止未知字段、字段赋值和无验证更新；`updated()` 返回完整重验证的新 snapshot；
- `Plan.steps` 使用 tuple，避免 planning collection 被原地修改；
- `active_step_id` 如存在，必须引用同一 Plan 中的唯一 `PlanStep.id`；
- `revision` 只提供 contract version 信息，不自动递增，也不实现 Replan policy；
- contract 不包含未来 Planner prompt/model-output schema、action、decision 或 workflow status。

## 5. PlanStep 与 Runtime Step 边界

```text
Plan.active_step_id
→ 只标识当前 Plan 中的 PlanStep

AgentState.current_step（例如 step-1）
→ Tool execution.step_id
→ Evidence.step_id
→ MemoryUpdateEvent.step_id
→ ContextBuilder current-step Evidence priority
```

两套标识不做隐式映射。`DefaultAgent._begin_step()` 仍只分配 runtime `step-N`，不会覆盖
`Plan.active_step_id`；Evidence identity 仍排除 `step_id`，dedup / first-record-wins 行为未改变。

## 6. 实际修改文件

```text
src/arkui_ut_agent/agents/planning.py
src/arkui_ut_agent/agents/state.py
src/arkui_ut_agent/agents/context.py
src/arkui_ut_agent/agents/default.py
src/arkui_ut_agent/agents/__init__.py
tests/agents/test_planning.py
tests/agents/test_state.py
tests/agents/test_task_memory.py
tests/agents/test_context.py
tests/agents/test_state_integration.py
docs/PLAN.md
docs/CURRENT_TASK.md
docs/SPEC.md
docs/DECISIONS.md
```

## 7. Tests / Evidence

- `test_planning.py` 验证 Plan / PlanStep shape、非空字段、strict revision、唯一 step id、有效
  `active_step_id`、未知字段拒绝、immutable / validated replacement update、unsafe copy 序列化拒绝，
  以及 AgentState JSON round-trip；
- `test_state.py` / `test_task_memory.py` 将既有 state / memory serialization regression 切换到正式 Plan；
- `test_context.py` 验证 structured plan canonical rendering、round-trip deterministic output、plan 内容参与
  relevance selection，以及 plan-local id 不会被解释为 runtime Evidence step identity；
- `test_state_integration.py` 验证真实 execution 后 Plan active step 不变，而 current step、Tool execution、
  Evidence 与 MemoryUpdateEvent 继续共享 runtime `step-N`；
- 既有 Stage 2 / Stage 3 tests 全部继续通过。

## 8. Verification

```text
py -m pytest -q tests/agents/test_planning.py tests/agents/test_state.py tests/agents/test_task_memory.py tests/agents/test_context.py tests/agents/test_context_runtime.py tests/agents/test_state_integration.py
141 passed

py -m pytest -q tests/agents
388 passed

$env:Path = 'D:\Work\Python\Scripts;' + $env:Path
py -m pytest -q
740 passed, 4 skipped, 1 warning

py -m ruff check src tests
All checks passed

git diff --check
passed
```

全量测试首次未补 console-script PATH 时为 `739 passed, 4 skipped, 1 failed`，唯一失败是
`arkui-ut-agent --help` 子进程找不到已安装 executable。按既有 Windows 验证前置条件加入
`D:\Work\Python\Scripts` 后全量通过；未修改测试规避环境问题。Warning 为既有 cache-control
deprecated 参数提示。

## 9. Status / Scope

Stage 4 / Slice 4A 实现与验证完成，当前无 Blocker。`PLAN.md` 只将已有代码和测试证明的
“定义 `Plan` / `PlanStep`”标记为完成；Stage 4 其余 checklist 保持未开始。没有 scope creep，未开始 Slice 4B。
`SPEC.md` 与 `DECISIONS.md` 只同步本 Slice 已实现的长期 identity boundary。

## 10. Completion Checklist

- [x] Plan / PlanStep validation；
- [x] immutable / validated replacement update；
- [x] AgentState serialization / restore；
- [x] ContextBuilder structured plan consumption；
- [x] runtime `step_id` / Evidence provenance boundary compatibility；
- [x] Stage 2 / Stage 3 regression；
- [x] 全量 pytest、Ruff、diff check。
