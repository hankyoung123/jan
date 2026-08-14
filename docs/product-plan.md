# Living Story World

## 产品需求文档 PRD v1.0

**产品代号：** AI Story Evolution Engine
**当前产品方向：** Persistent AI World Simulator
**阶段：** MVP
**核心技术基础：** Jan + Concordia
**产品状态：** 未发布，可进行破坏性重构，不考虑旧版本兼容

---

# 1. 产品定义

## 1.1 一句话定义

> **一个真的会回应你的故事世界。**

用户不是在让 AI “讲故事”，也不是在和角色“聊天”。

用户进入一个持续存在的世界，以其中一个 Actor 的身份自由行动。

这个世界拥有：

* 独立角色
* 持续状态
* 隐藏事实
* 时间推进
* 因果约束
* 角色记忆
* 自主行动
* 可回溯历史

用户可以自由表达：

> 我过去看看。

> 我问林澈为什么骗我。

> 我抢张野手里的包。

> 我想办法打开二楼的门。

但：

> **用户拥有自己的意图，不拥有世界的结果。**

真正发生什么，由当前世界事实、人物状态、资源、环境和其他角色共同决定。

---

# 2. 产品愿景

传统 AI Story / AI Roleplay 产品本质通常仍然是：

```text
用户输入
↓
LLM 生成下一段文本
↓
继续聊天
```

即使拥有角色卡、Memory 或多个 Agent，世界往往仍然会：

* 顺从用户描述
* 临时创造事实
* 忘记之前状态
* 泄露角色秘密
* 等待玩家触发
* 为了剧情方便改变规则

Living Story World 的目标是建立：

```text
持续 World
+
自主 Actor
+
统一 Resolution
+
可靠 Memory
+
用户自由 Intent
```

让用户产生：

> **“我不是在提示 AI 写下一段，而是真的进入了某个世界。”**

的体验。

---

# 3. 核心产品假设

MVP 只验证一个假设：

> 如果 AI 世界不会无条件迎合用户，而是按照持续世界状态、角色能力和因果关系回应用户，这种体验是否比传统 AI Chat / Tavern / Interactive Fiction 更有沉浸感和持续吸引力。

MVP 不验证：

* AI 能不能写出最好看的小说
* 世界能不能无限大
* 能不能模拟复杂战斗
* 能不能生成图片
* 能不能 24 小时持续运行

---

# 4. 目标用户

第一阶段面向：

### 核心用户

喜欢以下体验的人：

* AI Roleplay
* Interactive Fiction
* TRPG / 跑团
* 沉浸式故事
* 悬疑推理
* 角色驱动叙事
* 世界模拟

但他们对传统 AI Roleplay 的以下问题不满意：

* AI 太迎合
* 角色不独立
* 世界没有真实状态
* 可以随意“嘴炮改现实”
* 记忆容易错乱
* 没有真正错过机会
* 选择没有长期后果

### 非首要用户

MVP 暂不主要服务：

* 专业小说作者
* 纯文字编辑用户
* 游戏开发者
* TRPG 模组制作人
* 纯聊天陪伴用户

未来可以扩展。

---

# 5. 产品核心原则

以下规则属于产品最高级约束。

任何功能设计与其冲突时，应优先遵守这些原则。

---

## 5.1 Intent ≠ Fact

用户输入永远首先被理解为：

> **Intent / Attempt**

而不是事实。

例如：

用户输入：

> 我杀了张野。

内部语义是：

> 我试图让张野死亡。

不是：

> 张野已经死亡。

---

## 5.2 User owns Intent, GM owns Outcome

权限划分：

| 内容        | 权限         |
| --------- | ---------- |
| 用户想做什么    | 用户         |
| 用户主动说什么   | 用户         |
| 用户相信什么    | 用户         |
| NPC 想做什么  | NPC        |
| NPC 主动说什么 | NPC        |
| 实际发生什么    | World / GM |

---

## 5.3 User 与 NPC 遵守相同规则

NPC 不能：

> “我趁玩家不注意偷走了相机。”

