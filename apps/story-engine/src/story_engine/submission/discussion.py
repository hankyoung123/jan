"""Model message adapter for the initial setting discussion workflow."""

import json
import time
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from story_engine.domain.message import ModelMessageContext
from story_engine.domain.models import ReviewIssue, ReviewResult
from story_engine.models.contracts import Message, ModelRequest
from story_engine.models.gateway import ModelGateway
from story_engine.submission.reducer import (
    character_proposals,
    fact_proposals,
    reduce_submission_draft,
)

if TYPE_CHECKING:
    from story_engine.submission.service import (
        SubmissionConversationRequest,
        SubmissionConversationResponse,
    )


class SubmissionDiscussionService:
    """Translate user-controlled messages to one semantic draft delta."""

    def __init__(self, model_gateway: ModelGateway) -> None:
        self.model_gateway = model_gateway

    async def respond(
        self,
        request: "SubmissionConversationRequest",
    ) -> "SubmissionConversationResponse":
        from story_engine.submission.service import (
            SubmissionConversationResponse,
            SubmissionDraft,
            SubmissionDraftDelta,
            SubmissionMessage,
            SubmissionMessageMetadata,
            SubmissionModelOutput,
            SubmissionReasoningPart,
            SubmissionTextPart,
            fog_harbor_submission,
        )

        example_draft = SubmissionDraft.from_package(fog_harbor_submission())
        example_output = SubmissionModelOutput(
            reply="我会根据你的要求更新设定, 并指出仍需补充的内容。",
            delta=SubmissionDraftDelta(
                title=example_draft.title,
                genre=example_draft.genre,
                theme=example_draft.theme,
                tone=example_draft.tone,
                world_rules=example_draft.world_rules,
                facts=fact_proposals(example_draft),
                characters=character_proposals(example_draft),
                initial_time=example_draft.initial_time,
                initial_location=example_draft.initial_location,
                initial_incident=example_draft.initial_incident,
                pressures=example_draft.pressures,
            ),
        )
        protocol = (
            "Immutable protocol: do not create an outline or future plot; return the "
            "supplied JSON schema and preserve strict fact knowledge boundaries."
        )
        task_context = (
            "Discuss only creative "
            "direction, world rules, two to four initial active characters, and "
            "the concrete initial situation. Do not create an outline or future "
            "plot. Return only changed draft fields inside delta; omitted fields "
            "retain "
            "their current values. facts and characters replace those arrays when "
            "present. Give every fact a concrete statement and visibility and every "
            "character a current goal. Do not return any IDs, Review, passed, "
            "severity, "
            "or runnable fields. For fact known_by, use zero-based indexes into the "
            "effective characters array. Public facts require an empty known_by array; "
            "private or secret facts require every knowing character index. The local "
            "runtime generates IDs and both directions of knowledge links. "
            "The following example demonstrates the required JSON output shape; "
            "update its values from the conversation. EXAMPLE JSON OUTPUT: "
            f"{json.dumps(example_output.model_dump(mode='json'), ensure_ascii=False)} "
            "Current draft: "
            f"{json.dumps(request.draft.model_dump(mode='json'), ensure_ascii=False)}"
        )
        profile = self.model_gateway.registry.get_profile("submission_editor")
        message_id = f"call:{uuid.uuid4().hex}"
        started = time.monotonic()
        response = await self.model_gateway.complete(
            ModelRequest(
                profile_id="submission_editor",
                task_type="submission_editor",
                messages=(
                    Message(role="system", content=protocol),
                    Message(role="system", content=profile.default_system_prompt),
                    Message(role="system", content=task_context),
                    *(
                        message.to_model_message()
                        for message in request.active_messages()
                    ),
                ),
                output_schema=json.dumps(
                    SubmissionModelOutput.model_json_schema(),
                    ensure_ascii=False,
                ),
                max_output_tokens=profile.max_output_tokens,
                output_token_limit=(
                    "provider" if profile.max_output_tokens is None else "profile"
                ),
                timeout_seconds=profile.timeout_seconds,
                temperature=profile.temperature,
                reasoning_effort=profile.reasoning_effort,
            ),
            context=ModelMessageContext(
                project_id=request.draft.id,
                message_id=message_id,
                agent_name=profile.name,
                task_label="投稿讨论",
                stage="submission",
            ),
        )
        duration = max(0.0, time.monotonic() - started)
        output = SubmissionModelOutput.model_validate(response.parsed_output)
        draft = reduce_submission_draft(request.draft, output.delta)
        missing = draft.missing_requirements()
        runnable = draft.to_package() is not None
        if missing:
            deterministic_issues = tuple(
                ReviewIssue(
                    code="submission_missing_requirement",
                    message=f"投稿仍缺少: {requirement}",
                    severity="blocking",
                )
                for requirement in missing
            )
            review = ReviewResult(
                mode="submission_review",
                passed=False,
                summary="初始设定包尚未达到可运行条件。",
                issues=deterministic_issues,
            )
        else:
            review = ReviewResult(
                mode="submission_review",
                passed=True,
                summary="初始设定包已满足本地运行条件。",
            )
        response_group_id = request.response_group_id or message_id
        version_index = 1 + sum(
            1
            for message in request.messages
            if message.role == "assistant"
            and (message.metadata.versionGroupId or message.id) == response_group_id
        )
        return SubmissionConversationResponse(
            message=SubmissionMessage(
                id=message_id,
                role="assistant",
                parts=(
                    *(
                        (
                            SubmissionReasoningPart(
                                text=response.reasoning_content,
                            ),
                        )
                        if response.reasoning_content
                        else ()
                    ),
                    SubmissionTextPart(text=output.reply),
                ),
                metadata=SubmissionMessageMetadata(
                    callId=message_id,
                    agentType=profile.agent_type,
                    agentName=profile.name,
                    taskLabel="投稿讨论",
                    model=response.model_ref,
                    duration=duration,
                    promptTokens=response.usage.prompt_tokens,
                    completionTokens=response.usage.completion_tokens,
                    createdAt=datetime.now(UTC),
                    versionGroupId=response_group_id,
                    versionIndex=version_index,
                ),
            ),
            draft=draft,
            review=review,
            runnable=runnable,
            missing_requirements=missing,
        )
