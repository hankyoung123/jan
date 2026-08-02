from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from story_engine.domain.action import (
    ActionOutputType,
    ActionSpec,
    EntityRole,
)
from story_engine.domain.memory import (
    MemoryRecord,
    MemoryRecordType,
    MemoryScope,
    MemorySnapshot,
)
from story_engine.domain.projection import EventVisibility, ResolvedEvent
from story_engine.domain.recipe import AgentRecipe, ComponentRecipe
from story_engine.domain.simulation import (
    ControlMode,
    ControlPolicy,
    TurnSessionRequest,
    TurnSessionSnapshot,
    TurnSessionStatus,
)


def test_action_spec_enforces_choice_contract() -> None:
    with pytest.raises(ValidationError, match="requires options"):
        ActionSpec(
            spec_id="spec:1",
            output_type=ActionOutputType.CHOICE,
            call_to_action="选择一条路径。",
            content_locale="zh-CN",
        )

    with pytest.raises(ValidationError, match="cannot contain options"):
        ActionSpec(
            spec_id="spec:2",
            output_type=ActionOutputType.FREE,
            call_to_action="描述下一步。",
            options=("left",),
            content_locale="zh-CN",
        )


def test_machine_fields_are_stable_across_content_locales() -> None:
    chinese = ActionSpec(
        spec_id="spec:scene-1",
        output_type=ActionOutputType.CHOICE,
        call_to_action="选择下一步。",
        options=("等待", "离开"),
        option_ids=("wait", "leave"),
        tag="scene_action",
        content_locale="zh-CN",
    )
    english = chinese.model_copy(
        update={
            "call_to_action": "Choose the next move.",
            "options": ("Wait", "Leave"),
            "content_locale": "en-US",
        }
    )

    for field in ("spec_id", "output_type", "option_ids", "tag"):
        assert getattr(chinese, field) == getattr(english, field)


def test_restricted_event_requires_observers_and_timezone() -> None:
    with pytest.raises(ValidationError, match="requires observer_ids"):
        ResolvedEvent(
            event_id="event:1",
            session_id="session:1",
            step=1,
            event_text="陈默发现了钥匙。",
            visibility=EventVisibility.RESTRICTED,
            content_locale="zh-CN",
            occurred_at=datetime.now(UTC),
        )

    with pytest.raises(ValidationError, match="timezone"):
        ResolvedEvent(
            event_id="event:1",
            session_id="session:1",
            step=1,
            event_text="The bell rang.",
            visibility=EventVisibility.PUBLIC,
            content_locale="en-US",
            occurred_at=datetime.now(),
        )


def test_memory_record_and_snapshot_validate_recovery_fields() -> None:
    record = MemoryRecord(
        record_id="memory:actor-a:1",
        record_type=MemoryRecordType.OBSERVATION,
        scope=MemoryScope.CHARACTER,
        owner_id="actor-a",
        session_id="session:1",
        branch_id="main",
        step=1,
        text="A heard the public bell.",
        content_locale="en-US",
        created_at=datetime.now(UTC),
        visible_to=("actor-a",),
    )
    snapshot = MemorySnapshot(
        owner_id=record.owner_id,
        scope=record.scope,
        state={"records": [record.model_dump(mode="json")]},
        record_count=1,
        state_hash="a" * 64,
    )

    assert snapshot.owner_id == "actor-a"
    assert snapshot.record_count == 1

    invalid_payload = snapshot.model_dump(mode="json")
    invalid_payload["state_hash"] = "not-a-hash"
    with pytest.raises(ValidationError, match="string_pattern_mismatch"):
        MemorySnapshot.model_validate(invalid_payload)


def test_agent_recipe_rejects_duplicate_component_identity_or_order() -> None:
    duplicate = (
        ComponentRecipe(
            component_id="identity",
            component_type="constant",
            order=0,
        ),
        ComponentRecipe(
            component_id="identity",
            component_type="memory",
            order=1,
        ),
    )
    with pytest.raises(ValidationError, match="component IDs must be unique"):
        AgentRecipe(
            recipe_id="character.default",
            version="v1",
            role=EntityRole.CHARACTER,
            prefab_type="story_character",
            model_profile_id="actor",
            components=duplicate,
            content_locale="zh-CN",
            system_instruction_text="只根据角色可见的信息行动。",
        )


def test_session_contract_captures_control_and_checkpoint_state() -> None:
    request = TurnSessionRequest(
        project_id="fog-harbor",
        branch_id="main",
        premise_text="港口的灯塔突然熄灭。",
        actor_ids=("chen-mo",),
        content_locale="zh-CN",
        control=ControlPolicy(mode=ControlMode.STEP),
        seed=7,
    )
    now = datetime.now(UTC)
    snapshot = TurnSessionSnapshot(
        session_id="session:1",
        project_id=request.project_id,
        branch_id=request.branch_id,
        status=TurnSessionStatus.CREATED,
        content_locale=request.content_locale,
        request=request,
        current_step=0,
        actor_states={"chen-mo": {}},
        game_master_states={"gm": {}},
        memory_snapshots={},
        raw_log_offset=0,
        started_at=now,
        updated_at=now,
        state_hash="0" * 64,
    )

    assert request.control.mode == ControlMode.STEP
    assert snapshot.checkpoint_id is None
