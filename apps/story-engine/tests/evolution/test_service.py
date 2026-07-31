from pathlib import Path

from story_engine.domain.models import CharacterIntent, StateChange
from story_engine.evolution.context import CharacterContextAssembler
from story_engine.evolution.service import EvolutionService
from story_engine.submission.service import SubmissionService, fog_harbor_submission
from story_engine.workspace.event_store import EventStore
from story_engine.workspace.project_store import ProjectStore


def _formal_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*.md"))
        if ".story-engine" not in path.parts
    }


def _project(tmp_path: Path) -> Path:
    SubmissionService(tmp_path).finalize(fog_harbor_submission())
    return tmp_path / "fog-harbor"


def test_character_contexts_isolate_private_facts_and_other_intents(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path)
    snapshot = ProjectStore(root).load()
    contexts = CharacterContextAssembler().assemble(snapshot)

    chen = contexts["chen-mo"]
    lin = contexts["lin-lan"]

    assert "secret:chen-father-disappearance" in chen.visible_fact_ids
    assert "secret:lin-unfiled-duty-roster" not in chen.visible_fact_ids
    assert "secret:lin-unfiled-duty-roster" in lin.visible_fact_ids
    assert "secret:chen-father-disappearance" not in lin.visible_fact_ids
    assert not hasattr(chen, "other_intents")
    assert chen.character.id == "chen-mo"
    assert lin.character.id == "lin-lan"


def test_editor_blocks_intent_using_another_characters_secret(
    tmp_path: Path,
) -> None:
    service = EvolutionService(_project(tmp_path))
    candidate = service.generate_turn()
    leaked = candidate.model_copy(
        update={
            "intents": (
                CharacterIntent(
                    character_id="chen-mo",
                    action="读取林岚未归档的值班表",
                    goal="查明灯塔熄灭原因",
                    knowledge_basis=("secret:lin-unfiled-duty-roster",),
                ),
                candidate.intents[1],
            )
        }
    )

    reviewed = service.review(leaked)

    assert reviewed.status == "needs_revision"
    assert reviewed.review is not None
    assert reviewed.review.passed is False
    assert any(issue.code == "knowledge_boundary" for issue in reviewed.review.issues)


def test_editor_blocks_world_change_with_incorrect_source_value(tmp_path: Path) -> None:
    service = EvolutionService(_project(tmp_path))
    candidate = service.generate_turn()
    conflicted = candidate.with_outcome(
        candidate.outcome.model_copy(
            update={
                "world_changes": (
                    StateChange(
                        target_type="world",
                        target_id="world",
                        field="current_location",
                        old_value="不存在的港口",
                        new_value="灯塔",
                        reason="角色行动改变当前地点",
                    ),
                )
            }
        )
    )

    reviewed = service.review(conflicted)

    assert reviewed.status == "needs_revision"
    assert reviewed.review is not None
    assert any(issue.code == "world_rule_conflict" for issue in reviewed.review.issues)


def test_revision_and_discard_never_change_formal_markdown(tmp_path: Path) -> None:
    root = _project(tmp_path)
    service = EvolutionService(root)
    before = _formal_bytes(root)

    original = service.generate_turn()
    revised = service.request_revision(original.id, "让结果更克制")

    assert revised.review is not None
    assert revised.review != original.review
    assert revised.status == "reviewed"
    assert "让结果更克制" in revised.outcome.summary
    assert _formal_bytes(root) == before

    discarded = service.discard(revised.id)
    assert discarded.status == "discarded"
    assert _formal_bytes(root) == before


def test_ten_confirmed_rounds_remain_fully_traceable(tmp_path: Path) -> None:
    root = _project(tmp_path)
    service = EvolutionService(root)

    for expected_version in range(1, 11):
        candidate = service.generate_turn()
        assert candidate.status == "reviewed"
        assert candidate.base_world_version == expected_version - 1
        assert set(candidate.base_character_versions) == {"chen-mo", "lin-lan"}

        result = service.confirm(candidate.id)
        snapshot = ProjectStore(root).load()

        assert result.event.sequence == expected_version
        assert result.event.source_turn_id == candidate.id
        assert "chen-mo" not in result.event.summary
        assert "陈默" in result.event.summary
        assert snapshot.world.version == expected_version
        assert snapshot.world.world_variables["round"] == expected_version
        assert all(
            character.version == expected_version
            and character.last_event_id == result.event.id
            for character in snapshot.characters
        )

    events = EventStore(root).list_events()
    assert [event.sequence for event in events] == list(range(1, 11))
    assert [event.id for event in events] == [
        f"event-{sequence:06d}" for sequence in range(1, 11)
    ]


def test_generation_emits_ordered_pipeline_events(tmp_path: Path) -> None:
    service = EvolutionService(_project(tmp_path))
    emitted: list[tuple[str, dict[str, object]]] = []

    candidate = service.generate_turn(
        event_sink=lambda event_type, payload: emitted.append((event_type, payload))
    )

    assert [event_type for event_type, _ in emitted] == [
        "turn.started",
        "character.intent.started",
        "character.intent.completed",
        "character.intent.started",
        "character.intent.completed",
        "resolver.started",
        "resolver.completed",
        "review.started",
        "review.completed",
    ]
    assert all(payload["turn_id"] == candidate.id for _, payload in emitted)
    assert emitted[1][1]["character_id"] == "chen-mo"
    assert emitted[3][1]["character_id"] == "lin-lan"
