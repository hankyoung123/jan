# AI Story Evolution Engine Next
## 全新产品开发计划书（AI Coding 指导版）

**版本：** v1.0  
**日期：** 2026-07-31  
**用途：** 作为产品、架构、开发顺序、代码约束与验收标准的统一依据，供 AI Coding 按阶段实施。  
**适用范围：** 全新版本，不兼容旧项目数据库和旧工作流；旧仓库仅作为交互、组件和经验参考。

---

# 1. 项目结论

本项目从原来的“多 Agent 小说编辑器”重构为：

> **一个以角色有限认知、独立目标和世界结算为核心的长篇故事演化桌面系统。**

系统不预先生成并强制执行完整剧情大纲。投稿过程负责建立一个可以运行的初始世界；角色依据自己的知识、欲望和当前局面提出行动，世界结算器决定实际后果，用户决定哪些结果正式成为故事。

核心循环：

```text
投稿形成初始世界
→ 角色分别提出行动
→ 世界结算行动后果
→ 编辑检查合理性
→ 用户确认
→ 提交 Story Event
→ 更新角色与世界
→ Writer 生成正文
→ 进入下一轮
```

第一版的首要目标不是功能数量，而是证明以下闭环长期稳定：

```text
角色知识隔离
+ 独立行动
+ 世界统一结算
+ 用户最终确认
+ 状态可追溯
```

---

# 2. 第一性原则

## 2.1 故事的本体是状态变化

```text
旧世界状态 + 角色行动 + 世界回应 = 新世界状态
```

“故事梗概”是对已确认事件的回顾，不是角色必须执行的未来剧本。

## 2.2 角色 Agent 不是常驻进程

角色 Agent 本质上是：

```text
角色卡 + 当前可感知信息 + 一次模型调用
```

调用结束后，连续性由正式状态文件维持。

## 2.3 角色只能决定意图，不能决定结果

- Character Agent：提出“我准备做什么”。
- World Resolver：决定“实际上发生什么”。
- Editor Agent：检查行为、知识和世界规则是否合理。
- User：决定结果是否正式提交。
- Writer Agent：把已确认事件写成正文。

## 2.4 Agent 不得直接写正式状态

所有模型输出都是候选内容：

```text
候选结果
→ Schema 校验
→ Editor 检查
→ 用户确认
→ EventCommitService
→ 正式写入
```

## 2.5 Markdown 是唯一事实来源

正式项目数据只存在于 Markdown 文件中。索引、缓存、Embedding、向量库和运行状态都必须可删除、可重建。

## 2.6 RAG 只负责召回，不负责授权

```text
权限过滤
→ 在允许访问的数据中检索
→ 注入上下文
```

不得对全项目直接检索后，依赖模型自行忽略秘密。

## 2.7 默认使用最少角色和最少 Agent

- 初始活跃角色：2～4 个。
- 普通人物默认不是 Agent。
- 普通人物只有形成独立目标并可能主动影响后续故事时，才提出升级建议。
- V1 不使用多 Agent 投票和复杂角色等级。

---

# 3. 技术基座

## 3.1 总体选择

```text
Jan Fork
= 桌面外壳、React UI、设置、主题、本地模型基础设施

Python Story Engine
= 小说领域逻辑、Markdown、模型网关、RAG、审核与提交

Original Concordia
= Character / Game Master 式演化能力

Novel / Tiptap
= 正文和设定文档编辑器
```

## 3.2 为什么以 Jan 为桌面基座

保留并改造：

- Tauri 桌面外壳；
- React 设计系统；
- 左侧栏、标题栏、面板和设置结构；
- 深色与浅色主题；
- 快捷键；
- 模型中心界面；
- Provider 管理界面；
- 本地模型下载、加载和运行能力；
- 文件附件和流式交互；
- 应用更新和桌面打包流程。

不保留 Jan 原有的通用聊天产品领域作为核心业务。

## 3.3 为什么使用原版 Python Concordia

