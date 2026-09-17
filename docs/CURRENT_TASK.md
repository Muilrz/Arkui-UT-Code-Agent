# 当前任务（CURRENT_TASK）

## 1. Slice

**Stage 4 / Slice 4E — Stop Policy & Control Loop Integration**

所属清单：`PLAN.md` 的 Stage 4。本文件已替换完成验收的 Stage 4 / Slice 4D 交接状态。

## 2. Objective / In Scope

完成 Stage 4 最后一个 Slice：建立正式 Stop Policy，并把 Initial Planning、Decision/Diagnose、Replan、
Repeat Detection 与 Stop Policy 收口到默认主 Agent Loop。

支持现有 `StopReason`：

```text
success / step_limit / cost_limit / repeated_failure / no_new_evidence /
tool_unavailable / irrecoverable_failure
```

所有正式 terminal outcome 必须写入 `AgentState.stop_reason`。`finish` 必须形成 `success`；step/cost limit
必须区分；首次 exact repeat 先 Replan，Replan 后同一 repeat loop 在没有新 Evidence 时停止。

## 3. Implemented Control Semantics

- `retrieve / act / repair / verify`：保留为当前 step 的显式 decision，进入既有 action boundary；
- `diagnose`：再次消费同一批 bounded actual Observation records 进行 control-only Diagnose；
- `replan`：进入 4D Replanner；
- `finish`：不再调用 Model/Environment，终止为 `success`；
- 没有 actionable decision 时，默认 control loop 不允许静默执行普通 action；
- legacy/baseline 单一职责测试可显式关闭 integrated control loop，但每个实际 step 仍会获得可追踪 decision。

## 4. Stop Signals

- step/cost：现有 counters/limits；
- first exact repeat：Replan，不终止；
- same repeat loop after Replan：Evidence identity set 未变化则 `no_new_evidence`，有新 Evidence 但仍回到同一
  exact loop 则 `repeated_failure`；
- `tool_unavailable` / `irrecoverable_failure`：只识别真实 failed Observation 中同名 diagnostic code；
- legacy Submitted：`success`；不可恢复的 format/uncaught terminal path：`irrecoverable_failure`。

## 5. Minimal Step Trace

为 Stage 4 Acceptance 添加 task-local、deterministic、可序列化的最小 runtime step trace：

```text
step_id / current_goal / decision / actions / observations / state_updates
```

不记录 Stage 9 才需要的完整 timing、token、edit/build/test aggregation 或 evaluation metadata。

## 6. Out of Scope

不实现 RetrievalRouter、SemanticProvider、Task Relation Graph、Stage 8 UT workflow/failure classification 或
Stage 9 完整 Execution Trace framework。不开始 Stage 5，不检查 CI，不创建 commit。

## 7. Implementation / Tests

- `agents/stop.py`：把已有、可验证的 runtime signal 映射为正式 `StopReason`；
- `agents/trace.py`：定义 immutable/validated 的 Stage 4 最小 `ExecutionStepTrace`；
- `agents/default.py`：主循环接入 planning、decision/diagnose、replan、repeat guard 与 stop；
- `agents/interactive.py`：保持 human/confirm/yolo 继承路径和已有人工提高 limit 后续跑行为；
- `tests/agents/test_stop_policy.py`：覆盖 finish、step/cost limit、四种 actionable decision、diagnose、
  repeat → Replan → stop、真实 terminal diagnostics、decision gate 和 trace serialization；
- 既有 4A–4D / Stage 2–3 regression 对新显式 action decision 做了最小断言/配置适配。

## 8. Verification / Status

实际验证：

```text
py -m pytest -q tests/agents
434 passed

$agentScripts = py -c "import sysconfig; print(sysconfig.get_path('scripts'))"
$env:PATH = "$agentScripts;$env:PATH"
py -m pytest -q
786 passed, 4 skipped, 1 existing deprecation warning

py -m ruff check src tests
All checks passed!

git diff --check
passed（仅 Git 的 LF→CRLF working-copy notices）
```

第一次直接执行全量命令时，环境 PATH 缺少已安装 console script 所在的 `D:\Work\Python\Scripts`，因此
`test_arkui_ut_agent_help` 报 `FileNotFoundError`；把当前解释器的 Scripts 目录仅加入验证进程 PATH 后，目标测试
和全量测试均通过，未修改测试来规避该环境问题。

Stage 4 的全部 checklist、Acceptance 与 Exit Criteria 已由实现和测试满足，现已正式收口。没有开始 Stage 5，
没有检查 CI，也没有创建 commit。当前无 Blocker。
