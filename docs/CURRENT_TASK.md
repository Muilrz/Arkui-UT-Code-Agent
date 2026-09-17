# 当前任务（CURRENT_TASK）

## 1. Slice

**Stage 3 / Slice 3D — Runtime Context Integration**

所属清单：`PLAN.md` 的 Stage 3。本文件已替换完成验收的 Stage 3 / Slice 3C 交接状态。

## 2. Objective / In Scope

完成现有 `ContextBuilder` 到实际 model query 边界的最小接线：

- `DefaultAgent.query()` 每轮基于当前 `AgentState` 构造 bounded Context；
- 实际模型输入不再重放持续增长的完整 `self.messages`；
- 固定保留当前 run 的 system / instance agent instructions；
- 完整 messages / trajectory 继续追加、保存和序列化；
- 保持 step、cost、time limit、format error、tool execution 与 `InteractiveAgent` 继承路径；
- 使用 deterministic integration tests 验证实际调用输入的界限和关键 Evidence 可见性。

## 3. Out of Scope

本 Slice 不定义 `Plan` / `PlanStep`，不实现 Planner / Replanner、Diagnose / Stop Policy、
repeated-call detection、RetrievalRouter、SemanticProvider、Task Relation Graph，也不增加新的
history / memory contract。

## 4. 实际 Model-input Contract

每次 `model.query()` 接收以下受控视图：

```text
固定 system instruction
+ 固定 instance/agent instruction
+ ContextBuilder.build(current AgentState) 的单条 user context
+ 至多一条最新 runtime feedback（可选）
```

- Context 消息继续遵守 Stage 3A–3C 的固定 section 顺序和 16,000 Unicode character hard budget；
- `self.messages[2:]` 的 assistant / observation / tool 历史不直接进入下一轮 model input；事实只通过既有
  AgentState / TaskMemory / EvidenceMemory 与 ContextBuilder 选择进入；
- system / instance 前缀在一个 run 内固定，因此不会随 trajectory 增长；
- 为保持既有 format error 与 InteractiveAgent 人工中断/追加任务行为，仅选择最新一条带现有
  `interrupt_type` 的反馈，最多 2,000 characters；不建立新持久化 history schema；
- model output、observation、interrupt 与 exit message 仍完整追加到 `self.messages`，trajectory serialization
  不删除任何既有 trace；
- Context 在 `_begin_step()` 后构造，`current_step` 继续使用 Stage 2 opaque label，不引入 Stage 4 类型。

## 5. Verification

```text
py -m pytest -q tests/agents/test_context_runtime.py tests/agents/test_context.py
py -m pytest -q tests/agents/test_state_integration.py tests/agents/test_default.py tests/agents/test_interactive.py
py -m pytest -q tests/agents
py -m pytest -q
py -m ruff check src tests
git diff --check
```

## 6. Status / Results / Blockers

Stage 3 / Slice 3D 实现完成，当前无 Blocker。已完成验证：

```text
Runtime Context + Context Builder deterministic tests: 21 passed
Default / Interactive / State integration regression: 204 passed
Agent regression: 377 passed
Full pytest: 729 passed, 4 skipped, 1 warning
Ruff: All checks passed
git diff --check: passed
```

全量测试进程按既有 Windows 验证前置条件加入已安装 console script 目录
`D:\Work\Python\Scripts`；未修改测试规避环境问题。Warning 为既有 cache-control deprecated 参数提示。

## 7. Slice / Stage Boundary

- 没有修改 Stage 2 AgentState、Memory、Evidence identity/dedup contract；
- 没有删除或压缩持久化 trajectory，只控制每轮实际 model input；
- 没有新增 planning、diagnosis、stop、routing、semantic 或 graph 抽象；
- 没有把 Context 接线包装成新的 control loop；
- Stage 3 的 Runtime Exit Criteria 现在由 actual model-call integration test 证明，不再把接线错误归到 Stage 4。

## 8. Completion Checklist

- [x] ContextBuilder 接入实际 `model.query()` 边界；
- [x] system / instance instructions 保留；
- [x] trajectory 增长不再导致完整历史重放；
- [x] current-step Evidence 与 Build/Test diagnostics 可进入实际调用；
- [x] format error runtime feedback 有界保留；
- [x] 完整 messages / trajectory 继续记录和序列化；
- [x] DefaultAgent / InteractiveAgent 既有行为定向回归通过；
- [x] 全量 pytest、Ruff、diff check 最终验证。