- 不翻译为 Rust；
- 不在第一阶段维护 Concordia fork；
- 固定依赖版本；
- 在 `concordia_adapter` 外围实现小说领域约束；
- Concordia 只生成候选 Intent 和 Outcome，不写项目文件。

## 3.4 许可证策略

实施前必须锁定 Jan 与 Concordia 的具体 Commit，并完成目录级许可证清单。

默认原则：

- Apache-2.0 / MIT 模块可在保留声明的前提下复用；
- Jan 中单独声明 AGPL 的 RAG 扩展不直接复制到闭源发行物；
- RAG 接口和 UI 可以借鉴，检索后端默认由 Python Story Engine 重新实现；
- 仓库必须包含 `THIRD_PARTY_NOTICES.md`、`licenses/` 和修改说明；
- Jan、Novel 等项目的名称、Logo、插画和商标性资产必须替换。

---

# 4. 系统总体架构

```text
┌────────────────────────────────────────────┐
│ Jan-based Desktop UI                       │
│ React + Tauri                              │
│                                            │
│ 投稿 / 工作台 / 演化 / 角色 / 世界 / 事件  │
│ 正文 / 模型中心 / RAG 设置 / 应用设置       │
└─────────────────────┬──────────────────────┘
                      │ Local HTTP + WebSocket
                      │ Session Token
┌─────────────────────▼──────────────────────┐
│ Python Story Engine                        │
│ FastAPI + Pydantic                         │
│                                            │
│ SubmissionService                          │
│ EvolutionService                           │
│ CharacterContextAssembler                  │
│ WorldResolverAdapter                       │
│ EditorReviewService                        │
│ EventCommitService                         │
│ WriterService                              │
│ ModelGateway                               │
│ RetrievalGateway                           │
│ MarkdownWorkspace                          │
└───────────────┬─────────────────┬──────────┘
                │                 │
       ┌────────▼───────┐   ┌────▼────────────┐
       │ Original       │   │ Markdown Project │
       │ Concordia      │   │ + Derived Index  │
       └────────────────┘   └─────────────────┘
```

## 4.1 进程边界

### Tauri 负责

- 窗口和系统菜单；
- Python Sidecar 启动、停止和崩溃恢复；
- 本地模型下载与本地推理进程管理；
- 文件选择器、系统通知和应用更新；
- 安装包与操作系统集成。

### Python 负责

- 所有小说领域逻辑；
- 正式 Markdown 数据；
- 项目索引；
- 模型调用；
- Concordia 适配；
- RAG；
- 审核；
- Event 提交；
- 正文生成。

### React 负责

- UI 展示和交互；
- 状态订阅；
- 用户确认；
- 编辑器；
- 模型与任务配置界面。

React 不直接修改项目文件，也不直接决定正式状态。

## 4.2 通信协议

开发阶段：

```text
Tauri 启动：uv run story-engine serve
Python 绑定：127.0.0.1 随机端口
鉴权：每次启动生成随机 session token
请求：HTTP
流式事件：WebSocket
```

发布阶段：

- Python 使用 PyInstaller `--onedir` 或等价方式构建 Sidecar；
- Tauri 将 Sidecar 作为资源打包；
- 不采用 `--onefile`，避免大型依赖解压和启动延迟；
- Tauri 启动后先执行 `/health` 检查，再开放项目界面。

---

# 5. 产品工作流

## 5.1 项目创建：投稿

用户不需要填写故事梗概和章节大纲。

投稿聊天室围绕以下内容讨论：

```text
创作方向
世界规则
初始角色
初始局面
```

最终生成“初始设定包”：

```yaml
creative_direction:
  genre: 悬疑
  theme: 真相与亲情之间的选择
  tone: 克制、现实、缓慢积压

world:
  location: 雾港
  rules:
    - 灯塔控制港口夜航
    - 暴风雨时港口必须依赖灯塔

characters:
  - id: chen-mo
    desire: 找到父亲失踪真相
    current_goal: 查明灯塔熄灭原因
    known_facts:
      - 父亲十年前在灯塔附近失踪

initial_state:
  time: 暴风雨前夜
  location: 雾港
  incident: 灯塔突然熄灭
  pressure: 一艘客船即将进港
```

