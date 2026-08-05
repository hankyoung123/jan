from typing import Any, cast

from story_engine.models.contracts import AgentProfile, AgentType


def agent_profile(
    *,
    id: str,
    task_type: str,
    model_ref: str | None = None,
    **values: Any,
) -> AgentProfile:
    del id
    mapped = "wiki_maintainer" if task_type == "wiki_maintenance" else task_type
    prompt = values.pop(
        "default_system_prompt",
        f"Test instructions for {mapped}.",
    )
    return AgentProfile(
        name=f"Test {mapped}",
        agent_type=cast(AgentType, mapped),
        default_system_prompt=prompt,
        model=model_ref,
        **values,
    )
