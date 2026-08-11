# ruff: noqa: RUF001

import json
from datetime import UTC, datetime
from pathlib import Path
from threading import Event

from story_engine.concordia_runtime.components.knowledge import CharacterContextBudget
from story_engine.concordia_runtime.factory import (
    ConcordiaActorFactory,
    ConcordiaStoryActor,
    default_character_recipe,
    default_game_master_recipe,
)
from story_engine.concordia_runtime.memory import ConcordiaMemoryBank
from story_engine.concordia_runtime.replay import ReplayLanguageModel
from story_engine.domain.action import ActionOutputType, ActionSpec
from story_engine.domain.memory import MemoryRecord, MemoryRecordType, MemoryScope
from story_engine.domain.models import Character, WorldState
from story_engine.domain.recipe import PerceptionFrame
from story_engine.domain.wiki import WikiPatch, WikiPatchOperation
from story_engine.simulation.runtime import StorySimulationRuntime
from story_engine.submission.service import SubmissionService, fog_harbor_submission
from story_engine.wiki.store import WikiStore


def _build_actor(
    tmp_path: Path,
    actor_id: str,
    *,
    responses: tuple[str, ...] = ("继续调查。",),
    goal: str = "找到锁房钥匙",
    location: str = "locked-room",
    project_root: Path | None = None,
) -> tuple[ConcordiaStoryActor, ReplayLanguageModel, ConcordiaMemoryBank]:
    model = ReplayLanguageModel(text_responses=responses)
    memory = ConcordiaMemoryBank(
        owner_id=actor_id,
        scope=MemoryScope.CHARACTER,
    )
    actor = ConcordiaActorFactory({"actor": model}).build_actor(
        default_character_recipe(
            model_profile_id="actor",
            content_locale="zh-CN",
        ),
        actor_params={
            "name": actor_id,
            "display_name": actor_id,
            "actor_state": json.dumps(
                {"current_goal": goal, "location": location},
                ensure_ascii=False,
            ),
            "project_root": str(project_root or tmp_path),
            "branch_id": "main",
        },
        memory=memory,
    )
    return actor, model, memory


def _observe(
    actor: ConcordiaStoryActor,
    step: int,
    text: str,
    *,
    participants: tuple[str, ...] = (),
    locations: tuple[str, ...] = (),
) -> None:
    actor.observe(
        PerceptionFrame(
            frame_id=f"observation:long-term:{step}:{actor.name}",
            session_id="session:long-term",
            branch_id="main",
            actor_id=actor.name,
            step=step,
            content_locale="zh-CN",
            observation_text=text,
            participant_ids=participants,
            location_ids=locations,
        )
    )


def _act(actor: ConcordiaStoryActor, step: int) -> str:
    return actor.act(
        ActionSpec(
            spec_id=f"action:long-term:{step}:{actor.name}",
            output_type=ActionOutputType.FREE,
            call_to_action="围绕锁房和钥匙决定下一步行动。",
            tag="investigate",
            content_locale="zh-CN",
        )
    )


def _section(prompt: str, start: str, end: str) -> str:
    content = prompt.split(start, 1)[1]
    return content.split(end, 1)[0]


def test_turn_five_key_memory_is_recalled_when_locked_room_returns_at_turn_eighty(
    tmp_path: Path,
) -> None:
    actor, model, _memory = _build_actor(tmp_path, "lin-che")
    for step in range(1, 80):
        text = (
            "张野把黄铜钥匙藏进抽屉。"
            if step == 5
            else f"第 {step} 回合，林澈记录了窗外的天气。"
        )
        _observe(
            actor,
            step,
            text,
            participants=("zhang-ye",) if step == 5 else (),
            locations=("locked-room",) if step == 5 else ("garden",),
        )
    _observe(
        actor,
        80,
        "张野再次站在锁房门口，大家正在讨论钥匙。",
        participants=("zhang-ye",),
        locations=("locked-room",),
    )

    _act(actor, 80)

    prompt = model.prompts[-1]
    relevant = _section(prompt, "Relevant Recall:\n", "Current Perception:")
    recent = _section(prompt, "Recent Memory:\n", "Relevant Recall:")
    assert "张野把黄铜钥匙藏进抽屉" in relevant
    assert "张野把黄铜钥匙藏进抽屉" not in recent
    assert recent.count("- [Step") == 12
    assert prompt.index("Current Actor State") < prompt.index("Wiki:")
    assert prompt.index("Wiki:") < prompt.index("Recent Memory:")
    assert prompt.index("Recent Memory:") < prompt.index("Relevant Recall:")
    assert prompt.index("Relevant Recall:") < prompt.index("Current Perception:")