项目可启动条件：

1. 至少一个活跃角色；
2. 每个活跃角色存在当前目标；
3. 角色目标存在冲突，或世界存在明确压力；
4. 角色初始知识边界明确；
5. 有具体的时间、地点和起始事件。

## 5.2 单轮故事演化

```text
读取当前世界
→ 选择参与角色
→ 为每个角色构建私有上下文
→ 并行生成 Character Intent
→ World Resolver 统一结算
→ Editor 检查
→ 用户确认
→ 提交 Story Event
→ 更新角色与世界
```

### Character Intent

```json
{
  "character_id": "chen-mo",
  "action": "检查灯芯槽",
  "target": "灯塔照明装置",
  "goal": "判断灯塔是否被人为关闭",
  "knowledge_basis": [
    "knowledge:lighthouse-never-off"
  ],
  "recognized_risk": "可能暴露自己的调查"
}
```

角色不得输出：

- 行动已经成功；
- 其他角色如何回应；
- 新世界规则；
- 正式事件文本；
- 自己不知道的秘密。

### World Outcome

```json
{
  "summary": "陈默在灯芯槽中发现了新鲜刮痕。",
  "public_results": [],
  "hidden_results": [],
  "character_changes": [],
  "world_changes": [],
  "new_npcs": [],
  "unresolved_consequences": []
}
```

### 用户操作

V1 只保留：

```text
确认本轮
要求修改
放弃本轮
```

任何用户修改都会使旧审核结果失效并重新执行检查。

## 5.3 普通人物和角色升级

World Resolver 可以为了回应角色行动创建最小普通人物。

创建前只判断：

```text
现有角色能否合理承担该作用？
能：复用
不能：创建普通人物
```

普通人物形成独立目标并可能主动影响后续故事时，Editor 提出：

```text
建议升级为活跃角色 Agent
```

必须由用户确认。系统不得自动升级。

## 5.4 正文生成

```text
已确认 Story Event
→ Writer 生成场景草稿
→ 用户编辑
→ 事实差异检查
→ 保存正文
```

Writer 只能写已确认事实。

用户在正文中引入新事实时：

```text
检测新事实
→ 创建 Event Amendment 候选
→ 审核和确认
→ 再写入正式状态
```

---

# 6. Agent 体系

V1 只保留四种模型角色。

| Agent | 输入 | 输出 | 禁止 |
|---|---|---|---|
| Character | 私有角色上下文 | Action Intent | 决定结果、读取秘密 |
| Resolver | 世界规则与全部 Intent | Outcome Candidate | 直接提交正式状态 |
| Editor | 候选结果与证据 | Review Result | 替用户批准 |
| Writer | 已确认事件与风格规范 | Scene Draft | 创造未确认事实 |

用户承担最终主编职责。

Editor 使用同一个 Agent，不拆成多个常驻专业 Agent，通过模式区分：

```text
submission_review
character_review
world_review
turn_review
promotion_review
manuscript_review
```

---

# 7. 核心领域模型

## 7.1 Character

```python
class Character:
    id: str
    type: Literal["active", "npc", "retired"]
    identity: str
    core_desire: str
    current_goal: str | None
    known_fact_ids: list[str]
    relationships: list[Relationship]
    location: str | None
    emotional_state: str | None
    resources: list[str]
    last_event_id: str | None
    version: int
```

## 7.2 WorldState

```python
class WorldState:
    current_time: str
    current_location: str | None
    active_pressures: list[str]
    public_fact_ids: list[str]
    world_variables: dict[str, str | int | float | bool]
    version: int
```

## 7.3 StoryEvent

