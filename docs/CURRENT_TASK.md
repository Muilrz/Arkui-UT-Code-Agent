# 当前任务（CURRENT_TASK）

## 1. Slice

**Stage 2 / Slice 2B — Working Memory & Task Memory**

所属实施清单：`PLAN.md` 中的 **Stage 2 — AgentState 与 Memory**。

本文件只保留当前或刚完成的一个 Slice；长期边界以 `SPEC.md` / `DECISIONS.md` 为准。
本次按用户明确启动 2B 的要求，替换已验收的 2A 交接状态。

## 2. Objective

把现有 `AgentState` 执行字段明确为 Working Memory 唯一 contract，并加入最小、task-scoped 的
`TaskMemory`，支持确定性更新、snapshot isolation 和 serialization。为后续 Evidence Memory 保留宿主，
但不提前实现 Evidence Memory 或事实确认链路。

## 3. In Scope

1. 检查现有 `AgentState`、2A mutation-boundary tests 和 Stage 1 `Observation` / `Provenance`。
2. 保留 Working Memory 在 `AgentState` 中的单一字段来源，不创建重复 `WorkingMemory` 数据模型。
3. 定义 SPEC 已列出的最小 Task Memory 字段并作为 `AgentState.task_memory` 组成部分。
4. 保持 `AgentState(goal=...)` 默认行为和 `updated()` 全量校验、隔离快照更新入口。
5. 测试默认/完整状态、更新、无效状态、unknown fields、nested mutation、serialization 和 round-trip。
6. 明确 caller-confirmed task information 与 model speculation 的边界；不建立自动事实晋升入口。

## 4. Out of Scope

- Evidence Memory、Evidence identity / dedup；
- `step_id` lifecycle、Tool Call provenance chain、memory update event；
- Context Builder、Planner / Replanner、Diagnose / Stop Policy；
- RetrievalRouter、SemanticProvider、Task Relation Graph、ArkUI UT Workflow；
- Persistent repository memory / index / graph；
- MemoryManager / Repository / Store / Service / EventBus 等无真实调用方抽象；
- 主 Agent Loop 接入或 Stage 1 Tool 业务重构；
- Slice 2C。

## 5. Expected Deliverables

- Working Memory 由 `AgentState` 现有字段单一承载；
- 最小 `TaskMemory` 数据模型，字段名与 SPEC 一致；
- AgentState + Task Memory 的全量校验更新、snapshot isolation 与 Pydantic serialization；
- 不依赖 LLM、网络、MCP、ArkUI repo 或 conversation history 的 deterministic tests；
- 无模型推测自动升级为 confirmed fact 的入口；Evidence linkage 留给后续 Slice；
- 仅据实更新 `CURRENT_TASK.md` / `PLAN.md`，无真实长期冲突时不修改长期架构文档。

## 6. Verification

规定执行：

```text
py -m pytest -q tests/agents/test_state.py tests/agents/test_task_memory.py
py -m pytest -q
py -m ruff check src tests
git diff --check
```

实际验证结果：

```text
py -m pytest -q tests/agents/test_state.py tests/agents/test_task_memory.py
61 passed in 0.24s（含全部 22 项 Slice 2A 回归）

py -m pytest -q tests/agents/test_state.py tests/agents/test_task_memory.py tests/agents/test_init.py tests/run/test_save.py tests/utils/test_serialize.py tests/tools/test_contracts.py
94 passed in 0.38s

py -m pytest -q
595 passed, 4 skipped, 1 warning in 117.62s

py -m ruff check src tests
All checks passed!

git diff --check
passed
```

全量测试运行前把已安装项目的 `D:\Work\Python\Scripts` 加入本次验证进程 PATH，确保 CLI test
找到现有 console script；未修改代码或测试规避环境问题。Warning 来自既有 cache-control deprecated 参数。

## 7. Blockers

无 Blocker。本 Slice 已完成，未接入主 Agent Loop，未进入 Slice 2C。

## 8. Completion Checklist