def test_relevant_recall_filters_unrelated_weather_and_meal_history(
    tmp_path: Path,
) -> None:
    actor, model, _memory = _build_actor(tmp_path, "lin-che")
    for step in range(1, 80):
        if step == 5:
            text = "张野把黄铜钥匙藏进抽屉。"
            participants = ("zhang-ye",)
            locations = ("locked-room",)
        elif step % 2:
            text = f"天气档案 {step}：花园里下着小雨。"
            participants = ()
            locations = ("garden",)
        else:
            text = f"晚饭记录 {step}：餐厅供应了热汤。"
            participants = ()
            locations = ("dining-room",)
        _observe(
            actor,
            step,
            text,
            participants=participants,
            locations=locations,
        )
    _observe(
        actor,
        80,
        "张野询问锁房的黄铜钥匙在哪里。",
        participants=("zhang-ye",),
        locations=("locked-room",),
    )

    _act(actor, 80)

    relevant = _section(
        model.prompts[-1],
        "Relevant Recall:\n",
        "Current Perception:",
    )
    assert "张野把黄铜钥匙藏进抽屉" in relevant
    assert "天气档案" not in relevant
    assert "晚饭记录" not in relevant


def test_private_memory_never_enters_other_actor_or_player_recall(
    tmp_path: Path,
) -> None:
    lin_memory = ConcordiaMemoryBank(
        owner_id="lin-che",
        scope=MemoryScope.CHARACTER,
    )
    lin_memory.add(
        MemoryRecord(
            record_id="private:lin-che:ledger",
            record_type=MemoryRecordType.OBSERVATION,
            scope=MemoryScope.CHARACTER,
            owner_id="lin-che",
            session_id="session:long-term",
            branch_id="main",
            step=5,
            text="PRIVATE_LIN_MEMORY_TOKEN：港口账本密码藏在蓝色信封里。",
            content_locale="zh-CN",
            created_at=datetime.now(UTC),
            actor_ids=("lin-che",),
            visible_to=("lin-che",),
            importance=1.0,
        )
    )
    zhang, zhang_model, _ = _build_actor(tmp_path, "zhang-ye")
    player, player_model, _ = _build_actor(tmp_path, "player")
    for actor in (zhang, player):
        _observe(actor, 79, "昨夜港口很安静。", locations=("harbor",))
        _observe(actor, 80, "现在需要回想港口账本密码。", locations=("harbor",))
        _act(actor, 80)

    assert len(lin_memory.records()) == 1
    assert "PRIVATE_LIN_MEMORY_TOKEN" not in zhang_model.prompts[-1]
    assert "PRIVATE_LIN_MEMORY_TOKEN" not in player_model.prompts[-1]


def test_three_hundred_turn_prompt_stays_bounded_with_old_recall_and_wiki(
    tmp_path: Path,
) -> None:
    snapshot = SubmissionService(tmp_path).finalize(fog_harbor_submission())
    project_root = tmp_path / snapshot.project.id
    wiki_text = "林澈长期怀疑张野与港口记录有关，并逐渐失去对他的信任。"
    WikiStore(project_root, "main").apply_patches(
        (
            WikiPatch(
                path="characters/chen-mo/long-term.md",
                operation=WikiPatchOperation.CREATE,
                content=f"# Long-term understanding\n\n{wiki_text}",
                source_ids=("event:long-term-belief",),
            ),
        ),
        checkpoint_id="checkpoint:long-term-wiki",
        step=4,
    )
    actor, model, memory = _build_actor(
        tmp_path,
        "chen-mo",
        responses=("继续调查。", "再次调查。"),
        project_root=project_root,
    )
    for step in range(1, 301):
        if step == 5:
            text = "张野把黄铜钥匙藏进抽屉。"
            participants = ("zhang-ye",)
            locations = ("locked-room",)
        elif step in {100, 300}:
            text = "张野回到锁房，黄铜钥匙再次成为焦点。"
            participants = ("zhang-ye",)
            locations = ("locked-room",)
        else:
            text = f"常规巡查记录 {step}：走廊没有异常。"
            participants = ()
            locations = ("corridor",)
        _observe(
            actor,
            step,
            text,
            participants=participants,
            locations=locations,
        )
        if step in {100, 300}:
            _act(actor, step)

    prompt_100, prompt_300 = model.prompts
    for prompt in (prompt_100, prompt_300):
        relevant = _section(prompt, "Relevant Recall:\n", "Current Perception:")
        assert "张野把黄铜钥匙藏进抽屉" in relevant
        assert wiki_text in prompt
        assert "PRIVATE_LIN_MEMORY_TOKEN" not in prompt
        assert len(prompt) < 60_000
    assert len(prompt_300) <= len(prompt_100) + 1_000
    assert len(memory.records()) == 300


