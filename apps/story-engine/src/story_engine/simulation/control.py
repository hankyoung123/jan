from story_engine.domain.projection import SimulationBoundary
from story_engine.domain.simulation import ControlMode, ControlPolicy


def pauses_after_boundary(
    policy: ControlPolicy,
    boundary: SimulationBoundary,
) -> bool:
    if policy.mode == ControlMode.STEP:
        return True
    if policy.mode == ControlMode.SCENE:
        return boundary in {SimulationBoundary.SCENE, SimulationBoundary.CHAPTER}
    if policy.mode == ControlMode.CHAPTER:
        return boundary == SimulationBoundary.CHAPTER
    return policy.pause_after_scene and boundary in {
        SimulationBoundary.SCENE,
        SimulationBoundary.CHAPTER,
    }


def hard_limit_reason(
    policy: ControlPolicy,
    *,
    completed_steps: int,
    completed_scenes: int,
    elapsed_seconds: float,
) -> str | None:
    if completed_steps >= policy.max_steps:
        return "maximum step budget reached"
    if elapsed_seconds >= policy.max_runtime_seconds:
        return "maximum runtime reached"
    if completed_scenes >= policy.max_scenes:
        return "maximum scene budget reached"
    return None
