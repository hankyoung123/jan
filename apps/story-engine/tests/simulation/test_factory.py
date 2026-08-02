from pathlib import Path

from story_engine.concordia_runtime.memory import deterministic_embedder
from story_engine.domain.simulation import (
    ControlMode,
    ControlPolicy,
    TurnSessionRequest,
)
from story_engine.models.gateway import ModelGateway, UnavailableModelTransport
from story_engine.models.registry import ProfileRegistry
from story_engine.simulation.factory import ProjectRuntimeFactory
from story_engine.submission.service import SubmissionService, fog_harbor_submission


def test_project_runtime_imports_seed_with_private_memory_isolation(
    tmp_path: Path,
) -> None:
    SubmissionService(tmp_path).finalize(fog_harbor_submission())
    gateway = ModelGateway(
        ProfileRegistry(tmp_path / "models.json"),
        UnavailableModelTransport(),
    )
    runtime = ProjectRuntimeFactory(
        tmp_path,
        gateway,
        embedder=deterministic_embedder,
    )(
        "session:1",
        TurnSessionRequest(
            project_id="fog-harbor",
            branch_id="main",
            premise_text="灯塔突然熄灭。",
            content_locale="zh-CN",
            control=ControlPolicy(mode=ControlMode.STEP),
        ),
    )
    actor_memories = {
        actor.name: " ".join(
            record.text for record in actor.memory.retrieve_recent(limit=20)
        )
        for actor in runtime.actors
    }
    gm_memories = " ".join(
        record.text for record in runtime.game_master.memory.retrieve_recent(limit=20)
    )

    assert "父亲在灯塔附近失踪" in actor_memories["chen-mo"]
    assert "未归档的值班表" not in actor_memories["chen-mo"]
    assert "未归档的值班表" in actor_memories["lin-lan"]
    assert "父亲在灯塔附近失踪" not in actor_memories["lin-lan"]
    assert "父亲在灯塔附近失踪" in gm_memories
    assert "未归档的值班表" in gm_memories