然后直接把相机变成自己的。

NPC 同样只能提交：

> 尝试偷走相机。

之后统一 Resolution。

---

## 5.4 ResolvedEvent 是唯一 committed history

以下都不能单独改变世界：

* User Input
* NPC Output
* Putative Intent
* LLM Reasoning
* Narrative Text
* Actor Belief
* Perception

只有：

> **Validated ResolvedEvent**

能够进入世界历史。

---

## 5.5 World Truth、Knowledge、Belief 分离

必须始终保持：

```text
World Truth
≠
Actor Knowledge
≠
Actor Belief
```

例如：

> 我确定张野就是幕后的人。

可以改变玩家 belief。

不能改变：

> 张野到底是不是幕后的人。

---

## 5.6 世界不奖励“说得合理”

AI 不应该因为用户提出一个听起来聪明的方案就默认成功。

真实结果取决于：

```text
Actor
+
Knowledge
+
Capability
+
Condition
+
Resources
+
Environment
+
Other Actors
+
Time
```

原则：

> **只奖励当前世界真正提供的实施条件。**

---

# 6. 核心领域模型

产品层只暴露六个核心概念：

```text
World
Actor
Perception
Intent
Resolution
Memory
```

---

## 6.1 World

World 表示客观存在的世界事实。

包括：

* 时间
* 地点
* 世界规则
* 当前角色位置
* 重要物品
* 事件历史
* 环境状态
* 隐藏事实
* 当前压力

World 不等于一份巨大的 JSON。

它是由持久化状态、Memory 和 committed events 共同构成的持续世界。

---

## 6.2 Actor

Player 与 NPC 使用相同 Actor 模型。

Actor 可以包含：

```text
Identity
Knowledge
Capabilities / Experience
Conditions
Resources
Relationships
Location
Goal
Beliefs
Memory
```

不采用传统 RPG 式：

```text
STR 8
DEX 7
HP 100
Lockpick 14
```

除非某个世界本身需要明确数值。

---

## 6.3 Perception

Perception 表示：

> **这个 Actor 此刻实际能够感知和知道什么。**

Perception 不是完整 World。

它不能包含：

* NPC private memory
* GM hidden truth
* 未发现证据
* 其他角色心理
* 模型 reasoning

---

## 6.4 Intent

用户自由自然语言输入。

例如：

> 我靠近窗户看看外面。

> 我直接质问她。

> 我等十分钟看看张野会不会离开。

> 我试着说服店主把钥匙给我。

产品不要求用户学习：

* Command
* JSON
* Action Type
* Skill 名称
* Target ID

---

## 6.5 Resolution

Resolution 是世界唯一语义裁判。

负责：

```text
Intent
+
Current Reality
↓
What Actually Happens
```

Resolution 可以产生：

* 成功
* 部分成功
* 失败
* 意外
* 被阻止
* 被误解
* 被发现
* 时间推进
* 状态改变
* NPC 行动

---

## 6.6 Memory

Memory 回答：

> 这个 Actor 经历过什么、知道什么、记得什么。

Memory 不等于 Actor State。

边界：

> **Memory = 以前发生过什么。**

> **Actor State = 我现在是什么状态。**

---

# 7. 核心用户循环

产品最重要的体验闭环：

```text
Perception
↓
User Intent
↓
Resolution
↓
ResolvedEvent
↓
World / Actor / Memory Update
↓
Time Advances
↓
NPC Response
↓
New Perception
```

用户持续重复这一循环。

没有传统意义上的：

* 主线任务列表
* 推荐选项
* 技能按钮
* 对话选项

---

# 8. 世界交互设计

## 8.1 自由输入

核心输入框：

> **说出你想做的事……**

用户可以同时：

* 行动
* 对话
* 思考
* 观察
* 等待
* 试探
* 欺骗
* 改变计划

---

## 8.2 不提供 Action Menu

禁止作为核心交互：

```text
[调查桌子]
[询问林澈]
[跟踪张野]
[上二楼]
```

原因：

