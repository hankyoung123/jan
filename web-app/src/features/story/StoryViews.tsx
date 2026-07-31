import type { components } from '@story-engine/contracts'
import { Link } from '@tanstack/react-router'
import {
  ArrowRight,
  Check,
  CheckCircle2,
  FileText,
  LockKeyhole,
  RotateCcw,
  Save,
  Send,
  ShieldCheck,
  Sparkles,
  Trash2,
  TriangleAlert,
  UsersRound,
} from 'lucide-react'
import { useState, type ReactNode } from 'react'

import { route } from '@/constants/routes'
import { engineRequest } from './engine'

type SubmissionPackage = components['schemas']['SubmissionPackage']
type ProjectSnapshot = components['schemas']['ProjectSnapshot']
type TurnCandidate = components['schemas']['TurnCandidate']
type CommitResult = components['schemas']['CommitResult']

const primaryButton =
  'inline-flex h-9 items-center justify-center gap-2 rounded-md bg-primary px-3 text-sm font-medium text-primary-foreground shadow-sm transition hover:brightness-95 disabled:pointer-events-none disabled:opacity-50'
const secondaryButton =
  'inline-flex h-9 items-center justify-center gap-2 rounded-md border bg-background px-3 text-sm font-medium transition hover:bg-accent disabled:pointer-events-none disabled:opacity-50'
const inputClass =
  'min-h-9 w-full rounded-md border bg-background px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-ring/40'

function StoryPage({ children }: { children: ReactNode }) {
  return (
    <main className="h-svh overflow-y-auto bg-neutral-50 px-5 pb-12 pt-14 dark:bg-background md:px-8">
      <div className="mx-auto w-full max-w-6xl">{children}</div>
    </main>
  )
}

function PageHeader({
  eyebrow,
  title,
  action,
}: {
  eyebrow: string
  title: string
  action?: ReactNode
}) {
  return (
    <header className="mb-6 flex min-h-14 flex-wrap items-end justify-between gap-4 border-b pb-5">
      <div>
        <p className="mb-1 text-xs font-medium text-muted-foreground">
          {eyebrow}
        </p>
        <h1 className="font-studio text-2xl font-medium">{title}</h1>
      </div>
      {action}
    </header>
  )
}

function StatusPill({
  children,
  tone = 'neutral',
}: {
  children: ReactNode
  tone?: 'success' | 'warning' | 'danger' | 'neutral'
}) {
  const tones = {
    success: 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-300',
    warning: 'bg-amber-500/12 text-amber-700 dark:text-amber-300',
    danger: 'bg-destructive/10 text-destructive',
    neutral: 'bg-muted text-muted-foreground',
  }
  return (
    <span className={`inline-flex rounded px-2 py-1 text-xs font-medium ${tones[tone]}`}>
      {children}
    </span>
  )
}

