import {
  ArrowRight,
  Check,
  ChevronDown,
  FileText,
  Filter,
  LockKeyhole,
  MoreHorizontal,
  Plus,
  Save,
  Search,
  ShieldCheck,
  Sparkles,
  TriangleAlert,
  UserRoundPlus,
} from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";

import { EvolutionWorkspace } from "./features/evolution/EvolutionView";

function PageHeader({
  title,
  eyebrow,
  action,
}: {
  title: string;
  eyebrow: string;
  action?: React.ReactNode;
}) {
  return (
    <header className="page-header">
      <div>
        <p className="eyebrow">{eyebrow}</p>
        <h1>{title}</h1>
      </div>
      {action}
    </header>
  );
}

function StatusBadge({
  tone,
  children,
}: {
  tone: "confirmed" | "pending" | "blocked" | "neutral";
  children: React.ReactNode;
}) {
  return <span className={`status-badge status-${tone}`}>{children}</span>;
}

export function WorkbenchView() {
  return (
    <div className="page">
      <PageHeader
        eyebrow="雾港 / 第 12 回合"
        title="工作台"
        action={
          <Link className="button button-primary" to="/evolve">
            推进下一轮 <ArrowRight size={16} />
          </Link>
        }
      />

      <section className="situation-band" aria-labelledby="situation-title">
        <div className="situation-copy">
          <p className="section-kicker">当前世界</p>
          <h2 id="situation-title">暴风雨前夜，灯塔失去光源</h2>
          <p>
            客船“海燕号”将在四十分钟后抵港。陈默已经进入灯塔，林岚仍在港务所尝试恢复备用航标。
          </p>
        </div>
        <dl className="situation-facts">
          <div>
            <dt>时间</dt>
            <dd>22:18</dd>
          </div>
          <div>
            <dt>地点</dt>
            <dd>雾港</dd>
          </div>
          <div>
            <dt>压力</dt>
            <dd className="text-amber">客船逼近</dd>
          </div>
        </dl>
      </section>

      <div className="workbench-grid">
        <section className="panel" aria-labelledby="pending-title">
          <div className="panel-heading">
            <div>
              <p className="section-kicker">待处理</p>
              <h2 id="pending-title">编辑队列</h2>
            </div>
            <span className="count">3</span>
          </div>
          <div className="task-list">
            <div className="task-row">
              <span className="task-icon pending">
                <TriangleAlert size={16} />
              </span>
              <div>
                <strong>确认守塔人的知识边界</strong>
                <p>Editor 标记了 1 项潜在越界</p>
              </div>
              <StatusBadge tone="pending">待检查</StatusBadge>
            </div>
            <div className="task-row">
              <span className="task-icon neutral">
                <FileText size={16} />
              </span>
              <div>
                <strong>场景 012 尚未生成正文</strong>
                <p>来源事件 event-000012</p>
              </div>
              <StatusBadge tone="neutral">未开始</StatusBadge>
            </div>
            <div className="task-row">
              <span className="task-icon confirmed">
                <Check size={16} />
              </span>
              <div>
                <strong>世界索引已重建</strong>
                <p>42 个片段均有来源</p>
              </div>
              <StatusBadge tone="confirmed">完成</StatusBadge>
            </div>
          </div>
        </section>

        <section className="panel" aria-labelledby="characters-title">
          <div className="panel-heading">
            <div>
              <p className="section-kicker">在场角色</p>
              <h2 id="characters-title">活跃角色</h2>
            </div>
            <Link className="text-link" to="/characters">
              查看全部
            </Link>
          </div>
          <div className="character-rows">
            <div className="character-row">
              <span className="avatar avatar-coral">陈</span>
              <div>
                <strong>陈默</strong>
                <p>目标：查明灯塔熄灭原因</p>
              </div>
              <span className="location">灯塔一层</span>
            </div>
            <div className="character-row">
              <span className="avatar avatar-green">林</span>
              <div>
                <strong>林岚</strong>
                <p>目标：让海燕号安全进港</p>
              </div>
              <span className="location">港务所</span>
            </div>
            <div className="character-row">
              <span className="avatar avatar-amber">周</span>
              <div>
                <strong>周放</strong>
                <p>目标：避免十年前的记录曝光</p>
              </div>
              <span className="location">旧码头</span>
            </div>
          </div>
        </section>
      </div>

      <section className="event-strip" aria-labelledby="recent-event">
        <div className="event-sequence">012</div>
        <div>
          <p className="section-kicker">最近确认事件 · 22:12</p>
          <h2 id="recent-event">陈默在灯芯槽中发现新鲜刮痕</h2>
          <p>世界版本 12 · 角色版本 陈默 8 / 林岚 7</p>
        </div>
        <StatusBadge tone="confirmed">已确认</StatusBadge>
      </section>
    </div>
  );
}