1. 限制用户想象力
2. 暗示哪些东西重要
3. 将产品退化成传统互动小说
4. 让用户寻找“正确按钮”

---

# 9. 感知模型

用户不应该通过点击每个物体进行像素级探索。

采用三个自然语义层级：

### Glance

自然扫视。

进入场景自动获得：

* 人
* 明显结构
* 环境
* 强烈变化

### Observe

用户主动关注：

> 我仔细看看桌面。

得到一组合理细节。

### Inspect

需要真正操作：

> 我把收据拿起来看看背面。

三个层级：

> **不是三个系统，只是自然语言 Intent 的不同深度。**

---

# 10. 探索与证据原则

悬疑、调查类世界必须遵守：

> **Evidence Conservation**

关键因果不能因为用户调查而临时生成。

允许即时补充：

* 普通家具
* 日常物品
* 环境纹理
* 无关生活细节

禁止因为玩家“看了一下”而生成：

* 新凶器
* 关键证据
* 秘密通道
* 新证人
* 新不在场证明
* 改变谜底的事实

原则：

> **可以即时生成世界纹理，不能即时生成核心因果。**

---

# 11. 世界对象策略

不建立完整物理世界数据库。

采用：

### Hard Reality

必须明确存在：

* 关键人物
* 关键物品
* 证据
* 钥匙
* 重要门锁
* 因果状态
* 时间线

### Soft Environment

可按现实常识即时补充：

* 普通杯子
* 文具
* 垃圾桶
* 家具

如果 Soft Object 后续进入因果链：

> 从此成为持续世界事实。

MVP 不建立独立 Soft Environment Engine。

---

# 12. Actor Capability

产品不提供“允许/禁止动作列表”。

例如没有：

```text
can_lockpick = false
can_attack = true
```

而是维护：

```text
没有开锁经验
右手轻伤
有手机
没有开锁工具
认识店主
```

用户仍然可以说：

> 我试试开锁。

世界判断真实结果。

---

# 13. NPC 自主性

NPC 不是等待玩家点击的 Chatbot。

每个重要 NPC 应拥有：

* 自己知道的信息
* 当前目标
* 自己的关系
* 当前状态
* 私人 Memory

NPC 可以：

* 拒绝
* 撒谎
* 离开
* 改变计划
* 隐瞒
* 主动干涉
* 做错决定
* 错过机会
* 失败

NPC 不需要每回合全部运行。

只有当前真正需要作出新决定的角色参与。

---

# 14. 时间

世界时间真实存在。

用户调查时，其他人不会永久等待。

例如：

> 张野计划 19:10 离开旅馆。

用户如果长期做其他事情：

> 张野可以真的离开。

时间产生：

* 错失机会
* Deadline
* NPC 行为变化
* 世界变化

MVP 不开发复杂 Time Engine。

由 Resolution 维护合理时间推进。

---

# 15. Persistent World

用户退出后：

* 世界状态保存
* Actor 状态保存
* Memory 保存
* 时间保存
* 历史保存
* Branch 保存

再次进入：

> 用户必须看到离开时那个世界的当前状态。

而不是重新看到故事开场。

MVP 不要求世界在用户退出后 24 小时实时模拟。

未来可以实现：

> Deferred Settlement

即重新进入时一次性结算离开期间发生的重要变化。

---

# 16. Branch / Rewind

Checkpoint 是世界历史节点。

用户可以：

```text
18:43 进入旅馆
   ↓
18:52 张野离开
   ↓
19:03 当前
```

从过去节点：

> **从这里继续**

系统创建新的 Branch。

原历史保持不变。

Branch 不是“读档覆盖”，而是：

> Alternate History。

---

# 17. Self Lens

用户可以随时查看：

> **我现在是谁？**

显示：

* Identity
* Capability / Experience
* Conditions
* Important possessions
* Important relationships

禁止：

* HP
* Skill Level
* Success Chance
* RPG Stat
* “推荐你可以用相机……”

Self Lens 展示事实。

不展示攻略。

---

# 18. World Session UI

