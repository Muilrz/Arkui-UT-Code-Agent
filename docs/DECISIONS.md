# 架构决策记录（DECISIONS）

## 使用原则

这里只记录**会长期约束未来实现**的架构决策。

这里不是会议纪要，也不是临时 TODO。

如果某个决策未来发生变化：

- 新增一个决策条目说明 supersede；
- 不要静默删除或改写旧决策，让 Codex 失去历史上下文。

---

## D-001 — 以 `mini-swe-agent` 作为主要运行底座

**Status：accepted**

**Decision：** 复用 `mini-swe-agent` 的 Model、Environment、CLI/config、基础 trajectory、step/cost/time-limit 等通用能力；默认线性 Agent Control Flow 由本项目扩展/替换为 task-aware control plane。

**Reason：** 项目复杂度应投入 ArkUI 专项 Agent 能力，而不是重新实现通用 Code Agent runtime。

---

## D-002 — 不建设 Persistent Repository Knowledge Platform

**Status：accepted**

**Decision：** 不创建持久化 Component/File/Class/Function/Test Index、Symbol DB、Vector DB、Repository Snapshot、Incremental Refresh Pipeline、Full-repository Graph。

**Reason：**

```text
稳定领域知识 → ArkUI KB
当前源码事实 → rg/source
精确 C++ 语义 → clangd MCP
当前任务状态 → Agent Memory
```

重复建设会增加 freshness 与维护成本，并偏离轻量 Agent 架构。

---

## D-003 — Retrieval 使用 Layered、Evidence-driven 策略

**Status：accepted**

**Decision：**

```text
ArkUI KB → where to look
rg/source → what current revision says
SemanticProvider → exact C++ symbol relations
```

Semantic 是 escalation path，不是每次 Retrieval 的必经步骤。

---

## D-004 — Runtime Memory 只做 Task-scoped Memory

**Status：accepted**

**Decision：** 当前任务维护 Working / Task / Evidence Memory；不在本项目建设长期跨任务 Repository Memory。

**Reason：** 任务执行需要可靠状态，但长期仓库知识会重复外部权威信息源并引入 stale data。

---

## D-005 — Context Builder 与 Memory 分离

**Status：accepted**

**Decision：** Memory 负责保留状态/事实，Context Builder 负责每轮模型调用的选择、压缩和排序。

**Reason：** “保存什么”和“当前给模型看什么”是不同职责，不能混成一个组件。

---

## D-006 — Task Relation Graph 为临时、Task-local、带 Provenance

**Status：accepted**

**Decision：** 仅根据当前任务已经获取的 Evidence 形成 Relation Graph；不主动扫全仓、不使用 Graph Database、不做 snapshot/refresh。

**Reason：** Graph 主要服务当前 Planning、Context 和 Explainability，不承担 Repository Knowledge Platform 职责。

---

## D-007 — Tool Wrapper 保持薄

**Status：accepted**

**Decision：** Tool 只执行并返回标准化的 result/diagnostic/provenance；Planner reasoning 不进入 Tool；Retrieval mapping 放在 `RetrievalRouter`。

---

## D-008 — UT Repair 采用 Diagnosis-first

**Status：accepted**

**Decision：** Build/Test 失败后，优先分类失败并判断缺失 Evidence，再进行下一次编辑。

**Reason：** Blind edit/retry 难以调试、成本高，也不符合 Evidence-driven Agent 的设计目标。

---

## D-009 — 核心能力必须有 Executable Evidence

**Status：accepted**

**Decision：** Planning、Retrieval、Memory/Context、Task Relation Graph、UT Repair 等能力必须能通过代码、测试、Execution Trace 或 Evaluation 验证。

**Reason：** 仅有架构名称不能证明 Agent 真正具备该能力。

---

## D-010 — clangd MCP Server 使用现成开源实现，不自研

**Status：accepted**

**Decision：** 项目不开发 clangd MCP Server。首选集成：

```text
felipeerias/clangd-mcp-server
```

备用/参考：

```text
mpsm/mcp-cpp
```

项目内部只维护可替换的：

```text
SemanticProvider
├── McpSemanticProvider
└── UnavailableSemanticProvider
```

**Reason：** clangd lifecycle、LSP/JSON-RPC、definition/reference/call hierarchy 等已有成熟基础设施。本项目技术价值应集中在 Agent Control Plane，而不是重复实现语义 Server。

---

## D-011 — `SemanticProvider` 是外部 MCP 的稳定 Compatibility Boundary

**Status：accepted**

**Decision：** Planner、RetrievalRouter、Memory、Task Relation Graph 不得直接依赖 `felipeerias/clangd-mcp-server` 或其他 MCP Server 的 schema；所有外部差异封装在 `McpSemanticProvider` 中。

**Reason：** 外部项目可能更新 Tool 名、Payload 或实现。项目需要能够更换 Server，而不重写高层 Agent。

---

## D-012 — `compile_commands.json` 是 Semantic Integration Prerequisite，不是新子系统

**Status：accepted**

**Decision：** 精确 clangd 语义依赖有效 compilation database。项目可以检测、配置或调用现有 ArkUI/GN 工具获得 `compile_commands.json`，但不建设自己的编译命令推断、持久索引或 Repository Intelligence Pipeline。

**Reason：** compilation database 是 clangd 的环境输入，不应成为扩大项目边界的理由。