export function WorkbenchView() {
  return (
    <StoryPage>
      <PageHeader
        eyebrow="雾港 / 世界版本 12"
        title="工作台"
        action={
          <Link className={primaryButton} to={route.evolve}>
            推进下一轮 <ArrowRight size={15} />
          </Link>
        }
      />
      <section className="grid gap-6 border bg-background p-6 md:grid-cols-[1fr_auto]">
        <div className="max-w-2xl">
          <p className="mb-2 text-xs font-medium text-muted-foreground">
            当前世界
          </p>
          <h2 className="mb-3 font-studio text-xl font-medium">
            暴风雨前夜，灯塔失去光源
          </h2>
          <p className="leading-6 text-muted-foreground">
            客船“海燕号”将在四十分钟后抵港。陈默已经进入灯塔，林岚仍在港务所尝试恢复备用航标。
          </p>
        </div>
        <dl className="grid grid-cols-3 gap-px self-start overflow-hidden border bg-border text-center">
          {[
            ['时间', '22:18'],
            ['地点', '雾港'],
            ['压力', '客船逼近'],
          ].map(([label, value]) => (
            <div className="min-w-24 bg-background px-3 py-3" key={label}>
              <dt className="text-xs text-muted-foreground">{label}</dt>
              <dd className="mt-1 font-medium">{value}</dd>
            </div>
          ))}
        </dl>
      </section>
      <div className="mt-5 grid gap-5 lg:grid-cols-2">
        <section className="border bg-background">
          <div className="flex items-center justify-between border-b px-5 py-4">
            <div>
              <p className="text-xs text-muted-foreground">待处理</p>
              <h2 className="font-medium">编辑队列</h2>
            </div>
            <span className="grid size-7 place-items-center rounded-full bg-muted text-xs">3</span>
          </div>
          {[
            [TriangleAlert, '确认守塔人的知识边界', 'Editor 标记了 1 项潜在越界', '待检查'],
            [FileText, '场景 012 尚未生成正文', '来源事件 event-000012', '未开始'],
            [Check, '世界索引已重建', '42 个片段均有来源', '完成'],
          ].map(([Icon, title, detail, state], index) => (
            <div className="grid grid-cols-[28px_1fr_auto] items-center gap-3 border-b px-5 py-4 last:border-0" key={String(title)}>
              <Icon className={index === 0 ? 'text-amber-600' : index === 2 ? 'text-emerald-600' : 'text-muted-foreground'} size={16} />
              <div>
                <strong className="text-sm font-medium">{String(title)}</strong>
                <p className="mt-1 text-xs text-muted-foreground">{String(detail)}</p>
              </div>
              <StatusPill tone={index === 0 ? 'warning' : index === 2 ? 'success' : 'neutral'}>{String(state)}</StatusPill>
            </div>
          ))}
        </section>
        <section className="border bg-background">
          <div className="flex items-center justify-between border-b px-5 py-4">
            <div>
              <p className="text-xs text-muted-foreground">在场角色</p>
              <h2 className="font-medium">活跃角色</h2>
            </div>
            <Link className="text-xs font-medium text-primary" to={route.characters}>查看全部</Link>
          </div>
          {[
            ['陈', '陈默', '查明灯塔熄灭原因', '灯塔一层'],
            ['林', '林岚', '让海燕号安全进港', '港务所'],
            ['周', '周放', '避免十年前的记录曝光', '旧码头'],
          ].map(([initial, name, goal, location]) => (
            <div className="grid grid-cols-[34px_1fr_auto] items-center gap-3 border-b px-5 py-4 last:border-0" key={name}>
              <span className="grid size-8 place-items-center rounded-full bg-muted text-xs font-medium">{initial}</span>
              <div><strong className="text-sm font-medium">{name}</strong><p className="mt-1 text-xs text-muted-foreground">目标：{goal}</p></div>
              <span className="text-xs text-muted-foreground">{location}</span>
            </div>
          ))}
        </section>
      </div>
      <section className="mt-5 grid grid-cols-[48px_1fr_auto] items-center gap-4 border bg-background px-5 py-4">
        <span className="grid size-10 place-items-center rounded-full border font-studio">12</span>
        <div><p className="text-xs text-muted-foreground">最近确认事件 · 22:12</p><h2 className="mt-1 font-medium">陈默在灯芯槽中发现新鲜刮痕</h2></div>
        <StatusPill tone="success">已确认</StatusPill>
      </section>
    </StoryPage>
  )
}

const initialPackage: SubmissionPackage = {
  id: 'fog-harbor',
  title: '雾港',
  genre: '悬疑',
  theme: '真相与亲情之间的选择',
  tone: '克制、现实、缓慢积压',
  world_rules: ['灯塔控制港口夜航', '暴风雨时港口必须依赖灯塔或备用航标'],
  public_fact_ids: ['fact:lighthouse-controls-night-navigation', 'fact:storm-requires-navigation-light'],
  characters: [
    { id: 'chen-mo', display_name: '陈默', identity: '从外地返回雾港的机械工程师', core_desire: '找到父亲失踪的真相', current_goal: '查明灯塔熄灭原因', known_fact_ids: ['secret:chen-father-disappearance'], location: '灯塔入口', emotional_state: '紧张但专注', resources: ['铜钥匙'] },
    { id: 'lin-lan', display_name: '林岚', identity: '雾港港务所值班员', core_desire: '保护进港船只和港务所声誉', current_goal: '让客船安全进入雾港', known_fact_ids: ['secret:lin-unfiled-duty-roster'], location: '港务所', emotional_state: '警觉', resources: ['港务电台'] },
  ],
  initial_time: '暴风雨前夜',
  initial_location: '雾港',
  initial_incident: '灯塔突然熄灭',
  pressures: ['客船即将进入近港航道'],
}

