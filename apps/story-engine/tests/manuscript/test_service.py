import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

from profile_factory import agent_profile as _profile

from story_engine.domain.message import ModelMessageContext
from story_engine.domain.narrative import EditorContext, NarrativeSource, WriterContext
from story_engine.domain.projection import (
    EventVisibility,
    ResolvedEvent,
    SimulationBoundary,
)
from story_engine.manuscript.models import ProjectCreativeContext
from story_engine.manuscript.service import (
    WRITER_FIRST_CONTENT_TIMEOUT_SECONDS,
    GatewayManuscriptAgent,
    parse_writer_markdown,
)
from story_engine.models.contracts import ModelRequest, ModelResponse
from story_engine.models.registry import ProfileRegistry


class RecordingGateway:
    def __init__(self, registry: ProfileRegistry) -> None:
        self.registry = registry
        self.requests: list[ModelRequest] = []
        self.contexts: list[ModelMessageContext | None] = []

    async def complete(
        self,
        request: ModelRequest,
        *,
        context: ModelMessageContext | None = None,
    ) -> ModelResponse:
        self.requests.append(request)
        self.contexts.append(context)
        parsed_output = None
        content = "# 潮声\n\n灯塔在雨中亮起。"
        if request.task_type == "editor":
            parsed_output = {
                "summary": "One unsupported fact was found.",
                "issues": ["The source does not establish a hidden tunnel."],
            }
            content = json.dumps(parsed_output)
        return ModelResponse(
            profile_id=request.profile_id,
            model_ref="test-provider/test-writer",
            content=content,
            parsed_output=parsed_output,
            finish_reason="stop",
        )


def _source() -> WriterContext:
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
        wiki_branch_id="main",
        wiki_version_id="seed",
        viewpoint_actor_id="chen-mo",
        content_locale="zh-CN",
    )
    return WriterContext(
        source=source,
        events=(event,),
    )


def test_writer_uses_provider_length_and_five_minute_content_deadline(
    tmp_path: Path,
) -> None:
    registry = ProfileRegistry(tmp_path / "models.json")
    registry.upsert_profile(
        _profile(
            id="writer",
            task_type="writer",
            model_ref="test-provider/test-writer",
            max_output_tokens=None,
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
    assert request.output_schema is None
    assert request.max_output_tokens is None
    assert (
        request.first_content_timeout_seconds
        == WRITER_FIRST_CONTENT_TIMEOUT_SECONDS
        == 300
    )
    assert request.timeout_seconds == 90
    assert gateway.contexts[0] is not None
    assert gateway.contexts[0].project_id == "fog-harbor"
    assert gateway.contexts[0].stage == "writer"


def test_writer_ignores_profile_token_limit(tmp_path: Path) -> None:
    registry = ProfileRegistry(tmp_path / "models.json")
    registry.upsert_profile(
        _profile(
            id="writer",
            task_type="writer",
            model_ref="test-provider/test-writer",
            max_output_tokens=4096,
            timeout_seconds=90,
        )
    )
    gateway = RecordingGateway(registry)
    agent = GatewayManuscriptAgent(gateway)  # type: ignore[arg-type]

    asyncio.run(
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

    request = gateway.requests[0]
    assert request.max_output_tokens is None
    assert request.output_token_limit == "provider"


def test_writer_markdown_is_parsed_locally() -> None:
    output = parse_writer_markdown("# 潮声\n\n第一段。\n\n第二段。")

    assert output.title == "潮声"
    assert output.body == "第一段。\n\n第二段。"


def test_editor_model_returns_only_summary_and_issues(tmp_path: Path) -> None:
    registry = ProfileRegistry(tmp_path / "models.json")
    registry.upsert_profile(
        _profile(
            id="editor",
            task_type="editor",
            model_ref="test-provider/test-editor",
            max_output_tokens=4096,
        )
    )
    gateway = RecordingGateway(registry)
    agent = GatewayManuscriptAgent(gateway)  # type: ignore[arg-type]
    writer_source = _source()

    output = asyncio.run(
        agent.review(
            EditorContext(
                source=writer_source.source,
                events=writer_source.events,
                memories=(),
            ),
            title="The Tunnel",
            body="Chen discovers a hidden tunnel.",
        )
    )

    assert output.review.passed is False
    assert output.unsupported_facts == (
        "The source does not establish a hidden tunnel.",
    )
    assert output.review.issues[0].severity == "blocking"
    schema = json.loads(gateway.requests[0].output_schema or "{}")
    assert set(schema["properties"]) == {"summary", "issues"}
    serialized = json.dumps(schema)
    assert "passed" not in serialized
    assert "severity" not in serialized
    assert "ReviewResult" not in serialized
