import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier, Lock, get_ident

from story_engine.domain.models import (
    Character,
    CharacterIntent,
    FactCandidate,
    NpcCandidate,
    StateChange,
    StoryEvent,
    WorldOutcome,
    WorldState,
)
from story_engine.evolution.context import CharacterContext, CharacterContextAssembler
from story_engine.evolution.service import EvolutionService
from story_engine.submission.service import SubmissionService, fog_harbor_submission
from story_engine.workspace.event_store import EventStore
from story_engine.workspace.project_store import ProjectStore


class DeterministicTurnGenerator:
    def __init__(self) -> None:
        self.intent_calls: list[str] = []
        self.resolve_calls = 0
        self.revision_calls: list[str] = []
        self._lock = Lock()

    def generate_intent(self, context: CharacterContext) -> CharacterIntent:
        with self._lock:
            self.intent_calls.append(context.character.id)
        visible_fact_ids = tuple(
            fact.id for fact in context.perception.visible_facts
        )
        fact_id = next(
            (
                item
                for item in context.character.known_fact_ids
                if item in visible_fact_ids
            ),
            visible_fact_ids[0],
        )
        actions = {
            "chen-mo": "检查灯塔机械装置",
            "lin-lan": "呼叫客船降低航速",
        }
        return CharacterIntent(
            character_id=context.character.id,
            action=actions.get(context.character.id, "观察当前局势"),
            target=context.perception.perceived_location,
            goal=context.character.current_goal or context.character.core_desire,
            knowledge_basis=(fact_id,),
            recognized_risk="行动可能加剧当前压力",
        )

    def resolve(
        self,
        world: WorldState,
        intents: tuple[CharacterIntent, ...],
        characters: tuple[Character, ...],
    ) -> WorldOutcome:
        assert characters
        with self._lock:
            self.resolve_calls += 1
        round_number = world.world_variables.get("round", 0)
        assert isinstance(round_number, int) and not isinstance(round_number, bool)
        display_names = {"chen-mo": "陈默", "lin-lan": "林岚"}
        participants = "、".join(
            display_names.get(intent.character_id, intent.character_id)
            for intent in intents
        )
        next_round = round_number + 1
        return WorldOutcome(
            summary=f"第 {next_round} 轮: {participants} 的行动改变了当前局势。",
            fact_candidates=(
                FactCandidate(
                    id=f"fact:round-{next_round:06d}",
                    statement=f"局势推进至第 {next_round} 轮。",
                    visibility="public",
                ),
            ),
            world_changes=(
                StateChange(
                    target_type="world",
                    target_id="world",
                    field="world_variables.round",
                    old_value=round_number,
                    new_value=next_round,
                    reason="统一结算所有角色行动后推进回合",
                ),
            ),
            unresolved_consequences=world.active_pressures,
        )

    def revise(
        self,
        world: WorldState,
        intents: tuple[CharacterIntent, ...],
        characters: tuple[Character, ...],
        previous_outcome: WorldOutcome,
        instruction: str,
    ) -> WorldOutcome:
        assert previous_outcome.summary
        with self._lock:
            self.revision_calls.append(instruction)
        round_number = world.world_variables.get("round", 0)
        assert isinstance(round_number, int) and not isinstance(round_number, bool)
        return WorldOutcome(
            summary="陈默与林岚暂缓行动, 先观察客船与灯塔的变化。",
            fact_candidates=(
                FactCandidate(
                    id=f"fact:revision-round-{round_number + 1:06d}",
                    statement="客船维持低速等待进一步指令。",
                    visibility="public",
                ),
            ),
            world_changes=(
                StateChange(
                    target_type="world",
                    target_id="world",
                    field="world_variables.round",
                    old_value=round_number,
                    new_value=round_number + 1,
                    reason="按修改要求降低行动强度后推进回合",
                ),
            ),
            unresolved_consequences=world.active_pressures,
        )


class NpcTurnGenerator(DeterministicTurnGenerator):
    def resolve(
        self,
        world: WorldState,
        intents: tuple[CharacterIntent, ...],
        characters: tuple[Character, ...],
    ) -> WorldOutcome:
        outcome = super().resolve(world, intents, characters)
        return outcome.model_copy(
            update={
                "new_npcs": (
                    NpcCandidate(
                        id="temporary-pilot",
                        identity="暴风雨中赶到港口的临时引航员",
                        purpose="引导客船避开近港暗礁",
                    ),
                )
            }
        )