export function SubmissionView() {
  const [submission, setSubmission] = useState(initialPackage)
  const [project, setProject] = useState<ProjectSnapshot | null>(null)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function finalize() {
    setSaving(true)
    setError(null)
    try {
      setProject(await engineRequest<ProjectSnapshot>('/submissions/finalize', { method: 'POST', body: JSON.stringify(submission) }))
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '投稿创建失败')
    } finally {
      setSaving(false)
    }
  }

  return (
    <StoryPage>
      <PageHeader eyebrow="初始设定包" title="投稿" action={<button className={primaryButton} disabled={saving || project !== null} onClick={() => void finalize()} type="button">{project ? <Check size={15} /> : <Send size={15} />}{project ? '项目已创建' : saving ? '正在创建' : '创建雾港项目'}</button>} />
      <div className="grid min-h-[560px] border bg-background lg:grid-cols-[1.1fr_.9fr]">
        <section className="border-b p-6 lg:border-b-0 lg:border-r">
          <p className="mb-1 text-xs text-muted-foreground">创作讨论</p><h2 className="mb-5 font-medium">创作方向</h2>
          <div className="grid gap-4">
            {(['genre', 'theme'] as const).map((field) => <label className="grid gap-2 text-xs font-medium text-muted-foreground" key={field}>{field === 'genre' ? '类型' : '主题'}<input className={inputClass} value={submission[field]} onChange={(event) => setSubmission({ ...submission, [field]: event.target.value })} /></label>)}
            <label className="grid gap-2 text-xs font-medium text-muted-foreground">叙事气质<textarea className={`${inputClass} min-h-24 resize-y`} value={submission.tone} onChange={(event) => setSubmission({ ...submission, tone: event.target.value })} /></label>
            <label className="grid gap-2 text-xs font-medium text-muted-foreground">起始事件<textarea className={`${inputClass} min-h-24 resize-y`} value={submission.initial_incident} onChange={(event) => setSubmission({ ...submission, initial_incident: event.target.value })} /></label>
          </div>
          {error && <p className="mt-4 bg-destructive/10 p-3 text-sm text-destructive" role="alert">{error}</p>}
        </section>
        <aside className="bg-muted/30 p-6"><p className="mb-1 text-xs text-muted-foreground">Editor 整理结果</p><h2 className="mb-5 font-studio text-2xl">{submission.title}</h2><dl className="grid grid-cols-3 gap-px border bg-border text-xs">{[['时间', submission.initial_time], ['地点', submission.initial_location], ['压力', submission.pressures[0]]].map(([label, value]) => <div className="bg-background p-3" key={label}><dt className="text-muted-foreground">{label}</dt><dd className="mt-1 font-medium">{value}</dd></div>)}</dl><section className="mt-6 border-t pt-5"><h3 className="mb-3 text-xs font-medium text-muted-foreground">世界规则</h3><ul className="list-disc space-y-2 pl-5 text-sm">{submission.world_rules.map((rule) => <li key={rule}>{rule}</li>)}</ul></section><section className="mt-6 border-t pt-5"><h3 className="mb-3 flex items-center gap-2 text-xs font-medium text-muted-foreground"><UsersRound size={14} /> 初始角色</h3>{submission.characters.map((character) => <article className="border-b py-3 last:border-0" key={character.id}><strong className="text-sm">{character.display_name}</strong><p className="mt-1 text-sm text-muted-foreground">{character.identity}</p><small className="text-xs text-muted-foreground">{character.current_goal}</small></article>)}</section>{project && <p className="mt-5 flex items-center gap-2 bg-emerald-500/10 p-3 text-sm text-emerald-700" role="status"><Check size={15} /> 世界版本 {project.world.version}，可进入第一轮</p>}</aside>
      </div>
    </StoryPage>
  )
}

