export type EngineEventType =
  | "engine.status"
  | "workspace.changed"
  | "simulation.started"
  | "simulation.step.completed"
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

const eventTypes: ReadonlySet<string> = new Set<EngineEventType>([
  "engine.status",
  "workspace.changed",
  "simulation.started",
  "simulation.step.completed",
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