class ParallelProbeGenerator(DeterministicTurnGenerator):
    def __init__(self) -> None:
        super().__init__()
        self._barrier = Barrier(2)
        self.thread_ids: set[int] = set()

    def generate_intent(self, context: CharacterContext) -> CharacterIntent:
        self.thread_ids.add(get_ident())
        self._barrier.wait(timeout=2)
        return super().generate_intent(context)


def _formal_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*.md"))
        if ".story-engine" not in path.parts
    }


def _project(tmp_path: Path) -> Path:
    SubmissionService(tmp_path).finalize(fog_harbor_submission())
    return tmp_path / "fog-harbor"


def _service(
    root: Path,
    generator: DeterministicTurnGenerator | None = None,
) -> EvolutionService:
    return EvolutionService(root, generator=generator or DeterministicTurnGenerator())


def test_character_contexts_isolate_private_facts_and_other_intents(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path)
    snapshot = ProjectStore(root).load()
    contexts = CharacterContextAssembler().assemble(snapshot)

    chen = contexts["chen-mo"]
    lin = contexts["lin-lan"]

    chen_fact_ids = {fact.id for fact in chen.perception.visible_facts}
    lin_fact_ids = {fact.id for fact in lin.perception.visible_facts}
    assert "secret:chen-father-disappearance" in chen_fact_ids
    assert "secret:lin-unfiled-duty-roster" not in chen_fact_ids
    assert "secret:lin-unfiled-duty-roster" in lin_fact_ids
    assert "secret:chen-father-disappearance" not in lin_fact_ids
    assert not hasattr(chen, "other_intents")
    assert chen.character.id == "chen-mo"
    assert lin.character.id == "lin-lan"


def test_candidate_locks_every_existing_character_version(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path)
    store = ProjectStore(root)
    store.save_character(
        Character(
            id="dock-worker",
            type="npc",
            identity="在近港码头值守的工人",
            core_desire="确保码头设备安全",
            version=3,
        ),
        overwrite=False,
    )

    candidate = _service(root).generate_turn(("chen-mo",))

    assert candidate.base_character_versions == {
        "chen-mo": 0,
        "dock-worker": 3,
        "lin-lan": 0,
    }


def test_npc_candidate_is_not_formal_or_active_until_user_confirmation(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path)
    service = _service(root, NpcTurnGenerator())

    candidate = service.generate_turn()

    assert candidate.outcome.new_npcs[0].id == "temporary-pilot"
    assert not (root / "characters/npc/temporary-pilot.md").exists()
    assert "temporary-pilot" not in CharacterContextAssembler().assemble(
        ProjectStore(root).load()
    )

    result = service.confirm(candidate.id)
    snapshot = ProjectStore(root).load()
    npc = next(item for item in snapshot.characters if item.id == "temporary-pilot")

    assert result.event.id == "event-000001"
    assert npc.type == "npc"
    assert npc.identity == "暴风雨中赶到港口的临时引航员"
    assert npc.core_desire == "引导客船避开近港暗礁"
    assert npc.current_goal is None
    assert npc.last_event_id == result.event.id
    assert npc.location == snapshot.world.current_location
    assert (root / "characters/npc/temporary-pilot.md").exists()
    assert "temporary-pilot" not in CharacterContextAssembler().assemble(snapshot)


def test_editor_blocks_npc_id_that_already_exists(tmp_path: Path) -> None:
    service = _service(_project(tmp_path))
    candidate = service.generate_turn()
    conflicted = candidate.with_outcome(
        candidate.outcome.model_copy(
            update={
                "new_npcs": (
                    NpcCandidate(
                        id="chen-mo",
                        identity="冒用现有角色 ID 的陌生人",
                        purpose="制造冲突",
                    ),
                )
            }
        )
    )

    reviewed = service.review(conflicted)

    assert reviewed.status == "needs_revision"
    assert reviewed.review is not None
    assert any(issue.code == "npc_id_conflict" for issue in reviewed.review.issues)


def test_editor_blocks_intent_using_another_characters_secret(
    tmp_path: Path,
) -> None:
    service = _service(_project(tmp_path))
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
    service = _service(_project(tmp_path))
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
    assert any(
        issue.code == "state_source_conflict" for issue in reviewed.review.issues
    )


