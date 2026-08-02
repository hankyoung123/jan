from story_engine.domain.simulation import ControlMode, ControlPolicy


def pauses_after_step(policy: ControlPolicy) -> bool:
    return policy.pause_after_step or policy.mode == ControlMode.STEP


def hard_limit_reason(
    policy: ControlPolicy,
    *,
    completed_steps: int,
    elapsed_seconds: float,
) -> str | None:
    if completed_steps >= policy.max_steps:
        return "maximum step budget reached"
    if elapsed_seconds >= policy.max_runtime_seconds:
        return "maximum runtime reached"
    return None