export function EvolutionView() {
  const [candidate, setCandidate] = useState<TurnCandidate | null>(null)
  const [committed, setCommitted] = useState<CommitResult | null>(null)
  const [revision, setRevision] = useState('让结果更克制')
  const [workingAction, setWorkingAction] = useState<
    'generate' | 'revise' | 'discard' | 'confirm' | null
  >(null)
  const [error, setError] = useState<string | null>(null)
  const working = workingAction !== null

  async function act<Response>(
    action: NonNullable<typeof workingAction>,
    request: () => Promise<Response>
  ) {
    setWorkingAction(action)
    setError(null)
    try {
      return await request()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '引擎请求失败')
      return null
    } finally {
      setWorkingAction(null)
    }
  }
  async function generate() { const value = await act('generate', () => engineRequest<TurnCandidate>('/projects/fog-harbor/turns/generate', { method: 'POST', body: JSON.stringify({ participant_ids: ['chen-mo', 'lin-lan'] }) })); if (value) { setCandidate(value); setCommitted(null) } }
  async function revise() { if (!candidate) return; const value = await act('revise', () => engineRequest<TurnCandidate>(`/projects/fog-harbor/turns/${candidate.id}/request-revision`, { method: 'POST', body: JSON.stringify({ instruction: revision }) })); if (value) setCandidate(value) }
  async function discard() { if (!candidate) return; const value = await act('discard', () => engineRequest<TurnCandidate>(`/projects/fog-harbor/turns/${candidate.id}/discard`, { method: 'POST' })); if (value) setCandidate(value) }
  async function confirm() { if (!candidate) return; const value = await act('confirm', () => engineRequest<CommitResult>(`/projects/fog-harbor/turns/${candidate.id}/confirm`, { method: 'POST' })); if (value) setCommitted(value) }

  return (
    <StoryPage>
      <PageHeader eyebrow={`雾港 / 世界版本 ${candidate?.base_world_version ?? 0}`} title="推进故事" action={<button aria-busy={workingAction === 'generate'} className={secondaryButton} disabled={working} onClick={() => void generate()} type="button"><Sparkles size={15} /> {workingAction === 'generate' ? '正在生成' : candidate ? '重新生成角色行动' : '生成角色行动'}</button>} />
      {!candidate ? <section className="border bg-background p-8"><p className="text-xs text-muted-foreground">当前局面</p><h2 className="my-3 font-studio text-xl">灯塔熄灭，客船正在接近雾港</h2><p className="text-muted-foreground">陈默与林岚将依据各自的私有知识独立行动。</p></section> : <><section className="border bg-background"><div className="flex items-center justify-between border-b px-5 py-4"><div><p className="text-xs text-muted-foreground">私有上下文已隔离</p><h2 className="font-medium">角色行动</h2></div><StatusPill tone="success">{candidate.intents.length} / {candidate.intents.length} 完成</StatusPill></div>{candidate.intents.map((intent) => <div className="grid gap-3 border-b px-5 py-4 md:grid-cols-[22px_160px_1fr_auto] md:items-center" key={intent.character_id}><CheckCircle2 className="text-emerald-600" size={17} /><div><strong className="text-sm">{intent.character_id === 'chen-mo' ? '陈默' : '林岚'}</strong><p className="text-xs text-muted-foreground">{intent.goal}</p></div><p className="text-sm text-muted-foreground">{intent.action}</p><StatusPill tone="success">已完成</StatusPill></div>)}</section><section className="mt-5 grid gap-5 border bg-background p-5 md:grid-cols-[1fr_280px]"><div><p className="text-xs text-muted-foreground">统一结算</p><h2 className="my-2 font-studio text-lg">{candidate.outcome.summary}</h2><p className="text-sm text-muted-foreground">{candidate.outcome.public_results.join(' · ')}</p></div><div className="border-l pl-5"><p className="flex items-center gap-2 text-sm font-medium"><ShieldCheck className={candidate.review?.passed ? 'text-emerald-600' : 'text-destructive'} size={17} /> Editor {candidate.review?.passed ? '检查通过' : '要求修订'}</p><p className="mt-2 text-xs text-muted-foreground">{candidate.review?.summary}</p></div></section>{!committed && candidate.status !== 'discarded' && <section aria-busy={working} className="mt-5 grid gap-3 border bg-background p-4 md:grid-cols-[1fr_auto_auto_auto]"><input aria-label="修改要求" className={inputClass} value={revision} onChange={(event) => setRevision(event.target.value)} /><button className={secondaryButton} disabled={working || !revision.trim()} onClick={() => void revise()} type="button"><RotateCcw size={15} /> {workingAction === 'revise' ? '正在修改' : '要求修改'}</button><button aria-label={workingAction === 'discard' ? '正在放弃本轮' : '放弃本轮'} className={secondaryButton} disabled={working} onClick={() => void discard()} title="放弃本轮" type="button"><Trash2 size={15} /></button><button className={primaryButton} disabled={working || !candidate.review?.passed} onClick={() => void confirm()} type="button"><Check size={15} /> {workingAction === 'confirm' ? '正在提交' : '确认本轮'}</button></section>}{committed && <p className="mt-5 flex items-center gap-2 bg-emerald-500/10 p-3 text-sm text-emerald-700" role="status"><CheckCircle2 size={16} /> {committed.event.id} 已写入正式 Markdown</p>}{candidate.status === 'discarded' && <p className="mt-5 bg-muted p-3 text-sm text-muted-foreground" role="status">本轮已放弃，正式状态未改变。</p>}</>}
      {error && <p className="mt-4 bg-destructive/10 p-3 text-sm text-destructive" role="alert">{error}</p>}
    </StoryPage>
  )
}