Story Event 创建后不可原地编辑，只能通过更正事件追加修改。

```python
class StoryEvent:
    id: str
    sequence: int
    occurred_at: str
    summary: str
    participants: list[str]
    public_results: list[str]
    hidden_results: list[str]
    character_changes: list[StateChange]
    world_changes: list[StateChange]
    source_turn_id: str
    approved_by_user: bool
```

## 7.4 TurnCandidate

```python
class TurnCandidate:
    id: str
    project_id: str
    base_world_version: int
    base_character_versions: dict[str, int]
    intents: list[CharacterIntent]
    outcome: WorldOutcome
    review: ReviewResult | None
    status: Literal[
        "draft",
        "reviewed",
        "needs_revision",
        "approved",
        "discarded",
        "committed"
    ]
```

## 7.5 并发保护

提交前必须检查：

```text
当前 world.version == candidate.base_world_version
当前 character.version == candidate 中记录的版本
```

不一致则拒绝提交，要求重新运行或重新基于最新状态审核。

---

# 8. Markdown-First 数据结构

```text
project/
├── project.md
├── world.md
├── characters/
│   ├── active/
│   │   ├── chen-mo.md
│   │   └── lin-lan.md
│   ├── npc/
│   └── retired/
├── events/
│   ├── 000001.md
│   └── 000002.md
├── scenes/
│   ├── scene-001.md
│   └── scene-002.md
├── sources/
└── .story-engine/
    ├── turns/
    ├── reviews/
    ├── cache/
    ├── index/
    └── recovery/
```

## 8.1 正式数据

正式事实只存在于：

- `project.md`
- `world.md`
- `characters/`
- `events/`
- `scenes/`

## 8.2 运行数据

`.story-engine/` 中允许使用 JSON、二进制索引和缓存。

这些文件必须可删除并重建。

## 8.3 角色卡示例

```md
---
schema: character/v1
id: chen-mo
type: active
version: 4
last_event_id: event-000012
---

# 陈默

## 身份

从外地返回雾港的机械工程师。

## 核心欲望

查明父亲失踪的真相。

## 当前目标

找到灯塔熄灭的原因。

## 已知事实

- fact:father-disappeared-near-lighthouse
- fact:lighthouse-never-off-at-night

## 重要关系

- 林岚：信任，但怀疑她有所隐瞒。
- 守塔人：警惕。

## 当前状态

- 位置：灯塔一层
- 情绪：紧张
- 资源：铜钥匙
```

## 8.4 写入规则

```text
写入临时文件
→ Pydantic / Front Matter 校验
→ fsync
→ 原子 rename
→ 更新内存索引
```

`EventCommitService` 是正式世界、角色和事件的唯一写入口。

---

# 9. 模型管理

## 9.1 单一模型注册中心

保留 Jan 的模型中心交互和本地模型基础设施，但 Story Engine 的领域调用统一经过 Python `ModelGateway`。

```text
Jan 模型中心 UI
→ Python Model Registry
→ Python ModelGateway
→ 远程 Provider 或 Jan 本地模型服务
```

避免 React 和 Python 各自维护一套模型调用逻辑。

## 9.2 任务模型档案

至少提供：

| Profile | 用途 |
|---|---|
| Character | 大量角色行动 |
| Resolver | 世界结算 |
| Editor | 审核与一致性检查 |
| Writer | 正文生成 |
| Embedding | RAG 索引 |

项目设置只保存 Profile ID 和覆写项，不保存密钥。

## 9.3 配置继承

```text
系统强制限制
> 临时任务覆写
> 项目覆写
> 全局任务模型档案
> 应用默认
```

## 9.4 密钥和配置

- 非敏感 Provider 配置：Python 配置文件；
- API Key：操作系统 Keychain；
- 本地模型：Jan/Tauri 下载和运行；
- Python 通过本地 OpenAI-compatible Endpoint 调用本地模型；
- 不把 API Key 写入项目目录；
- 不把完整密钥返回 React。

