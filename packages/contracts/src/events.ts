export type EngineEventType =
  | "engine.status"
  | "workspace.changed"
  | "simulation.started"
  | "simulation.step.started"
  | "simulation.stage.started"
  | "simulation.stage.completed"
  | "simulation.stage.failed"
  | "simulation.step.completed"
  | "simulation.pause.requested"
  | "simulation.termination.requested"
  | "simulation.paused"
  | "simulation.checkpointed"
  | "simulation.terminated"
  | "simulation.failed"
  | "stream.resync_required";

export interface EngineEventEnvelope {
  event_id: string;
  project_id: string;
  subject_id: string;
  timestamp: string;
  sequence: number;
  type: EngineEventType;
  payload: Record<string, unknown>;
}

export type SimulationStage =
  | "termination"
  | "observation"
  | "actor_selection"
  | "action_spec"
  | "actor_action"
  | "resolution"
  | "memory_routing"
  | "promotion"
  | "commit";

export type SimulationStageStatus =
  | "pending"
  | "running"
  | "succeeded"
  | "skipped"
  | "failed"
  | "cancelled";

export interface SimulationStageEventPayload {
  event_id: string;
  project_id: string;
  session_id: string;
  branch_id: string;
  step: number;
  stage: SimulationStage;
  status: SimulationStageStatus;
  actor_id: string | null;
  action_spec: Record<string, unknown> | null;
  summary_text: string | null;
  input_record_ids: string[];
  output_record_ids: string[];
  visible_to: string[];
  profile_ids: string[];
  model_refs: string[];
  prompt_tokens: number;
  completion_tokens: number;
  duration_ms: number | null;
  checkpoint_id: string | null;
  error_code: string | null;
  started_at: string;
  completed_at: string | null;
}

const eventTypes: ReadonlySet<string> = new Set<EngineEventType>([
  "engine.status",
  "workspace.changed",
  "simulation.started",
  "simulation.step.started",
  "simulation.stage.started",
  "simulation.stage.completed",
  "simulation.stage.failed",
  "simulation.step.completed",
  "simulation.pause.requested",
  "simulation.termination.requested",
  "simulation.paused",
  "simulation.checkpointed",
  "simulation.terminated",
  "simulation.failed",
  "stream.resync_required",
]);

export function isEngineEvent(value: unknown): value is EngineEventEnvelope {
  if (typeof value !== "object" || value === null) return false;
  const candidate = value as Partial<EngineEventEnvelope>;
  return (
    typeof candidate.event_id === "string" &&
    typeof candidate.project_id === "string" &&
    typeof candidate.subject_id === "string" &&
    typeof candidate.timestamp === "string" &&
    typeof candidate.sequence === "number" &&
    Number.isSafeInteger(candidate.sequence) &&
    candidate.sequence > 0 &&
    typeof candidate.type === "string" &&
    eventTypes.has(candidate.type) &&
    typeof candidate.payload === "object" &&
    candidate.payload !== null
  );
}
