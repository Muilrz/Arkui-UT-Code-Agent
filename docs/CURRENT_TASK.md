# 当前任务（CURRENT_TASK）

## 1. Slice

**Stage 2 / Slice 2A — AgentState Contract Foundation**

所属实施清单：`PLAN.md` 中的 **Stage 2 — AgentState 与 Memory**。

本文件是唯一的实时任务交接文档，只保留当前 Slice；长期边界以 `SPEC.md` / `DECISIONS.md` 为准，
长期 Stage 定义以 `TECHNICAL_ROADMAP.md` 为准，其 checklist 和 Acceptance 状态以 `PLAN.md` 为准。

## 2. Objective

定义可序列化的 `AgentState` 基础契约，显式承载当前任务的最小执行状态，为后续 Working / Task /
Evidence Memory 切片提供稳定宿主，但本 Slice 不提前实现完整 Memory 系统。

## 3. In Scope

1. 核对现有 Agent Loop、trajectory serialization 与配置入口。
2. 定义 `AgentState` 的最小字段、状态约束和序列化形式。
3. 覆盖 `goal`、`current_plan`、`current_step`、`next_action`、`open_questions`、`hypotheses`、
   `blocking_issue` 与 stop/retry state 的基础表示。
4. 为默认值、更新和序列化/反序列化添加 deterministic unit tests。
5. 记录后续 Memory 切片所依赖的明确接口，不预建无调用方抽象。

## 4. Out of Scope

- Working Memory、Task Memory、Evidence Memory 的完整行为；
- Evidence identity / dedup、Tool Call provenance 与 `step_id` 串联；
- Context Builder；
- Planner / Replanner / Diagnose / Stop Policy；
- RetrievalRouter、SemanticProvider、Task Relation Graph；
- ArkUI UT Workflow；
- Persistent Repository Memory、Repository Index 或跨任务知识系统；
- 对既有 Tool 业务实现的重构。

## 5. Expected Deliverables

- 一个边界清晰、可序列化的 `AgentState` 数据模型；
- 字段默认值与更新规则明确，不依赖完整 conversation history；
- 无模型推测自动升级为 confirmed Evidence 的入口；
- 针对正常路径、无效状态和 serialization round-trip 的单元测试；
- 如长期契约无需变化，不修改 `SPEC.md` / `DECISIONS.md` / `TECHNICAL_ROADMAP.md`。

## 6. Verification

计划执行：

```text
py -m pytest -q <Slice 2A 相关测试>
py -m pytest -q
py -m ruff check src tests
git diff --check
```

实际执行结果：

```text
py -m pytest -q tests/agents/test_state.py
15 passed in 0.12s

py -m pytest -q tests/agents/test_state.py tests/agents/test_init.py tests/run/test_save.py tests/utils/test_serialize.py tests/tools/test_contracts.py
48 passed in 0.34s

py -m pytest -q
549 passed, 4 skipped, 1 warning in 113.75s

py -m ruff check src tests
All checks passed!

git diff --check
passed
```

全量 pytest 首次运行时，当前 Python 环境未把已 editable 安装项目的 `D:\Work\Python\Scripts` 放入
`PATH`，导致 `test_arkui_ut_agent_help` 无法找到 `arkui-ut-agent`。补齐该现有 console script 目录后，
单项重跑为 `1 passed`，随后按上方命令全量重跑通过；未修改代码或测试来规避该环境问题。

## 7. Blockers

无 Blocker。现有 trajectory serialization 继续负责包含 `messages` 的完整运行记录；`AgentState` 使用
Pydantic 的 JSON serialization 独立保存任务执行状态，本 Slice 未修改 trajectory format 或主 Agent Loop。

## 8. Completion Checklist

- [x] 已核对现有 Agent Loop 与 serialization 边界；
- [x] `AgentState` 最小字段和约束已定义；
- [x] 默认值与更新行为已测试；
- [x] serialization round-trip 已测试；
- [x] invalid/error path 已测试；
- [x] 最小相关测试通过；
- [x] 全量 pytest 通过；
- [x] ruff 通过；
- [x] `git diff --check` 通过；
- [x] `PLAN.md` 状态按真实 Evidence 更新。

## 9. 后续接口约束

- `AgentState` 是 task-scoped execution state，不包含或恢复 conversation `messages`；trajectory 仍是独立运行记录。
- `current_plan` / `current_step` 在正式 `Plan` / `PlanStep` contract 出现前只承诺 JSON-compatible，后续
  Planner Slice 应替换或收窄表示，而不是依赖本 Slice 假设出的结构。
- 后续 Working / Task / Evidence Memory 可把 `AgentState` 作为稳定宿主，但不得把模型推测直接写成
  confirmed Evidence；Evidence identity、dedup、provenance 与 `step_id` 仍留给后续 Slice。
- 状态持久化使用 `model_dump(mode="json")` / `model_dump_json()`，恢复使用 `model_validate()` /
  `model_validate_json()`；unknown field、非法枚举、负数/非整数 retry count 与非 JSON plan/step 均确定性失败。
