import type { components } from "@story-engine/contracts";
import {
  Check,
  CheckCircle2,
  RotateCcw,
  Sparkles,
  Trash2,
} from "lucide-react";
import { useState } from "react";

import { engineRequest } from "../api/client";
import { useEngineEvents } from "./useEngineEvents";

type TurnCandidate = components["schemas"]["TurnCandidate"];
type CommitResult = components["schemas"]["CommitResult"];

const steps = ["当前局面", "角色行动", "世界结算", "编辑检查", "用户确认"];
const characterNames: Record<string, string> = {
  "chen-mo": "陈默",
  "lin-lan": "林岚",
};

export function EvolutionWorkspace() {
  const stream = useEngineEvents("fog-harbor");
  const [candidate, setCandidate] = useState<TurnCandidate | null>(null);
  const [committed, setCommitted] = useState<CommitResult | null>(null);
  const [revision, setRevision] = useState("让结果更克制");
  const [state, setState] = useState<"idle" | "working" | "error">("idle");
  const [error, setError] = useState<string | null>(null);

  async function act<Response>(request: () => Promise<Response>) {
    setState("working");
    setError(null);
    try {
      const response = await request();
      setState("idle");
      return response;
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "引擎请求失败");
      setState("error");
      return null;
    }
  }

  async function generate() {
    const response = await act(() =>
      engineRequest<TurnCandidate>("/projects/fog-harbor/turns/generate", {
        method: "POST",
        body: JSON.stringify({ participant_ids: ["chen-mo", "lin-lan"] }),
      }),
    );
    if (response) {
      setCandidate(response);
      setCommitted(null);
    }
  }

  async function confirm() {
    if (!candidate) return;
    const response = await act(() =>
      engineRequest<CommitResult>(
        `/projects/fog-harbor/turns/${candidate.id}/confirm`,
        { method: "POST" },
      ),
    );
    if (response) setCommitted(response);
  }

  async function requestRevision() {
    if (!candidate) return;
    const response = await act(() =>
      engineRequest<TurnCandidate>(
        `/projects/fog-harbor/turns/${candidate.id}/request-revision`,
        { method: "POST", body: JSON.stringify({ instruction: revision }) },
      ),
    );
    if (response) setCandidate(response);
  }

  async function discard() {
    if (!candidate) return;
    const response = await act(() =>
      engineRequest<TurnCandidate>(
        `/projects/fog-harbor/turns/${candidate.id}/discard`,
        { method: "POST" },
      ),
    );
    if (response) setCandidate(response);
  }

  const streamedStep = {
    "turn.started": 1,
    "character.intent.started": 1,
    "character.intent.delta": 1,
    "character.intent.completed": 1,
    "resolver.started": 2,
    "resolver.completed": 2,
    "review.started": 3,
    "review.completed": 4,
    "engine.status": 0,
    "turn.failed": 0,
    "turn.cancelled": 0,
  }[stream.latest?.type ?? "engine.status"];
  const currentStep = committed ? 5 : candidate ? 4 : streamedStep;
  const disabled = state === "working";

  return (
    <div className="page evolution-page">
      <header className="page-header">
        <div>
          <p className="eyebrow">雾港 / 世界版本 {candidate?.base_world_version ?? 0}</p>
          <h1>推进故事</h1>
        </div>
        <button className="button button-secondary" disabled={disabled} onClick={() => void generate()} type="button">
          <Sparkles size={16} /> {candidate ? "重新生成角色行动" : "生成角色行动"}
        </button>
      </header>

      <ol className="turn-steps" aria-label="回合进度">
        {steps.map((step, index) => (
          <li className={index === currentStep ? "current" : index < currentStep ? "done" : ""} key={step}>
            <span>{index < currentStep ? <Check size={14} /> : index + 1}</span>{step}
          </li>
        ))}
      </ol>

      {!candidate ? (
        <section className="evolution-empty">
          <p className="section-kicker">当前局面</p>
          <h2>灯塔熄灭，客船正在接近雾港</h2>
          <p>陈默与林岚将依据各自的私有知识独立行动。</p>
          <small className="stream-status" role="status">
            {stream.connected ? "实时事件已连接" : "等待实时事件连接"}
          </small>
        </section>
      ) : (
        <>
          <section className="agent-work">
            <div className="panel-heading">
              <div><p className="section-kicker">私有上下文已隔离</p><h2>角色行动</h2></div>
              <span className="quiet-meta">{candidate.intents.length} / {candidate.intents.length} 完成</span>
            </div>
            {candidate.intents.map((intent) => (
              <div className="agent-row complete" key={intent.character_id}>
                <CheckCircle2 size={18} />
                <div className="agent-name"><strong>{characterNames[intent.character_id] ?? intent.character_id}</strong><span>{intent.goal}</span></div>
                <p>{intent.action}</p>
                <span className="status-badge status-confirmed">已完成</span>
              </div>
            ))}
          </section>

          <section className="outcome-review">
            <div><p className="section-kicker">统一结算</p><h2>{candidate.outcome.summary}</h2><p>{candidate.outcome.public_results.join(" · ")}</p></div>
            <div className={candidate.review?.passed ? "review-pass" : "review-blocked"}>
              <CheckCircle2 size={18} />
              <span><strong>Editor {candidate.review?.passed ? "检查通过" : "要求修订"}</strong><small>{candidate.review?.summary}</small></span>
            </div>
          </section>

          {!committed && candidate.status !== "discarded" && (
            <section className="turn-actions">
              <label>修改要求<input value={revision} onChange={(event) => setRevision(event.target.value)} /></label>
              <button className="button button-secondary" disabled={disabled || !revision.trim()} onClick={() => void requestRevision()} type="button"><RotateCcw size={16} /> 要求修改</button>
              <button className="icon-button danger-button" disabled={disabled} onClick={() => void discard()} title="放弃本轮" type="button"><Trash2 size={17} /><span className="sr-only">放弃本轮</span></button>
              <button className="button button-primary" disabled={disabled || !candidate.review?.passed} onClick={() => void confirm()} type="button"><Check size={16} /> 确认本轮</button>
            </section>
          )}
          {committed && <p className="commit-notice" role="status"><CheckCircle2 size={17} /> {committed.event.id} 已写入正式 Markdown</p>}
          {candidate.status === "discarded" && <p className="discard-notice" role="status">本轮已放弃，正式状态未改变。</p>}
        </>
      )}
      {error && <p className="form-error" role="alert">{error}</p>}
    </div>
  );
}