## 9.5 模型调用合同

所有调用必须提供：

```python
class ModelRequest:
    profile_id: str
    task_type: str
    messages: list[Message]
    output_schema: str | None
    max_output_tokens: int
    timeout_seconds: int
    temperature: float | None
```

统一执行：

- 超时；
- Token 上限；
- 响应字节上限；
- 取消；
- 重试；
- JSON Schema 校验；
- Provider 错误归一化；
- 使用量统计。

---

# 10. RAG 与记忆

## 10.1 V1 检索范围

V1 实现：

- Markdown 文件索引；
- 标题、Front Matter 和正文分块；
- 精确 ID 引用；
- 关键词 / BM25 检索；
- 检索证据展示；
- 项目、角色和任务范围过滤。

V1 不要求复杂向量数据库。

## 10.2 V1.5 混合检索

```text
关键词召回
+ Embedding 向量召回
+ 权限过滤
+ 重排
```

向量索引可使用可重建的本地缓存，不得成为正式数据。

## 10.3 三种权限范围

### Editorial Scope

可访问全部项目设定、事件、角色、来源和正文。

### Writer Scope

可访问已确认事件、当前场景、风格规范、必要世界规则和已完成正文。

### Character Scope

只能访问：

- 自己的角色卡；
- 自己知道的 Fact；
- 自己经历过的 Event；
- 自己当前能够感知的内容；
- 自己的关系和资源。

## 10.4 核心规则

> 权限决定角色能否知道；RAG 只决定角色本轮是否想起。

RAG 结果必须携带来源：

```json
{
  "chunk_id": "event-000012#result-2",
  "source_type": "event",
  "source_id": "event-000012",
  "permission_scope": "character:chen-mo",
  "score": 0.83
}
```

---

# 11. 桌面信息架构

## 11.1 主导航

```text
工作台
推进故事
角色
世界设定
事件历史
章节正文
模型中心
项目设置
```

全局设置仍从左下角头像进入。

## 11.2 工作台

只展示：

- 当前世界状态；
- 当前待处理事项；
- 最近 Story Event；
- 活跃角色；
- 主按钮“推进下一轮”。

## 11.3 推进故事

固定步骤：

```text
当前局面
→ 角色行动
→ 世界结算
→ 编辑检查
→ 用户确认
→ 正文
```

用户始终能看到：

- 当前步骤；
- 系统正在处理什么；
- 哪些 Agent 已完成；
- 下一步可执行操作。

## 11.4 角色

三组：

```text
活跃角色
普通人物
已退出角色
```

角色详情：

- 角色卡；
- 当前目标；
- 已知事实；
- 关系；
- 当前位置和状态；
- 相关事件。

## 11.5 世界设定

使用 Novel/Tiptap 编辑：

- 创作方向；
- 世界规则；
- 地点；
- 历史；
- 组织；
- 外部研究资料。

## 11.6 事件历史

默认时间线，Story Map 作为可选视图，不作为首页。

## 11.7 章节正文

```text
左：章节和场景列表
中：Novel 编辑器
右：来源事件和事实差异
```

## 11.8 视觉原则

- 继承 Jan 的成熟布局、间距、主题和组件；
- 一个页面只有一个视觉主角；
- 主体文字不低于 14px；
- 正文 17～18px；
- 紫色仅作当前选择和品牌强调；
- 绿色表示已确认；
- 琥珀表示等待或风险；
- 红色表示阻塞或错误；
- 不使用 Unicode 字符充当图标；
- 右侧 Inspector 默认可折叠。

---

# 12. 仓库结构

