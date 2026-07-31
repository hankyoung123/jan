import type { components } from "@story-engine/contracts";
import { Check, MessageSquareText, Send, UsersRound } from "lucide-react";
import { useState } from "react";

import { engineRequest } from "../api/client";

type SubmissionPackage = components["schemas"]["SubmissionPackage"];
type ProjectSnapshot = components["schemas"]["ProjectSnapshot"];

const initialPackage: SubmissionPackage = {
  id: "fog-harbor",
  title: "雾港",
  genre: "悬疑",
  theme: "真相与亲情之间的选择",
  tone: "克制、现实、缓慢积压",
  world_rules: [
    "灯塔控制港口夜航",
    "暴风雨时港口必须依赖灯塔或备用航标",
  ],
  public_fact_ids: [
    "fact:lighthouse-controls-night-navigation",
    "fact:storm-requires-navigation-light",
  ],
  characters: [
    {
      id: "chen-mo",
      display_name: "陈默",
      identity: "从外地返回雾港的机械工程师",
      core_desire: "找到父亲失踪的真相",
      current_goal: "查明灯塔熄灭原因",
      known_fact_ids: ["secret:chen-father-disappearance"],
      location: "灯塔入口",
      emotional_state: "紧张但专注",
      resources: ["铜钥匙"],
    },
    {
      id: "lin-lan",
      display_name: "林岚",
      identity: "雾港港务所值班员",
      core_desire: "保护进港船只和港务所声誉",
      current_goal: "让客船安全进入雾港",
      known_fact_ids: ["secret:lin-unfiled-duty-roster"],
      location: "港务所",
      emotional_state: "警觉",
      resources: ["港务电台"],
    },
  ],
  initial_time: "暴风雨前夜",
  initial_location: "雾港",
  initial_incident: "灯塔突然熄灭",
  pressures: ["客船即将进入近港航道"],
};

export function SubmissionView() {
  const [submission, setSubmission] = useState(initialPackage);
  const [project, setProject] = useState<ProjectSnapshot | null>(null);
  const [state, setState] = useState<"idle" | "saving" | "error">("idle");
  const [error, setError] = useState<string | null>(null);

  async function finalize() {
    setState("saving");
    setError(null);
    try {
      const snapshot = await engineRequest<ProjectSnapshot>(
        "/submissions/finalize",
        { method: "POST", body: JSON.stringify(submission) },
      );
      setProject(snapshot);
      setState("idle");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "投稿创建失败");
      setState("error");
    }
  }

  return (
    <div className="page submission-page">
      <header className="page-header">
        <div>
          <p className="eyebrow">初始设定包</p>
          <h1>投稿</h1>
        </div>
        <button
          className="button button-primary"
          disabled={state === "saving" || project !== null}
          onClick={() => void finalize()}
          type="button"
        >
          {project ? <Check size={16} /> : <Send size={16} />}
          {project ? "项目已创建" : state === "saving" ? "正在创建" : "创建雾港项目"}
        </button>
      </header>

      <div className="submission-layout">
        <section className="submission-dialogue" aria-labelledby="direction-title">
          <div className="panel-heading">
            <div>
              <p className="section-kicker">创作讨论</p>
              <h2 id="direction-title">创作方向</h2>
            </div>
            <MessageSquareText size={18} />
          </div>
          <label>
            类型
            <input
              value={submission.genre}
              onChange={(event) =>
                setSubmission({ ...submission, genre: event.target.value })
              }
            />
          </label>
          <label>
            主题
            <input
              value={submission.theme}
              onChange={(event) =>
                setSubmission({ ...submission, theme: event.target.value })
              }
            />
          </label>
          <label>
            叙事气质
            <textarea
              value={submission.tone}
              onChange={(event) =>
                setSubmission({ ...submission, tone: event.target.value })
              }
            />
          </label>
          <label>
            起始事件
            <textarea
              value={submission.initial_incident}
              onChange={(event) =>
                setSubmission({
                  ...submission,
                  initial_incident: event.target.value,
                })
              }
            />
          </label>
          {error && <p className="form-error" role="alert">{error}</p>}
        </section>

        <aside className="submission-package" aria-label="初始设定预览">
          <p className="section-kicker">Editor 整理结果</p>
          <h2>{submission.title}</h2>
          <dl>
            <div><dt>时间</dt><dd>{submission.initial_time}</dd></div>
            <div><dt>地点</dt><dd>{submission.initial_location}</dd></div>
            <div><dt>世界压力</dt><dd>{submission.pressures[0]}</dd></div>
          </dl>
          <section>
            <h3>世界规则</h3>
            <ul>{submission.world_rules.map((rule) => <li key={rule}>{rule}</li>)}</ul>
          </section>
          <section>
            <h3><UsersRound size={15} /> 初始角色</h3>
            {submission.characters.map((character) => (
              <article className="submission-character" key={character.id}>
                <strong>{character.display_name}</strong>
                <span>{character.identity}</span>
                <small>{character.current_goal}</small>
              </article>
            ))}
          </section>
          {project && (
            <p className="submission-ready" role="status">
              <Check size={15} /> 世界版本 {project.world.version}，可进入第一轮
            </p>
          )}
        </aside>
      </div>
    </div>
  );
}