export function CharactersView() {
  const [selected, setSelected] = useState(0)
  const characters = [
    { name: '陈默', identity: '机械工程师 · 活跃角色', desire: '找到父亲失踪的真相', goal: '查明灯塔熄灭原因', location: '灯塔一层', emotion: '紧张但专注', facts: ['父亲失踪前留下了一把铜钥匙', '灯塔机械装置通常不受港区停电影响'] },
    { name: '林岚', identity: '港务所值班员 · 活跃角色', desire: '保护进港船只和港务所声誉', goal: '让海燕号安全进入雾港', location: '港务所', emotion: '警觉', facts: ['值班表中有一条未归档改动', '备用航标剩余电量不足'] },
  ]
  const current = characters[selected]
  return <StoryPage><PageHeader eyebrow="2 个活跃角色" title="角色" /><div className="grid min-h-[540px] border bg-background md:grid-cols-[230px_1fr]"><nav className="border-b p-2 md:border-b-0 md:border-r">{characters.map((character, index) => <button className={`mb-1 w-full rounded-md p-3 text-left ${selected === index ? 'bg-accent' : 'hover:bg-accent/60'}`} key={character.name} onClick={() => setSelected(index)} type="button"><strong className="text-sm">{character.name}</strong><p className="mt-1 text-xs text-muted-foreground">{character.identity}</p></button>)}</nav><article className="p-7"><p className="text-xs text-muted-foreground">角色私有档案</p><h2 className="mt-1 font-studio text-2xl">{current.name}</h2><p className="mt-1 text-sm text-muted-foreground">{current.identity}</p><div className="mt-6 grid border md:grid-cols-2">{[['核心欲望', current.desire], ['当前目标', current.goal], ['当前位置', current.location], ['情绪状态', current.emotion]].map(([label, value]) => <section className="border-b p-5 odd:md:border-r" key={label}><h3 className="text-xs text-muted-foreground">{label}</h3><p className="mt-2 text-sm">{value}</p></section>)}</div><section className="mt-6"><h3 className="mb-3 flex items-center gap-2 text-xs text-muted-foreground"><LockKeyhole size={14} /> 已知事实</h3><ul className="space-y-2">{current.facts.map((fact) => <li className="border-l-2 border-primary/40 pl-3 text-sm text-muted-foreground" key={fact}>{fact}</li>)}</ul></section></article></div></StoryPage>
}

export function WorldView() {
  const [saved, setSaved] = useState(true)
  return <StoryPage><PageHeader eyebrow={saved ? '所有更改已保存' : '存在未保存更改'} title="世界设定" action={<button className={secondaryButton} disabled={saved} onClick={() => setSaved(true)} title="保存世界设定" type="button"><Save size={15} /></button>} /><div className="grid min-h-[560px] border bg-background md:grid-cols-[200px_1fr]"><nav className="flex overflow-x-auto border-b p-2 md:block md:border-b-0 md:border-r">{['创作方向', '世界规则', '地点', '历史', '组织', '研究资料'].map((item, index) => <button className={`min-w-max rounded-md px-3 py-2 text-left text-sm md:mb-1 md:w-full ${index === 1 ? 'bg-accent font-medium' : 'text-muted-foreground hover:bg-accent/60'}`} key={item} type="button">{item}</button>)}</nav><article className="max-w-3xl p-8"><p className="text-xs text-muted-foreground">world.md / 世界规则</p><input aria-label="文档标题" className="mt-2 w-full bg-transparent font-studio text-2xl outline-none" defaultValue="雾港运行规则" onChange={() => setSaved(false)} /><div className="my-6 h-0.5 w-10 bg-primary" /><h2 className="mb-3 font-medium">灯塔与夜航</h2><textarea aria-label="世界规则正文" className="min-h-80 w-full resize-none bg-transparent text-base leading-7 outline-none" defaultValue={'灯塔控制雾港的夜间航路。暴风雨期间，所有进入近港航道的船只必须依赖灯塔主光源或港务所的备用航标。\n\n灯塔主光源由独立机械装置驱动，正常情况下不会因港区停电而熄灭。'} onChange={() => setSaved(false)} /></article></div></StoryPage>
}