```text
ai-story-evolution-engine-next/
├── apps/
│   ├── desktop/                 # Jan-based React + Tauri
│   └── story-engine/            # Python Sidecar
│
├── packages/
│   ├── ui/                      # 迁移后的 Jan / shadcn 组件
│   ├── contracts/               # OpenAPI / JSON Schema / TS 类型
│   └── editor/                  # Novel/Tiptap 封装
│
├── docs/
│   ├── product-plan.md
│   ├── architecture.md
│   ├── domain-model.md
│   ├── data-contracts.md
│   ├── ui-flow.md
│   ├── ai-coding-guide.md
│   └── adr/
│
├── licenses/
├── THIRD_PARTY_NOTICES.md
├── pnpm-workspace.yaml
└── README.md
```

Python：

```text
apps/story-engine/src/story_engine/
├── api/
├── domain/
├── workspace/
├── submission/
├── characters/
├── evolution/
├── concordia_adapter/
├── review/
├── events/
├── writing/
├── models/
├── retrieval/
└── tests/
```

---

# 13. API 边界

## 13.1 核心接口

```text
POST   /projects
GET    /projects/{id}
POST   /projects/{id}/submission/messages
POST   /projects/{id}/submission/finalize

POST   /projects/{id}/turns
GET    /projects/{id}/turns/{turn_id}
POST   /projects/{id}/turns/{turn_id}/review
POST   /projects/{id}/turns/{turn_id}/approve
POST   /projects/{id}/turns/{turn_id}/discard

GET    /projects/{id}/characters
GET    /projects/{id}/characters/{character_id}
POST   /projects/{id}/characters/{character_id}/promote

GET    /projects/{id}/events
GET    /projects/{id}/events/{event_id}

POST   /projects/{id}/scenes/generate
PUT    /projects/{id}/scenes/{scene_id}

GET    /models/profiles
PUT    /models/profiles/{profile_id}
POST   /retrieval/search
```

## 13.2 WebSocket 事件

```text
engine.status
turn.started
character.intent.started
character.intent.delta
character.intent.completed
resolver.started
resolver.completed
review.started
review.completed
turn.failed
turn.cancelled
```

所有事件必须包含：

```json
{
  "event_id": "ulid",
  "project_id": "project-id",
  "turn_id": "turn-id",
  "timestamp": "ISO-8601",
  "type": "character.intent.completed",
  "payload": {}
}
```

---

# 14. 实施阶段

## Phase 0：仓库和架构冻结

目标：

- Fork Jan；
- 新建产品仓库；
- 增加 `upstream/jan`；
- 锁定 Jan 与 Concordia Commit / Version；
- 完成许可证清单；
- 建立 ADR；
- 建立 CI。

验收：

- Windows 和 macOS 能启动 Jan 基础壳；
- Python `/health` 可访问；
- React 能显示 Python 状态；
- 未引入旧项目数据库。

## Phase 1：产品壳与页面骨架

完成：

- 替换品牌；
- 重写主导航；
- 保留模型中心和全局设置；
- 建立工作台、演化、角色、世界、事件、正文空页面；
- 删除或隐藏 Jan 通用聊天产品入口；
- 保留可复用 UI。

验收：

- 所有主页面可导航；
- 深浅主题正常；
- 窗口缩放无布局破坏；
- 无旧 Jan 品牌资产。

## Phase 2：Python Sidecar 与 IPC

完成：

- FastAPI；
- WebSocket；
- 随机端口和 session token；
- Tauri 生命周期管理；
- Sidecar 日志；
- 取消和崩溃恢复；
- OpenAPI 生成 TS 类型。

验收：

- UI 可启动、停止和重启 Sidecar；
- 非法 token 被拒绝；
- Sidecar 崩溃后 UI 给出明确恢复入口。

## Phase 3：Markdown Workspace

完成：

- 项目创建、打开、关闭；
- Front Matter；
- Pydantic Schema；
- 原子保存；
- 文件监听；
- 内存索引；
- 版本号；
- Event 追加写；
- 恢复机制。

验收：

- 删除 `.story-engine/cache` 后项目可完整恢复；
- 未确认 Turn 不修改正式文件；
- Event 不可原地修改。

## Phase 4：模型中心与 ModelGateway

完成：