export function EvolutionView() {
  return <EvolutionWorkspace />;
}

export function CharactersView() {
  const [group, setGroup] = useState("active");
  return (
    <div className="page">
      <PageHeader
        eyebrow="3 个活跃角色"
        title="角色"
        action={
          <button className="button button-secondary" type="button">
            <UserRoundPlus size={16} /> 添加普通人物
          </button>
        }
      />
      <div className="segmented" role="tablist" aria-label="角色分组">
        {[
          ["active", "活跃角色", "3"],
          ["npc", "普通人物", "4"],
          ["retired", "已退出角色", "1"],
        ].map(([id, label, count]) => (
          <button
            aria-selected={group === id}
            className={group === id ? "selected" : ""}
            key={id}
            onClick={() => setGroup(id)}
            role="tab"
            type="button"
          >
            {label} <span>{count}</span>
          </button>
        ))}
      </div>
      <div className="character-detail-layout">
        <aside className="character-list" aria-label="角色列表">
          {[
            ["陈默", "机械工程师", "陈", "coral"],
            ["林岚", "港务值班员", "林", "green"],
            ["周放", "前任守塔人", "周", "amber"],
          ].map(([name, identity, initial, tone], index) => (
            <button
              className={`character-select ${index === 0 ? "selected" : ""}`}
              key={name}
              type="button"
            >
              <span className={`avatar avatar-${tone}`}>{initial}</span>
              <span>
                <strong>{name}</strong>
                <small>{identity}</small>
              </span>
              <ChevronDown size={14} />
            </button>
          ))}
        </aside>
        <article className="character-sheet">
          <header>
            <div>
              <p className="section-kicker">活跃角色 · 版本 8</p>
              <h2>陈默</h2>
              <p>从外地返回雾港的机械工程师</p>
            </div>
            <StatusBadge tone="confirmed">本轮在场</StatusBadge>
          </header>
          <div className="sheet-grid">
            <section>
              <h3>核心欲望</h3>
              <p>查明父亲十年前在灯塔附近失踪的真相。</p>
            </section>
            <section>
              <h3>当前目标</h3>
              <p>确认灯塔熄灭是否由人为破坏造成。</p>
            </section>
            <section>
              <h3>当前位置与状态</h3>
              <p>灯塔一层 · 紧张但专注 · 携带铜钥匙</p>
            </section>
            <section>
              <h3>重要关系</h3>
              <p>信任林岚，但怀疑她隐瞒了港务记录。</p>
            </section>
          </div>
          <section className="known-facts">
            <h3>已知事实</h3>
            <ul>
              <li>
                <LockKeyhole size={14} /> 父亲十年前在灯塔附近失踪
              </li>
              <li>
                <LockKeyhole size={14} /> 灯塔从未在夜间无故熄灭
              </li>
              <li>
                <LockKeyhole size={14} /> 林岚保留了一份未归档的值班表
              </li>
            </ul>
          </section>
        </article>
      </div>
    </div>
  );
}