export function EventsView() {
  const events = [['012', '22:12', '陈默在灯芯槽中发现新鲜刮痕', '陈默 · 灯塔一层'], ['011', '22:04', '林岚启动港务所备用航标', '林岚 · 港务所'], ['010', '21:58', '海燕号报告能见度降至三百米', '林岚 · 近港航道'], ['009', '21:51', '周放离开酒馆前往旧码头', '周放 · 旧码头']]
  return <StoryPage><PageHeader eyebrow="12 个已确认事件" title="事件历史" /><div className="max-w-4xl">{events.map(([sequence, time, title, detail], index) => <article className="relative grid grid-cols-[48px_1fr] gap-4 pb-7" key={sequence}>{index < events.length - 1 && <span className="absolute bottom-0 left-5 top-10 w-px bg-border" />}<span className="z-10 grid size-10 place-items-center rounded-full border bg-background font-studio text-xs">{sequence}</span><div className="border-b pb-6"><div className="mb-2 flex items-center gap-3 text-xs text-muted-foreground"><span>{time}</span><StatusPill tone="success">已确认</StatusPill></div><h2 className="font-medium">{title}</h2><p className="mt-2 text-sm text-muted-foreground">{detail}</p></div></article>)}</div></StoryPage>
}

export function ManuscriptView() {
  return <StoryPage><PageHeader eyebrow="第 3 章 / 场景 012" title="章节正文" action={<button className={secondaryButton} type="button"><Sparkles size={15} /> 从事件生成</button>} /><div className="grid min-h-[620px] border bg-background lg:grid-cols-[190px_1fr_230px]"><aside className="hidden border-r p-3 lg:block"><strong className="px-2 text-sm">章节与场景</strong><p className="mb-2 mt-5 px-2 text-xs text-muted-foreground">第三章 · 风暴线</p>{[['010', '海燕号进入航道'], ['011', '备用航标'], ['012', '灯芯槽的刮痕']].map(([id, title], index) => <button className={`mb-1 grid w-full grid-cols-[26px_1fr] rounded-md px-2 py-2 text-left text-sm ${index === 2 ? 'bg-accent' : 'text-muted-foreground hover:bg-accent/60'}`} key={id} type="button"><span className="font-studio">{id}</span>{title}</button>)}</aside><article className="p-8 md:p-12"><input aria-label="场景标题" className="mb-7 w-full bg-transparent font-studio text-2xl outline-none" defaultValue="灯芯槽的刮痕" /><div className="space-y-5 text-base leading-8" contentEditable suppressContentEditableWarning><p>风把雨水从门缝里推了进来。陈默蹲在熄灭的灯座旁，铜钥匙硌着掌心，像一小块没有温度的骨头。</p><p>他卸下底板。螺丝很紧，但金属边缘有一道不属于旧锈的亮色。刮痕从灯芯槽一直延伸到暗格，末端还沾着细小的黑色纤维。</p><p>楼下传来门轴转动的声音。陈默没有出声，只把底板轻轻放回原位。</p></div></article><aside className="hidden border-l p-5 lg:block"><p className="text-xs text-muted-foreground">事实来源</p><h2 className="mt-1 font-medium">Event 000012</h2><p className="my-4 flex items-center gap-2 bg-emerald-500/10 p-3 text-xs text-emerald-700"><ShieldCheck size={15} /> 未发现事实差异</p><dl className="text-sm">{[['参与者', '陈默'], ['地点', '灯塔一层'], ['确认事实', '灯芯槽有新鲜刮痕']].map(([label, value]) => <div className="border-b py-3" key={label}><dt className="text-xs text-muted-foreground">{label}</dt><dd className="mt-1">{value}</dd></div>)}</dl></aside></div></StoryPage>
}
