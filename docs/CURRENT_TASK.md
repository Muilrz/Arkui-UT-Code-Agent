# 当前任务（CURRENT_TASK）

## 1. 本文件用途

这是**唯一的实时任务交接文档**。

Codex 每次开始开发时应优先读取本文件，但长期边界仍以 `SPEC.md` / `DECISIONS.md` 为准。

保持简短：

- 只保存当前目标、范围、Blocker、真实验证结果；
- 旧状态直接替换，不追加成流水账；
- 完成一个 Slice 后立即更新下一个 Slice。

## 2. Current Objective

**Stage 1E：ArkUI `kb_search` Tool 已完成。**

已使用与 Repository/Editing/Execution Tool 相同的 `repository_root`，调用其中真实 `docs/kb_search.py` CLI，
完成全部官方参数的薄适配、统一 Tool contract 与 provenance。Stage 1 的 Slice 已全部完成，等待 Stage 1
整体验收；Stage 2 未开始。

## 3. 当前 In Scope

1. 默认非空 query 与 list mode 的 `kb_search`。
2. 统一 `repository_root`，固定查找 `repository_root/docs/kb_search.py`。
3. `ArkuiKbBackend` / `LocalArkuiKbBackend` boundary。
4. query、detail、all、field、category、list-categories、list-all 的显式 argv mapping。
5. 真实 compact/detail/list 输出的最小 normalization 与 KB provenance。
6. unavailable、root/script、执行、格式与参数失败的结构化表达。
7. deterministic unit tests 与可选 real-checkout smoke。

## 4. 当前 Out of Scope

本 Slice 不实现：

- 自行实现 KB 搜索、排序或 registry 读取算法；
- ArkUI KB 内容复制、同步或缓存；
- Repository/KB index、Vector DB、embedding 或 RAG；
- Memory / Context Builder；
- Planner / Replanner / Diagnose / Stop Policy；
- RetrievalRouter；
- SemanticProvider / clangd MCP 集成；
- Task Relation Graph；
- ArkUI UT Workflow；
- 新的 Repository Index、Persistent Graph 或跨任务知识系统。

## 5. Expected Deliverables

- Tool call 不允许覆盖 repository root；
- CLI 使用 `sys.executable`、argv、固定 repository cwd 且不使用 shell；
- 默认 query 不隐式添加 flag，其他官方选项仅在调用方显式请求时映射；
- compact/detail/list 模式提供的 identity、KB path、source/API/test/Spec/category 等真实字段进入结果；
- repository 绝对路径不进入正常 Observation；
- dispatch 后统一归一化为 `Observation`。

## 6. Verification

2026-09-14 Windows Stage 1E 验证结果：

```text
py -m pytest -q
→ 534 passed, 4 skipped, 1 warning, 0 failed, 0 errors

py -m ruff check src tests
→ passed

git diff --check
→ passed

ARKUI_REPOSITORY_ROOT=C:\Users\Muil\Desktop\arkui_ace_engine-master
py -m pytest -q tests/tools/test_knowledge.py::test_real_arkui_kb_default_detail_and_field_smoke_when_repository_is_configured
→ 1 passed
```

全量测试中的新增 skip 是未配置外部 checkout 时的 real KB smoke；CI 不依赖外部源码仓。warning 为既有
`last_n_messages_offset` deprecation warning，与本 Slice 无关。

## 7. Known Blockers / Unknowns

无。`repository_root` 缺失时返回 `kb_root_not_found`，其中没有 `docs/kb_search.py` 时返回 `kb_unavailable`。

## 8. Completion Checklist

- [x] 真实 ArkUI `kb_search.py` 接口与输出已核对；
- [x] 统一 repository root 与 local backend adapter 已实现；
- [x] 全部官方 CLI 参数的 argv mapping 已测试；
- [x] compact / detail / list 结果解析已测试；
- [x] success / no-match / failure normalization 已测试；
- [x] KB result provenance 与绝对路径最小暴露已测试；
- [x] Registry 原子注册与 dispatch normalization 已测试；
- [x] real ArkUI checkout default / detail / field smoke passed；
- [x] pytest passed；
- [x] ruff passed；
- [x] git diff --check passed。