- 改造 Jan 模型管理 UI；
- Python Profile Registry；
- 远程 Provider；
- 本地 llama.cpp Endpoint；
- Character / Resolver / Editor / Writer / Embedding Profile；
- 流式输出；
- 用量统计；
- 结构化输出校验。

验收：

- 同一项目可为不同任务使用不同模型；
- API Key 不出现在项目文件和前端日志；
- 本地模型和远程模型可互换。

## Phase 5：投稿与初始世界

完成：

- 投稿聊天室；
- 初始设定实时侧栏；
- Editor 整理；
- 初始角色生成；
- 可运行条件检查；
- 项目文件生成；
- 创建后进入第一轮。

验收：

- 不填写大纲也能创建可运行项目；
- 初始角色只有 2～4 个；
- 角色知识边界明确。

## Phase 6：角色演化内核

完成：

- Concordia Adapter；
- Character Context Assembler；
- 独立 Intent；
- 并行调用；
- World Resolver；
- TurnCandidate；
- 普通人物生成；
- 取消和重试。

验收：

- 角色看不到其他角色秘密；
- 角色看不到其他角色本轮 Intent；
- 角色不决定结果；
- 连续十轮状态仍可追溯。

## Phase 7：审核与提交

完成：

- Editor Review；
- 知识越界检查；
- 世界规则检查；
- 状态变更来源检查；
- 用户确认；
- 乐观并发检查；
- EventCommitService；
- NPC 升级建议。

验收：

- 未经用户确认不能修改正式状态；
- 修改候选结果后旧审核失效；
- 版本冲突时拒绝提交。

## Phase 8：Writer 与正文

完成：

- 从 Event 生成场景；
- Novel 编辑器；
- 章节和场景列表；
- 来源事件；
- 事实差异检测；
- Event Amendment；
- Markdown 导出。

验收：

- Writer 不能生成未确认 Canon；
- 用户新增事实会产生 Amendment 候选。

## Phase 9：RAG V1

完成：

- Markdown 分块；
- 精确 ID 检索；
- BM25；
- Scope 权限过滤；
- 检索证据查看；
- 索引重建；
- Writer 和 Editor 检索。

验收：

- Character Scope 无法返回未授权内容；
- 删除索引后可完整重建；
- 每个注入片段都有来源。

## Phase 10：桌面打包与稳定性

完成：

- Python Sidecar 打包；
- Windows / macOS 构建；
- 自动更新；
- 崩溃日志；
- 大项目性能测试；
- 外部文件修改冲突；
- 安装与卸载验证。

---

# 15. AI Coding 执行规则

## 15.1 单任务原则

每个 AI Coding 任务必须只完成一个清晰目标，例如：

```text
实现 CharacterIntent Pydantic Schema 和验证测试
```

不得使用：

```text
完成整个演化系统
```

## 15.2 开始编码前必须读取

```text
docs/product-plan.md
docs/architecture.md
docs/domain-model.md
docs/data-contracts.md
相关 ADR
目标模块现有测试
```

## 15.3 每个任务的固定输入

```text
目标
允许修改的目录
禁止修改的目录
输入输出合同
验收标准
运行命令
```

## 15.4 每个任务的固定输出

AI 必须报告：

1. 修改了哪些文件；
2. 为什么这样实现；
3. 新增了哪些测试；
4. 执行了哪些命令；
5. 仍有哪些限制；
6. 是否修改了 API 或 Schema。

## 15.5 禁止事项

- 不得为了“以后可能需要”增加抽象层；
- 不得绕过 Pydantic / JSON Schema；
- 不得让模型直接写 Markdown；
- 不得在 React 中复制领域状态；
- 不得引入第二个正式数据源；
- 不得把 RAG 结果当作角色知识授权；
- 不得在一个 PR 中同时重构平台层和领域层；
- 不得在没有 ADR 的情况下更换核心技术；
- 不得保留无测试的兼容代码；
- 不得直接复制许可证不明确的源文件。