def test_real_runtime_routes_metadata_and_recalls_it_for_an_npc(
    tmp_path: Path,
) -> None:
    turn_count = 15
    actor, actor_model, memory = _build_actor(
        tmp_path,
        "actor-a",
        responses=tuple(f"Action {step}" for step in range(turn_count)),
        goal="Find the brass key",
        location="locked-room",
    )
    gm_text = tuple(
        value
        for step in range(turn_count)
        for value in (
            (
                "The brass key and portrait demand attention."
                if step == turn_count - 1
                else f"Routine corridor observation {step}."
            ),
            '{"call_to_action":"Investigate the room.",'
            '"output_type":"free","options":[],"tag":"investigate"}',
            (
                '{"event_text":"The brass key is hidden behind the portrait.",'
                '"boundary":"none","visibility":"participants",'
                '"participant_names":["actor-a"]}'
                if step == 0
                else (
                    '{"event_text":"Routine corridor result '
                    f'{step}.","boundary":"none",'
                    '"visibility":"participants",'
                    '"participant_names":["actor-a"]}'
                )
            ),
        )
    )
    gm_model = ReplayLanguageModel(
        text_responses=gm_text,
        choice_responses=tuple(
            choice for _ in range(turn_count) for choice in ("No", "actor-a")
        ),
    )
    gm = ConcordiaActorFactory({"gm": gm_model}).build_game_master(
        default_game_master_recipe(
            model_profile_id="gm",
            content_locale="en-US",
        ),
        gm_params={
            "name": "gm",
            "scene_goal": "Find the archive key.",
            "project_root": str(tmp_path),
            "branch_id": "main",
        },
        actors=(actor,),
        shared_memory=ConcordiaMemoryBank(
            owner_id="gm",
            scope=MemoryScope.GAME_MASTER,
        ),
    )
    runtime = StorySimulationRuntime(
        project_id="project-1",
        session_id="session:runtime-recall",
        branch_id="main",
        content_locale="en-US",
        actors=(actor,),
        game_master=gm,
        characters=(
            Character(
                id="actor-a",
                display_name="actor-a",
                type="active",
                identity="An NPC investigator.",
                core_desire="Find the truth.",
                current_goal="Find the brass key.",
                location="locked-room",
            ),
        ),
        world=WorldState(
            current_time="21:00",
            current_location="archive",
        ),
        initial_roster_selected=True,
    )

    for step in range(turn_count):
        runtime.execute_step(step, cancellation=Event())

    routed = next(
        record
        for record in memory.records()
        if record.record_id == "event-observation:session:runtime-recall:0:actor-a"
    )
    relevant = _section(
        actor_model.prompts[-1],
        "Relevant Recall:\n",
        "Current Perception:",
    )
    assert routed.actor_ids == ("actor-a",)
    assert len(routed.location_ids) == 2
    assert all(location.startswith("location:") for location in routed.location_ids)
    assert "investigate" in routed.tags
    assert "The brass key is hidden behind the portrait" in relevant


def test_sixty_thousand_character_perception_respects_the_total_budget(
    tmp_path: Path,
) -> None:
    actor, model, _memory = _build_actor(tmp_path, "actor-a")
    perception = "CURRENT_START:" + ("x" * 59_970) + ":CURRENT_END"
    _observe(actor, 1, perception)
    _act(actor, 1)
    prompt = model.prompts[-1]
    context = prompt[prompt.index("Wiki:") : prompt.index("\n\n\nExercise:")]

    budget = CharacterContextBudget()
    current = context.split("Current Perception:\n", 1)[1]
    assert len(context) <= budget.total_chars
    assert current.startswith("CURRENT_START:")
    assert current.count("x") == budget.perception_chars - len("CURRENT_START:")
    assert "CURRENT_END" not in current
