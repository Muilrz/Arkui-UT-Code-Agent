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

本 Slice 尚未开始实现，暂无验证结果。

## 7. Blockers

无已知 Blocker。开始实现前需先确认现有 trajectory serialization 的可复用边界。

## 8. Completion Checklist

- [ ] 已核对现有 Agent Loop 与 serialization 边界；
- [ ] `AgentState` 最小字段和约束已定义；
- [ ] 默认值与更新行为已测试；
- [ ] serialization round-trip 已测试；
- [ ] invalid/error path 已测试；
- [ ] 最小相关测试通过；
- [ ] 全量 pytest 通过；
- [ ] ruff 通过；
- [ ] `git diff --check` 通过；
- [ ] `PLAN.md` 状态按真实 Evidence 更新。