export function WorldView() {
  const [saved, setSaved] = useState(true);
  return (
    <div className="page world-page">
      <PageHeader
        eyebrow={saved ? "所有更改已保存" : "存在未保存更改"}
        title="世界设定"
        action={
          <button
            className="icon-button"
            disabled={saved}
            onClick={() => setSaved(true)}
            title="保存世界设定"
            type="button"
          >
            <Save size={17} />
            <span className="sr-only">保存世界设定</span>
          </button>
        }
      />
      <div className="document-layout">
        <nav className="document-outline" aria-label="世界文档">
          {["创作方向", "世界规则", "地点", "历史", "组织", "研究资料"].map(
            (item, index) => (
              <button
                className={index === 1 ? "selected" : ""}
                key={item}
                type="button"
              >
                {item}
              </button>
            ),
          )}
        </nav>
        <article className="world-editor">
          <p className="section-kicker">world.md / 世界规则</p>
          <input
            aria-label="文档标题"
            className="document-title"
            defaultValue="雾港运行规则"
            onChange={() => setSaved(false)}
          />
          <div className="editor-rule" />
          <h2>灯塔与夜航</h2>
          <textarea
            aria-label="世界规则正文"
            defaultValue={
              "灯塔控制雾港的夜间航路。暴风雨期间，所有进入近港航道的船只必须依赖灯塔主光源或港务所的备用航标。\n\n灯塔主光源由独立机械装置驱动，正常情况下不会因港区停电而熄灭。"
            }
            onChange={() => setSaved(false)}
          />
        </article>
      </div>
    </div>
  );
}

export function EventsView() {
  return (
    <div className="page">
      <PageHeader
        eyebrow="12 个已确认事件"
        title="事件历史"
        action={
          <div className="toolbar">
            <button className="icon-button" title="搜索事件" type="button">
              <Search size={17} />
              <span className="sr-only">搜索事件</span>
            </button>
            <button className="icon-button" title="筛选事件" type="button">
              <Filter size={17} />
              <span className="sr-only">筛选事件</span>
            </button>
          </div>
        }
      />
      <div className="timeline">
        {[
          [
            "012",
            "22:12",
            "陈默在灯芯槽中发现新鲜刮痕",
            "陈默",
            "灯塔一层",
          ],
          [
            "011",
            "22:04",
            "林岚启动港务所备用航标",
            "林岚",
            "港务所",
          ],
          [
            "010",
            "21:58",
            "海燕号报告能见度降至三百米",
            "林岚",
            "近港航道",
          ],
          [
            "009",
            "21:51",
            "周放离开酒馆前往旧码头",
            "周放",
            "旧码头",
          ],
        ].map(([sequence, time, title, participant, location]) => (
          <article className="timeline-event" key={sequence}>
            <div className="timeline-marker">
              <span>{sequence}</span>
            </div>
            <div className="timeline-content">
              <header>
                <span>{time}</span>
                <StatusBadge tone="confirmed">已确认</StatusBadge>
              </header>
              <h2>{title}</h2>
              <p>
                {participant} · {location}
              </p>
            </div>
            <button className="icon-button" title="事件操作" type="button">
              <MoreHorizontal size={17} />
              <span className="sr-only">事件操作</span>
            </button>
          </article>
        ))}
      </div>
    </div>
  );
}

export function ManuscriptView() {
  return (
    <div className="page manuscript-page">
      <PageHeader
        eyebrow="第 3 章 / 场景 012"
        title="章节正文"
        action={
          <button className="button button-secondary" type="button">
            <Sparkles size={16} /> 从事件生成
          </button>
        }
      />
      <div className="manuscript-layout">
        <aside className="scene-list">
          <div className="scene-list-heading">
            <strong>章节与场景</strong>
            <button className="icon-button" title="新建场景" type="button">
              <Plus size={16} />
              <span className="sr-only">新建场景</span>
            </button>
          </div>
          <p className="chapter-label">第三章 · 风暴线</p>
          {[
            ["010", "海燕号进入航道"],
            ["011", "备用航标"],
            ["012", "灯芯槽的刮痕"],
          ].map(([id, name], index) => (
            <button
              className={`scene-item ${index === 2 ? "selected" : ""}`}
              key={id}
              type="button"
            >
              <span>{id}</span>
              {name}
            </button>
          ))}
        </aside>
        <article className="manuscript-editor">
          <input
            aria-label="场景标题"
            className="manuscript-title"
            defaultValue="灯芯槽的刮痕"
          />
          <div className="prose" contentEditable suppressContentEditableWarning>
            <p>
              风把雨水从门缝里推了进来。陈默蹲在熄灭的灯座旁，铜钥匙硌着掌心，像一小块没有温度的骨头。
            </p>
            <p>
              他卸下底板。螺丝很紧，但金属边缘有一道不属于旧锈的亮色。刮痕从灯芯槽一直延伸到暗格，末端还沾着细小的黑色纤维。
            </p>
            <p>
              楼下传来门轴转动的声音。陈默没有出声，只把底板轻轻放回原位。
            </p>
          </div>
        </article>
        <aside className="source-panel">
          <p className="section-kicker">事实来源</p>
          <h2>Event 000012</h2>
          <div className="source-status">
            <ShieldCheck size={16} />
            <span>未发现事实差异</span>
          </div>
          <dl>
            <div>
              <dt>参与者</dt>
              <dd>陈默</dd>
            </div>
            <div>
              <dt>地点</dt>
              <dd>灯塔一层</dd>
            </div>
            <div>
              <dt>确认事实</dt>
              <dd>灯芯槽有新鲜刮痕</dd>
            </div>
          </dl>
        </aside>
      </div>
    </div>
  );
}