## 15.6 测试先行顺序

```text
定义合同
→ 写失败测试
→ 最小实现
→ 运行测试
→ 重构
→ 更新文档
```

## 15.7 PR 大小

建议：

- 单 PR 尽量不超过 500 行有效业务变更；
- 大型迁移拆成“复制原文件”“完成适配”“删除旧入口”三个 PR；
- Schema 变更单独 PR；
- 许可证和第三方声明与代码迁移同一 PR 完成。

---

# 16. CI 和质量门槛

每个 PR 必须通过：

```text
Frontend:
pnpm lint
pnpm typecheck
pnpm test
pnpm build

Python:
ruff check
mypy
pytest
python -m build

Contracts:
OpenAPI / TS types 无未提交差异

Desktop:
cargo fmt --check
cargo clippy
tauri build smoke test
```

核心测试：

- Character 隐私边界；
- 其他角色 Intent 隔离；
- 未确认状态不可写入；
- Event 不可变；
- 版本冲突；
- Sidecar 重启；
- 模型输出超限；
- JSON 解析失败；
- RAG Scope；
- Writer 新事实检测；
- 外部 Markdown 修改冲突。

---

# 17. V1 明确不做

- 完整剧情大纲；
- 自动规划结局；
- 无限后台自动演化；
- 角色常驻进程；
- 复杂角色等级；
- 自动角色晋升；
- 多 Agent 投票；
- 多个常驻编辑 Agent；
- 角色自我修改 Prompt；
- 角色跨权限向量检索；
- 知识图谱；
- 多人协作；
- 云同步；
- 分支宇宙；
- 全自动章节发布；
- 旧数据库迁移兼容。

---

# 18. V1 完成定义

满足以下条件才算 V1 完成：

1. 用户可通过投稿讨论创建初始世界；
2. 项目不依赖完整大纲；
3. 至少两个角色能依据私有知识分别行动；
4. World Resolver 能统一结算；
5. Editor 能发现明显知识越界和规则冲突；
6. 用户确认前正式 Markdown 不发生变化；
7. 确认后 Story Event、角色和世界状态一致更新；
8. 可以连续运行至少 30 个回合；
9. Writer 能把已确认事件生成正文；
10. 本地模型和远程模型都可配置；
11. RAG 不会向角色泄漏未授权事实；
12. 删除全部派生索引后项目仍可恢复；
13. Windows 和 macOS 安装包可启动；
14. 项目数据可由用户直接查看和备份；
15. UI 不暴露 Concordia、Entity、Component 等内部框架概念。

用户最终感受到的应当是：

> 我建立了一个世界，角色按照自己的认知做出选择，我决定哪些结果真正成为故事。

而不是：

> 我在操作一套复杂的 Agent 编排系统。

---

# 19. 推荐的首批 AI Coding 任务

按顺序执行：

1. 建立新仓库、Jan upstream 和许可证清单；
2. 保留 Jan App Shell，替换品牌和主导航；
3. 建立 Python FastAPI `/health`；
4. 完成 Tauri Sidecar 生命周期管理；
5. 建立 OpenAPI 自动生成 TS 类型；
6. 定义 Character、WorldState、StoryEvent、TurnCandidate；
7. 完成 Markdown 原子写入；
8. 完成 ProjectStore 和 CharacterStore；
9. 完成 EventStore 和不可变事件测试；
10. 建立 Model Profile Schema；
11. 接通一个远程模型与一个本地模型；
12. 建立 Concordia Adapter 的固定测试场景；
13. 完成 CharacterContextAssembler；
14. 完成两个角色的独立 Intent；
15. 完成 World Resolver；
16. 完成用户确认与 EventCommitService；
17. 完成演化页面；
18. 完成投稿页面；
19. 接入 Novel 正文编辑器；
20. 完成 BM25 与权限过滤。

在第 16 项通过以前，不开发 Story Map、办公室动画和复杂角色关系图。
