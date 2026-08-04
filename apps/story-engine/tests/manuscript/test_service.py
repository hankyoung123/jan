import asyncio
from datetime import UTC, datetime
from pathlib import Path

from story_engine.domain.narrative import NarrativeContext, NarrativeSource
from story_engine.domain.projection import (
    EventVisibility,
    ResolvedEvent,
    SimulationBoundary,
)
from story_engine.manuscript.models import ProjectCreativeContext
from story_engine.manuscript.service import (
    WRITER_FIRST_CONTENT_TIMEOUT_SECONDS,
    GatewayManuscriptAgent,
)
from story_engine.models.contracts import ModelProfile, ModelRequest, ModelResponse
from story_engine.models.registry import ProfileRegistry


class RecordingGateway:
    def __init__(self, registry: ProfileRegistry) -> None:
        self.registry = registry
        self.requests: list[ModelRequest] = []

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        return ModelResponse(
            profile_id=request.profile_id,
            model_ref="test-provider/test-writer",
            content='{"title":"潮声","body":"灯塔在雨中亮起。"}',
            parsed_output={"title": "潮声", "body": "灯塔在雨中亮起。"},
            finish_reason="stop",
        )


def _source() -> NarrativeContext:
    event = ResolvedEvent(
        event_id="event-1",
        session_id="session-1",
        step=1,
        actor_id="chen-mo",
        event_text="陈默点亮了灯塔。",
        visibility=EventVisibility.PUBLIC,
        participant_ids=("chen-mo",),
        content_locale="zh-CN",
        occurred_at=datetime(2026, 8, 4, tzinfo=UTC),
    )
    source = NarrativeSource(
        project_id="fog-harbor",
        branch_id="main",
        checkpoint_id="checkpoint-1",
        from_step=1,
        to_step=1,
        boundary=SimulationBoundary.SCENE,
        event_ids=(event.event_id,),
        memory_record_ids=(),
        viewpoint_actor_id="chen-mo",
        content_locale="zh-CN",
    )
    return NarrativeContext(
        source=source,
        events=(event,),
        game_master_memories=(),
    )


def test_writer_uses_provider_length_and_five_minute_content_deadline(
    tmp_path: Path,
) -> None:
    registry = ProfileRegistry(tmp_path / "models.json")
    registry.upsert_profile(
        ModelProfile(
            id="writer",
            task_type="writer",
            model_ref="test-provider/test-writer",
            max_output_tokens=4096,
            timeout_seconds=90,
        )
    )
    gateway = RecordingGateway(registry)
    agent = GatewayManuscriptAgent(gateway)  # type: ignore[arg-type]

    output = asyncio.run(
        agent.generate(
            _source(),
            project=ProjectCreativeContext(
                project_id="fog-harbor",
                title="雾港",
                genre="悬疑",
                theme="真相与代价",
                tone="克制",
                content_locale="zh-CN",
            ),
        )
    )

    assert output.body == "灯塔在雨中亮起。"
    request = gateway.requests[0]
    assert request.output_token_limit == "provider"
    assert request.max_output_tokens is None
    assert (
        request.first_content_timeout_seconds
        == WRITER_FIRST_CONTENT_TIMEOUT_SECONDS
        == 300
    )
    assert request.timeout_seconds == 90
