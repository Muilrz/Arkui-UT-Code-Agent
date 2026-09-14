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

已通过配置的 ArkUI Ace Engine checkout 调用其真实 `docs/kb_search.py --detail` CLI，完成薄 backend adapter、
统一 Tool contract 与 provenance。Stage 1 的 Slice 已全部完成，等待 Stage 1 整体验收；Stage 2 未开始。

## 3. 当前 In Scope

1. 必填非空 query 的 `kb_search`。
2. `tools.arkui_ace_engine_root` runtime 配置。
3. `ArkuiKbBackend` / `LocalArkuiKbBackend` boundary。
4. 真实 `kb_search.py --detail` 输出的最小 normalization 与 KB provenance。
5. unavailable、root/script、执行、格式与参数失败的结构化表达。
6. deterministic unit tests 与可选 real-checkout smoke。

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

- Tool call 不允许覆盖 ArkUI root；
- CLI 使用 argv、固定 cwd 且不使用 shell；
- name、KB path、source/API/test/Spec 等真实字段进入结果；
- checkout 绝对路径不进入正常 Observation；
- dispatch 后统一归一化为 `Observation`。

## 6. Verification

2026-09-14 Windows Stage 1E 验证结果：

```text
py -m pytest -q
→ 518 passed, 4 skipped, 1 warning, 0 failed, 0 errors

py -m ruff check src tests
→ passed

git diff --check
→ passed

ARKUI_ACE_ENGINE_ROOT=C:\Users\Muil\Desktop\arkui_ace_engine-master
py -m pytest -q tests/tools/test_knowledge.py::test_real_arkui_kb_smoke_when_checkout_is_configured
→ 1 passed
```

全量测试中的新增 skip 是未配置外部 checkout 时的 real KB smoke；CI 不依赖外部源码仓。warning 为既有
`last_n_messages_offset` deprecation warning，与本 Slice 无关。

## 7. Known Blockers / Unknowns

无。真实 ArkUI checkout 是可选 runtime dependency；未配置时 `kb_search` 返回结构化 `kb_unavailable`。

## 8. Completion Checklist

- [x] 真实 ArkUI `kb_search.py` 接口与输出已核对；
- [x] path config 与 local backend adapter 已实现；
- [x] success / no-match / failure normalization 已测试；
- [x] KB result provenance 与绝对路径最小暴露已测试；
- [x] Registry 原子注册与 dispatch normalization 已测试；
- [x] real ArkUI checkout smoke passed；
- [x] pytest passed；
- [x] ruff passed；
- [x] git diff --check passed。
