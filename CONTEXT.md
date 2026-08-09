# Living Story World Context

本文定义当前产品使用的核心术语。产品最高层约束以
[Living Story World PRD v1.0](docs/product-plan.md) 为准。

## Product protocol

**World**：持续存在的客观世界，包括时间、地点、规则、环境、隐藏事实、重要
物品和已提交历史。World 不等于 UI 文本、Wiki 或一份任意生成的 JSON。

**Actor**：能够在世界中感知并提交 Intent 的参与者。Player 与重要 NPC 使用
相同的 Actor 规则；二者的 Intent 都必须经过 Resolution。

**Perception**：某个 Actor 此刻依法可感知和知道的派生视图。它不包含其他
Actor 的私密记忆、GM 隐藏事实、未发现证据或模型 reasoning。

**Intent**：Actor 想做什么或尝试做什么。自然语言中的结果性表述仍只是
Attempt，不能直接成为世界事实。

**Resolution**：Concordia Game Master 根据当前 Reality 裁定实际发生什么的
唯一语义边界。

**Memory**：Actor 对过去经历的持久记录。Memory 与 Actor 当前状态分离。

## Authority

**ResolvedEvent**：通过验证后唯一能够进入 committed history 的世界事件。
User Input、NPC Output、Intent、Narrative、Belief、Perception 和 reasoning 都
不能单独修改 World Truth。

**Checkpoint**：包含可恢复世界、Actor、Memory 和运行状态的不可变历史节点。

**Branch**：从某个 Checkpoint 派生的独立 Alternate History。分支不会覆盖原
历史。

**Projection**：由权威历史派生、可以重建的视图，包括 Wiki、Narrative、
UI Scene、Summary 和 Manuscript。

## Product boundary

World Session 是 MVP 核心体验。Writer、Editor、投稿、章节正文、RAG 工作台和
传统多 Agent 创作流程属于遗留或未来能力，不能成为世界回合、恢复、分支或
感知的核心依赖。