MVP 主界面必须尽可能像“世界”，而不是聊天软件。

基本结构：

```text
                   22:15
                 四楼楼道


暴雨敲打着楼道尽头的窗。

停电后，电梯已经无法使用。
应急灯照着 403、405 和楼梯口。

一张中午留下的快递单压在墙边。

沈遥站在 403 门口看向周宁。

“相机几个小时前就不见了。”


            说出你想做的事……
```

辅助功能：

* Self Lens
* Timeline
* Branch

不显示传统 Chat Bubble。

---

# 19. 默认 MVP 世界

内置世界：

# 《雨夜公寓》

它既是 Demo，也是产品集成测试。

---

## 场景

暴雨夜。

老城区公寓四楼楼道。

时间是 22:15，整栋楼刚刚停电，电梯不可用。

周宁赶到朋友沈遥住的 403，想取回落在她家的相机。相机里还有没有备份的照片。

沈遥告诉周宁：

> 她下午还见过相机，但几个小时前它已经不见了。

邻居顾衡住在 405，只想休息，不愿被卷入麻烦。他听到过楼道里的争执声。

403、405、楼梯口、失效的电梯和应急灯都是明确可见的环境。一张写着“陆明、402、中午”的快递单留在墙边；它只是环境事实，不是相机失踪的关键证据。

---

## Player

身份：

> 自由摄影师周宁

能力：

* 调查采访
* 摄影
* 基础设备检查

随身：

* 钱包
* 手机

当前目标：

> 找回自己的相机，并保护其中尚未备份的照片。

---

## NPC

### 沈遥

周宁的朋友，住在 403。

她想弄清是谁拿走了相机，同时避免惹上麻烦。

她知道下午还见过相机，也知道后来有人敲过门。

### 顾衡

住在 405 的邻居。

他想休息、不被卷入事情。

他知道自己听到过楼道里的争执声。

---

# 20. 初始事实边界

默认世界初始化时只建立已经声明的事实和知识边界：

1. 暴雨、停电、电梯不可用
2. 四楼楼道、403、405、楼梯和应急灯可见
3. 周宁拥有相机，知道它落在沈遥家且照片未备份
4. 沈遥知道下午见过相机，之后有人敲门
5. 顾衡知道自己听到过楼道争执
6. 快递单存在，但不是相机事件的关键证据

默认世界不预设犯人、幕后真相或固定终点。后续事实只能来自已提交事件的因果结果：

> 检查可见环境可以揭示已有信息，但不能临时制造关键证据。

---

# 21. MVP 功能范围

MVP 必须实现：

### World

* 持久状态
* 时间
* 位置
* 基础规则
* Hard causal facts

### Actor

* Player Actor
* NPC Actor
* Private Memory
* State
* Goal
* Knowledge boundary

### Simulation

* Natural-language Intent
* Unified GM Resolution
* ResolvedEvent
* State Effects
* NPC response
* Time progression

### Perception

* 玩家当前可见世界
* Visible events
* 当前自身状态
* 信息隔离

### Persistence

* Checkpoint
* Restore
* Branch
* Timeline

### UI

* World Session
* Intent Input
* Self Lens
* Branch / Timeline

---

# 22. MVP 明确不做

第一版不开发：

* Writer Agent
* 自动小说章节
* Editor Agent 主流程
* 投稿聊天室主流程
* Director Agent
* Planner Agent
* Combat Engine
* Skill Engine
* Inventory Engine
* Quest System
* Achievement
* 复杂经济系统
* Multiplayer
* Voice
* Runtime Image Generation
* World Marketplace
* Character Marketplace
* 24h 实时世界运行

现有旧代码可以保留，但不得成为 World Session 核心依赖。

---

# 23. 小说输出的新定位

小说不再是产品核心运行过程。

未来可以：

```text
World History
+
ResolvedEvents
+
Actor Memory
+
Branch
↓
Writer
↓
Novel / Chapter
```

即：

> **小说是世界经历的一个 Projection。**

不是：

> 世界为了生成小说而运行。