- [x] Working Memory 单一来源已明确并测试；
- [x] Task Memory 最小字段与默认值已实现；
- [x] confirmed fact / speculation 边界已明确；
- [x] supported update 与 snapshot isolation 已测试；
- [x] invalid / unknown / nested mutation paths 已测试；
- [x] AgentState + Task Memory serialization round-trip 已测试；
- [x] 现有 2A mutation-boundary tests 通过；
- [x] Slice tests / 全量 pytest / Ruff / diff check 通过；
- [x] PLAN 按真实 Evidence 更新。

## 9. 后续接口约束

- Working Memory 继续直接使用 `AgentState` 的现有执行字段，不创建两个事实源。
- `TaskMemory` 与 `AgentState` 位于 project-owned `agents/state.py`；`AgentState.task_memory` 使用
  `default_factory=TaskMemory`，旧的最小 dict/JSON state payload 恢复后得到空 Task Memory。
- `TaskMemory` 保存调用方已确认的当前任务信息；结构校验不能证明事实真实性。
- 不从 messages、hypotheses、模型输出或裸 Observation 自动提取/晋升 confirmed task facts。
- 两个模型都使用 `updated(**changes)` 产生全量重新校验、容器隔离的新 snapshot，更新是整字段替换，
  不隐式 merge、append 或 dedup。父状态的入口仍是 `AgentState.updated()`，无需修改 2A 实现：

  ```python
  next_state = state.updated(
      task_memory=state.task_memory.updated(target_files=["src/text.cc"])
  )
  ```

- 传入 `task_memory={...}` 是整个 TaskMemory 替换，未提供的字段回到默认值，不是 nested patch。
- 顶层赋值禁止；原地 list/dict mutation、`model_copy(update=...)` 和 `model_construct()` 不是受支持更新。
  全量 instance revalidation 与所有 Pydantic dump 均拒绝非法 nested memory，即使只更新 Working Memory
  或 exclude Task Memory。更新/恢复失败为 `ValidationError`，dump 失败为 `PydanticSerializationError`。
- 序列化继续使用 `model_dump(mode="json")` / `model_dump_json()`；恢复使用 `model_validate()` /
  `model_validate_json()`，不需要 conversation history。原有执行字段的形式保持不变，只新增 task_memory。
- Evidence identity、dedup、provenance linkage、step lifecycle 和 memory events 留给后续 Slice。
- Slice 2C 应定义真正的 Evidence contract 与 linkage，而不是把结构校验成功等同于工程事实已被证明；
  本 Slice 没有新增 Evidence placeholder、复制 Provenance 或建立另一套来源系统。
- 不接入主循环，也不进入 Slice 2C。

### Task Memory 字段语义

可选标量默认为 `None`；列表默认为独立空列表，保留顺序与重复值。所有字符串去除首尾空白后必须非空。
路径/符号/测试只存调用方确认的任务标签，不扫描仓库，不检查文件系统，不建立持久索引。

| 字段 | 类型 | 当前任务含义 |
| --- | --- | --- |
| target_component | str / None | 已确认的目标组件 |
| target_files | list[str] | 已确认相关的目标源码/文件路径 |
| target_classes | list[str] | 已确认的目标类标签 |
| target_functions | list[str] | 已确认的目标函数标签 |
| changed_files | list[str] | 已确认发生修改的文件路径，不要求属于 target_files |
| relevant_tests | list[str] | 已确认相关的测试名称或路径 |
| fixtures | list[str] | 已确认相关的 Fixture 标签 |
| mocks | list[str] | 已确认相关的 Mock 标签 |
| build_target | str / None | 已确认的最小构建/测试 Target 标签 |
| completed_steps | list[str] | 已完成步骤的摘要，不是 PlanStep ID 或 step_id lifecycle |
| failed_attempts | list[str] | 已发生失败尝试的摘要，不是完整失败 workflow schema |
| important_decisions | list[str] | 已明确作出的任务选择/决策摘要，不是未经确认的推断 |