export function ModelsView() {
  return (
    <div className="page">
      <PageHeader
        eyebrow="5 个任务档案"
        title="模型中心"
        action={
          <button className="button button-secondary" type="button">
            <Plus size={16} /> 添加 Provider
          </button>
        }
      />
      <section className="model-summary">
        <div>
          <p className="section-kicker">本地推理</p>
          <h2>Qwen3 8B · 已加载</h2>
          <p>Metal · 6.2 GB · 28.4 tok/s</p>
        </div>
        <StatusBadge tone="confirmed">运行中</StatusBadge>
      </section>
      <section className="profile-table" aria-labelledby="profiles-heading">
        <div className="panel-heading">
          <div>
            <p className="section-kicker">任务路由</p>
            <h2 id="profiles-heading">模型档案</h2>
          </div>
        </div>
        <div className="table-header">
          <span>档案</span>
          <span>模型</span>
          <span>Provider</span>
          <span>状态</span>
        </div>
        {[
          ["Character", "Qwen3 8B", "本地模型", "可用"],
          ["Resolver", "Claude Sonnet 4", "Anthropic", "可用"],
          ["Editor", "GPT-5 mini", "OpenAI", "可用"],
          ["Writer", "Claude Sonnet 4", "Anthropic", "可用"],
          ["Embedding", "bge-m3", "本地模型", "可用"],
        ].map(([profile, model, provider, state]) => (
          <button className="table-row" key={profile} type="button">
            <strong>{profile}</strong>
            <span>{model}</span>
            <span>{provider}</span>
            <StatusBadge tone="confirmed">{state}</StatusBadge>
          </button>
        ))}
      </section>
    </div>
  );
}

export function SettingsView() {
  const [evidence, setEvidence] = useState(true);
  const [autosave, setAutosave] = useState(true);
  return (
    <div className="page settings-page">
      <PageHeader eyebrow="雾港" title="项目设置" />
      <div className="settings-layout">
        <nav aria-label="设置分组">
          {["基本信息", "演化规则", "模型档案", "检索与证据", "数据与导出"].map(
            (item, index) => (
              <button
                className={index === 0 ? "selected" : ""}
                key={item}
                type="button"
              >
                {item}
              </button>
            ),
          )}
        </nav>
        <form className="settings-form">
          <section>
            <h2>基本信息</h2>
            <label>
              项目名称
              <input defaultValue="雾港" />
            </label>
            <label>
              项目位置
              <div className="path-input">
                <input
                  defaultValue="/Users/hank/Stories/fog-harbor"
                  readOnly
                />
                <button className="button button-secondary" type="button">
                  选择
                </button>
              </div>
            </label>
          </section>
          <section>
            <h2>保存与证据</h2>
            <label className="toggle-row">
              <span>
                <strong>自动保存运行数据</strong>
                <small>候选结果仍需确认后才会写入正式状态</small>
              </span>
              <input
                checked={autosave}
                onChange={(event) => setAutosave(event.target.checked)}
                type="checkbox"
              />
            </label>
            <label className="toggle-row">
              <span>
                <strong>显示检索证据</strong>
                <small>在审核与正文侧栏展示注入片段来源</small>
              </span>
              <input
                checked={evidence}
                onChange={(event) => setEvidence(event.target.checked)}
                type="checkbox"
              />
            </label>
          </section>
        </form>
      </div>
    </div>
  );
}