这是一条重要产品边界。

---

# 24. 技术架构原则

Concordia 继续作为唯一 simulation kernel。

映射：

| 产品概念       | 实现                              |
| ---------- | ------------------------------- |
| World      | GM + Components + durable state |
| Actor      | Concordia Entity + Actor State  |
| Perception | Derived projection              |
| Intent     | Action Attempt                  |
| Resolution | Game Master adjudication        |
| Memory     | Concordia Memory                |
| History    | ResolvedEvent                   |
| Rewind     | Checkpoint / Branch             |

禁止重新创建：

* PlayerEngine
* WorldEngine
* ResolutionEngine
* CombatEngine
* InventoryEngine

原则：

> **Concordia 是实现，World → Perception → Intent → Resolution 是产品协议。**

---

# 25. 数据权威

系统必须保持：

```text
ResolvedEvent
Checkpoint
Actor State
Memory
```

为真实数据。

以下只能是 Projection：

```text
Wiki
Narrative
UI Scene
Summary
Manuscript
```

Provider Cache 未来也只能是性能优化。

不得成为 Source of Truth。

---

# 26. MVP 核心验收案例

必须验证：

### Case 01

> 我杀了张野。

不能自动死亡。

### Case 02

> 我掏出枪。

如果 Actor 没有枪，不能凭空生成。

### Case 03

> 我查教程把锁打开。

可以获得知识。

但知识不自动等于能力和工具。

### Case 04

> 我偷偷跟过去，没人发现。

“没人发现”不是用户可以决定的事实。

### Case 05

> 我抢他的包。

NPC 可以反抗。

### Case 06

NPC：

> 我偷走玩家相机。

同样必须经过 Resolution。

### Case 07

> 我确定张野就是凶手。

只改变 Actor belief。

### Case 08

NPC 私密知识不能泄露。

### Case 09

随机调查垃圾桶不能生成关键证据。

### Case 10

长时间调查后，NPC 可以自行离开。

### Case 11

玩家提出未预设方案：

> 用相机长焦观察二楼。

不能回答：

> “不支持这种操作。”

必须按 Reality 裁定。

### Case 12

从旧 Checkpoint 创建 Branch。

两条世界线应真正产生不同后续。

---

# 27. MVP 完成标准

用户能够连续完成：

```text
进入世界
↓
自由行动
↓
被世界约束
↓
NPC 自主反应
↓
世界持续变化
↓
退出
↓
重新进入
↓
世界保持一致
↓
从历史节点创建另一条世界线
```

并满足：

1. User Input 不能直接修改 World Truth
2. NPC Intent 同样不能直接修改世界
3. Player 状态真实影响 Resolution
4. NPC Private Knowledge 不泄露
5. 关键资源不能凭空生成或复制
6. 世界时间能够推进
7. NPC 可以错过、离开、拒绝和失败
8. 重新进入后 Perception 是当前世界
9. 30+ Turn 无明显因果错误
10. Branch 保持独立世界历史

满足以上条件：

> World Simulation MVP 完成。

---

# 28. 成功指标

MVP 首先关注体验验证，而不是 DAU。

核心定性问题：

### Reality

用户是否觉得：

> “这个世界不是围着我转。”

### Autonomy

用户是否觉得：

> “这些角色有自己的事情。”

### Freedom

用户是否觉得：

> “我真的可以尝试任何合理的事情。”

### Causality

用户是否相信：

> “事情发生是因为之前的世界状态，而不是 AI 临时编。”

### Continuity

重新进入后用户是否认为：

> “这是我刚才离开的那个世界。”

---

# 29. 产品北极星

产品未来的竞争力不来自：

> 更多 Agent。

也不来自：

> 更长 Prompt。

而来自：

```text
可靠世界状态
+
独立角色认知
+
统一因果裁决
+
持续记忆
+
开放自然语言行动
+
可回溯世界历史
```

最终产品应让用户产生一个简单但强烈的感觉：

> **这个世界并不知道我要做什么。**

> **但无论我做什么，它都会真实地回应我。**
