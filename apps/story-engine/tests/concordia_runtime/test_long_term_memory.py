# ruff: noqa: RUF001

import json
from datetime import UTC, datetime
from pathlib import Path
from threading import Event

from story_engine.concordia_runtime.components.knowledge import (
    CharacterContextBudget,
    WorldWikiContext,
)
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
    wiki_store = WikiStore(project_root, "main")
    beliefs = wiki_store.load_page("characters/chen-mo/beliefs.md")
    wiki_store.save_page(
        beliefs.path,
        f"{beliefs.content}\n\n{wiki_text}",
        expected_revision=beliefs.revision,
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

    restored_actor, restored_model, restored_memory = _build_actor(
        tmp_path,
        "chen-mo",
        responses=("恢复后继续调查。",),
        project_root=project_root,
    )
    restored_memory.replay(memory.records())
    assert len(restored_memory.records()) == 300
    _observe(
        restored_actor,
        301,
        "张野回到锁房，黄铜钥匙再次成为焦点。",
        participants=("zhang-ye",),
        locations=("locked-room",),
    )
    _act(restored_actor, 301)
    restored_prompt = restored_model.prompts[-1]
    restored_relevant = _section(
        restored_prompt,
        "Relevant Recall:\n",
        "Current Perception:",
    )
    assert "张野把黄铜钥匙藏进抽屉" in restored_relevant
    assert len(restored_prompt) < 60_000


def test_real_runtime_routes_metadata_and_recalls_it_for_an_npc(
    tmp_path: Path,
) -> None:
    turn_count = 3
    actor, actor_model, memory = _build_actor(
        tmp_path,
        "actor-a",
        responses=tuple(f"Action {step}" for step in range(turn_count)),
        goal="Follow the repeating signal",
        location="locked-room",
    )
    actor.entity.get_component("knowledge").set_state({"recent_limit": 0})
    target_text = "Signal alpha repeats near the door TARGET-MEMORY."
    distractor_text = "Signal alpha repeats near the door DISTRACTOR-MEMORY."
    gm_text: list[str] = []
    for step in range(turn_count):
        gm_text.append(
            "Signal alpha repeats near the door."
            if step == turn_count - 1
            else f"Routine observation {step}."
        )
        gm_text.append(
            json.dumps(
                {
                    "call_to_action": "Follow the signal.",
                    "output_type": "free",
                    "options": [],
                    "tag": "investigate" if step in {0, turn_count - 1} else "wait",
                }
            )
        )
        resolution: dict[str, object] = {
            "event_text": f"Routine result {step}.",
            "boundary": "none",
            "visibility": "participants",
            "participant_names": ["actor-a"],
        }
        if step == 0:
            resolution.update(
                {
                    "event_text": target_text,
                    "participant_names": ["actor-a", "actor-b"],
                }
            )
        elif step == 1:
            resolution.update(
                {
                    "event_text": distractor_text,
                    "state_updates": [
                        {
                            "target": "character_projection",
                            "target_name": "actor-a",
                            "path": "location",
                            "value": "corridor",
                        },
                        {
                            "target": "world_projection",
                            "target_name": None,
                            "path": "current_location",
                            "value": "hall",
                        },
                    ],
                }
            )
        elif step == turn_count - 2:
            resolution.update(
                {
                    "participant_names": ["actor-a", "actor-b"],
                    "state_updates": [
                        {
                            "target": "character_projection",
                            "target_name": "actor-a",
                            "path": "location",
                            "value": "locked-room",
                        },
                        {
                            "target": "world_projection",
                            "target_name": None,
                            "path": "current_location",
                            "value": "archive",
                        },
                    ],
                }
            )
        gm_text.append(json.dumps(resolution))
    gm_model = ReplayLanguageModel(
        text_responses=tuple(gm_text),
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
                current_goal="Follow the repeating signal.",
                location="locked-room",
            ),
            Character(
                id="actor-b",
                display_name="actor-b",
                type="active",
                identity="A remote participant.",
                core_desire="Understand the signal.",
                current_goal="Listen from the archive.",
                location="archive",
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

    target = next(
        record
        for record in memory.records()
        if record.record_id == "event-observation:session:runtime-recall:0:actor-a"
    )
    distractor = next(
        record
        for record in memory.records()
        if record.record_id == "event-observation:session:runtime-recall:1:actor-a"
    )
    relevant = _section(
        actor_model.prompts[-1],
        "Relevant Recall:\n",
        "Current Perception:",
    )
    assert target.actor_ids == ("actor-a", "actor-b")
    assert distractor.actor_ids == ("actor-a",)
    assert len(target.location_ids) == 2
    assert target.location_ids != distractor.location_ids
    assert "investigate" in target.tags
    assert "wait" in distractor.tags
    assert target_text in relevant
    assert distractor_text in relevant
    assert relevant.index(target_text) < relevant.index(distractor_text)
    assert len(actor_model.prompts[-1]) < 60_000


def test_stale_wiki_is_excluded_from_actor_and_game_master_context(
    tmp_path: Path,
) -> None:
    SubmissionService(tmp_path).finalize(fog_harbor_submission())
    root = tmp_path / "fog-harbor"
    store = WikiStore(root, "main")
    marker = "ABANDONED_WIKI_SECRET"
    for path in ("characters/chen-mo/self.md", "world/state.md"):
        page = store.load_page(path)
        store.save_page(
            path,
            f"{page.content}\n\n{marker}",
            expected_revision=page.revision,
        )
    store.mark_stale("checkpoint-" + "a" * 64, 9)

    actor, model, _memory = _build_actor(
        tmp_path,
        "chen-mo",
        project_root=root,
    )
    _observe(actor, 1, "Check the lighthouse mechanism.")
    _act(actor, 1)
    actor_wiki = _section(model.prompts[-1], "Wiki:\n", "Recent Memory:")
    gm_wiki = WorldWikiContext(
        project_root=str(root),
        branch_id="main",
    )._make_pre_act_value()

    assert marker not in actor_wiki
    assert marker not in gm_wiki
    assert "Wiki unavailable / stale." in actor_wiki
    assert gm_wiki == "Wiki unavailable / stale."


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