def test_revision_and_discard_never_change_formal_markdown(tmp_path: Path) -> None:
    root = _project(tmp_path)
    service = _service(root)
    before = _formal_bytes(root)

    original = service.generate_turn()
    revised = service.request_revision(original.id, "让结果更克制")

    assert revised.review is not None
    assert revised.review != original.review
    assert revised.status == "reviewed"
    assert revised.outcome != original.outcome
    assert revised.outcome.summary == "陈默与林岚暂缓行动, 先观察客船与灯塔的变化。"
    assert service.generator.revision_calls == ["让结果更克制"]
    assert revised.intents == original.intents
    assert _formal_bytes(root) == before

    discarded = service.discard(revised.id)
    assert discarded.status == "discarded"
    assert _formal_bytes(root) == before


def test_ten_confirmed_rounds_remain_fully_traceable(tmp_path: Path) -> None:
    root = _project(tmp_path)
    service = _service(root)

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


def test_large_project_supports_thirty_traceable_rounds(tmp_path: Path) -> None:
    root = _project(tmp_path)
    store = ProjectStore(root)
    event_store = EventStore(root)
    history_size = 500
    history_start = datetime(2026, 1, 1, tzinfo=UTC)

    for sequence in range(1, history_size + 1):
        event_store.append(
            StoryEvent(
                id=f"event-{sequence:06d}",
                sequence=sequence,
                occurred_at=history_start + timedelta(minutes=sequence),
                summary=f"归档事件 {sequence}",
                participants=("chen-mo", "lin-lan"),
                source_turn_id=f"archived-turn-{sequence:06d}",
                approved_by_user=True,
            )
        )

    snapshot = store.load()
    store.save_world(
        snapshot.world.model_copy(
            update={
                "version": history_size,
                "world_variables": {"round": history_size},
            }
        )
    )
    for character in snapshot.characters:
        store.save_character(
            character.model_copy(
                update={
                    "version": history_size,
                    "last_event_id": f"event-{history_size:06d}",
                }
            )
        )

    service = _service(root)
    started = time.perf_counter()
    for offset in range(1, 31):
        expected_version = history_size + offset
        candidate = service.generate_turn()
        assert candidate.base_world_version == expected_version - 1
        assert candidate.base_workspace_revision is not None
        result = service.confirm(candidate.id)
        current = ProjectStore(root).load()

        assert result.event.sequence == expected_version
        assert result.event.source_turn_id == candidate.id
        assert current.world.version == expected_version
        assert current.world.world_variables["round"] == expected_version
        assert all(
            character.version == expected_version
            and character.last_event_id == result.event.id
            for character in current.characters
        )

    elapsed = time.perf_counter() - started
    events = EventStore(root).list_events()
    assert len(events) == history_size + 30
    assert [event.sequence for event in events[-30:]] == list(
        range(history_size + 1, history_size + 31)
    )
    assert elapsed < 60, f"30 rounds over a 500-event project took {elapsed:.2f}s"


def test_generation_emits_ordered_pipeline_events(tmp_path: Path) -> None:
    service = _service(_project(tmp_path))
    emitted: list[tuple[str, dict[str, object]]] = []

    candidate = service.generate_turn(
        event_sink=lambda event_type, payload: emitted.append((event_type, payload))
    )

    assert [event_type for event_type, _ in emitted] == [
        "turn.started",
        "character.intent.started",
        "character.intent.started",
        "character.intent.completed",
        "character.intent.completed",
        "resolver.started",
        "resolver.completed",
        "review.started",
        "review.completed",
    ]
    assert all(payload["turn_id"] == candidate.id for _, payload in emitted)
    assert emitted[1][1]["character_id"] == "chen-mo"
    assert emitted[2][1]["character_id"] == "lin-lan"


def test_generation_delegates_intents_and_resolution_to_injected_generator(
    tmp_path: Path,
) -> None:
    generator = DeterministicTurnGenerator()
    service = _service(_project(tmp_path), generator)

    candidate = service.generate_turn()

    assert sorted(generator.intent_calls) == ["chen-mo", "lin-lan"]
    assert generator.resolve_calls == 1
    assert tuple(intent.character_id for intent in candidate.intents) == (
        "chen-mo",
        "lin-lan",
    )


def test_character_intents_are_generated_in_parallel(tmp_path: Path) -> None:
    generator = ParallelProbeGenerator()
    service = _service(_project(tmp_path), generator)

    service.generate_turn()

    assert len(generator.thread_ids) == 2
