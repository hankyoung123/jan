import hashlib
import json
import re
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, JsonValue, ValidationError, model_validator

from story_engine.domain.errors import DomainError
from story_engine.domain.message import ModelMessageContext
from story_engine.domain.models import (
    Character,
    DomainModel,
    Fact,
    InitialFact,
    ReviewIssue,
    ReviewResult,
    WorldState,
)
from story_engine.models.contracts import (
    ImageMessagePart,
    ImageUrl,
    Message,
    ModelRequest,
    TextMessagePart,
)
from story_engine.models.gateway import ModelGateway
from story_engine.wiki.store import WikiStore
from story_engine.workspace.atomic import atomic_write_text
from story_engine.workspace.documents import dump_json_envelope, load_json_envelope
from story_engine.workspace.lock import ProjectLock
from story_engine.workspace.project_store import (
    ProjectSeed,
    ProjectSnapshot,
    ProjectStore,
)


class SubmissionNotRunnableError(DomainError):
    """Raised when an initial setting package cannot start a story."""


class SubmissionCharacter(DomainModel):
    id: str = Field(min_length=1, pattern=r"^[a-z0-9][a-z0-9-]*$")
    display_name: str = Field(min_length=1)
    identity: str = Field(min_length=1)
    core_desire: str = Field(min_length=1)
    current_goal: str = Field(min_length=1)
    known_fact_ids: tuple[str, ...] = Field(
        description=(
            "Only private or secret fact ids belong here. A fact id must appear "
            "exactly for the characters listed in that fact's known_by array; "
            "never include public fact ids."
        ),
    )
    location: str = Field(min_length=1)
    emotional_state: str | None = None
    resources: tuple[str, ...] = ()


class SubmissionPackage(DomainModel):
    id: str = Field(min_length=1, pattern=r"^[a-z0-9][a-z0-9-]*$")
    title: str = Field(min_length=1)
    genre: str = Field(min_length=1)
    theme: str = Field(min_length=1)
    tone: str = Field(min_length=1)
    world_rules: tuple[str, ...] = Field(min_length=1)
    facts: tuple[InitialFact, ...] = Field(min_length=1)
    characters: tuple[SubmissionCharacter, ...] = Field(min_length=2, max_length=4)
    initial_time: str = Field(min_length=1)
    initial_location: str = Field(min_length=1)
    initial_incident: str = Field(min_length=1)
    pressures: tuple[str, ...] = ()

    @model_validator(mode="after")
    def has_unique_character_and_fact_boundaries(self) -> Self:
        character_ids = [character.id for character in self.characters]
        if len(character_ids) != len(set(character_ids)):
            raise ValueError("submission character ids must be unique")
        facts_by_id = {fact.id: fact for fact in self.facts}
        if len(facts_by_id) != len(self.facts):
            raise ValueError("submission fact ids must be unique")
        referenced = {
            fact_id
            for character in self.characters
            for fact_id in character.known_fact_ids
        }
        public = {fact.id for fact in self.facts if fact.visibility == "public"}
        if referenced | public != set(facts_by_id):
            raise ValueError("all submission facts must have a knowledge boundary")
        for character in self.characters:
            for fact_id in character.known_fact_ids:
                fact = facts_by_id.get(fact_id)
                if fact is None or fact.visibility == "public":
                    raise ValueError(
                        "character knowledge must reference restricted facts"
                    )
                if character.id not in fact.known_by:
                    raise ValueError("character knowledge must match fact known_by")
        for fact in self.facts:
            actual = {
                character.id
                for character in self.characters
                if fact.id in character.known_fact_ids
            }
            if fact.visibility != "public" and actual != set(fact.known_by):
                raise ValueError(
                    "restricted fact known_by must match character knowledge"
                )
        return self


class SubmissionDraft(DomainModel):
    """Non-canonical setting package assembled during submission discussion."""

    id: str = Field(min_length=1, pattern=r"^[a-z0-9][a-z0-9-]*$")
    title: str = ""
    genre: str = ""
    theme: str = ""
    tone: str = ""
    world_rules: tuple[str, ...] = ()
    facts: tuple[InitialFact, ...] = ()
    characters: tuple[SubmissionCharacter, ...] = Field(default=(), max_length=4)
    initial_time: str = ""
    initial_location: str = ""
    initial_incident: str = ""
    pressures: tuple[str, ...] = ()

    @classmethod
    def from_package(cls, package: SubmissionPackage) -> "SubmissionDraft":
        return cls.model_validate(package.model_dump(mode="json"))

    def missing_requirements(self) -> tuple[str, ...]:
        missing: list[str] = []
        for label, value in (
            ("标题", self.title),
            ("类型", self.genre),
            ("主题", self.theme),
            ("基调", self.tone),
        ):
            if not value.strip():
                missing.append(label)
        if not self.world_rules or not any(
            fact.visibility == "public" for fact in self.facts
        ):
            missing.append("世界规则与公共事实")
        if not 2 <= len(self.characters) <= 4:
            missing.append("初始角色 (2-4 个)")
        if not all(
            value.strip()
            for value in (
                self.initial_time,
                self.initial_location,
                self.initial_incident,
            )
        ):
            missing.append("初始时间、地点和起始事件")
        distinct_goals = {character.current_goal for character in self.characters}
        if not self.pressures and len(distinct_goals) < 2:
            missing.append("世界压力或角色目标冲突")

        try:
            if not self.missing_core_requirements():
                SubmissionPackage.model_validate(self.model_dump(mode="json"))
        except ValidationError:
            missing.append("角色知识边界")
        return tuple(missing)

    def missing_core_requirements(self) -> bool:
        return not (
            all((self.title, self.genre, self.theme, self.tone))
            and self.world_rules
            and self.facts
            and 2 <= len(self.characters) <= 4
            and all((self.initial_time, self.initial_location, self.initial_incident))
        )

    def to_package(self) -> SubmissionPackage | None:
        if self.missing_requirements():
            return None
        try:
            return SubmissionPackage.model_validate(self.model_dump(mode="json"))
        except ValidationError:
            return None


class SubmissionTextPart(DomainModel):
    type: Literal["text"] = "text"
    text: str = Field(min_length=1, max_length=262_144)


class SubmissionReasoningPart(DomainModel):
    type: Literal["reasoning"] = "reasoning"
    text: str = Field(min_length=1, max_length=262_144)


class SubmissionFilePart(DomainModel):
    type: Literal["file"] = "file"
    mediaType: str = Field(pattern=r"^image/(?:jpeg|png|webp|gif)$")
    url: str = Field(min_length=1, max_length=1_048_576)
    filename: str | None = Field(default=None, max_length=255)


class SubmissionToolPart(DomainModel):
    type: str = Field(pattern=r"^tool-[a-zA-Z0-9_-]+$")
    state: str = Field(min_length=1, max_length=100)
    toolCallId: str = Field(min_length=1, max_length=200)
    input: JsonValue = None
    output: JsonValue = None
    errorText: str | None = Field(default=None, max_length=8_000)


SubmissionMessagePart = (
    SubmissionTextPart
    | SubmissionReasoningPart
    | SubmissionFilePart
    | SubmissionToolPart
)


class SubmissionMessageMetadata(DomainModel):
    callId: str = Field(min_length=1, max_length=200)
    agentType: str = Field(min_length=1, max_length=100)
    agentName: str = Field(min_length=1, max_length=200)
    taskLabel: str = Field(min_length=1, max_length=200)
    model: str | None = Field(default=None, max_length=200)
    duration: float | None = Field(default=None, ge=0)
    promptTokens: int = Field(default=0, ge=0)
    completionTokens: int = Field(default=0, ge=0)
    outputStatus: Literal["completed", "failed"] = "completed"
    error: str | None = Field(default=None, max_length=8_000)
    createdAt: datetime
    versionGroupId: str | None = Field(default=None, max_length=200)
    versionIndex: int = Field(default=1, ge=1)
    active: bool = True
    stopped: bool = False


class SubmissionMessage(DomainModel):
    id: str = Field(min_length=1, max_length=200)
    role: Literal["user", "assistant"]
    parts: tuple[SubmissionMessagePart, ...] = Field(min_length=1, max_length=64)
    metadata: SubmissionMessageMetadata

    @model_validator(mode="after")
    def parts_match_role(self) -> Self:
        if self.role == "user" and any(
            isinstance(part, (SubmissionReasoningPart, SubmissionToolPart))
            for part in self.parts
        ):
            raise ValueError("user submission messages only support text and files")
        if not any(
            isinstance(part, (SubmissionTextPart, SubmissionFilePart))
            for part in self.parts
        ):
            raise ValueError("submission messages require text or file content")
        return self

    def to_model_message(self) -> Message:
        content: list[TextMessagePart | ImageMessagePart] = []
        for part in self.parts:
            if isinstance(part, SubmissionTextPart):
                content.append(TextMessagePart(text=part.text))
            elif isinstance(part, SubmissionFilePart):
                content.append(ImageMessagePart(image_url=ImageUrl(url=part.url)))
        return Message(role=self.role, content=tuple(content))


class SubmissionConversationRequest(DomainModel):
    draft: SubmissionDraft
    messages: tuple[SubmissionMessage, ...] = Field(min_length=1, max_length=40)
    response_group_id: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def history_is_user_controlled(self) -> Self:
        active = tuple(message for message in self.messages if message.metadata.active)
        if not active or active[-1].role != "user":
            raise ValueError("last submission message must be user")
        groups: dict[str, int] = {}
        for message in self.messages:
            if message.role != "assistant":
                continue
            group_id = message.metadata.versionGroupId or message.id
            groups.setdefault(group_id, 0)
            if message.metadata.active:
                groups[group_id] += 1
        if any(
            count != 1 and not (group_id == self.response_group_id and count == 0)
            for group_id, count in groups.items()
        ):
            raise ValueError(
                "each submission response group requires one active version"
            )
        return self

    def active_messages(self) -> tuple[SubmissionMessage, ...]:
        return tuple(message for message in self.messages if message.metadata.active)


class SubmissionConversationUpdate(DomainModel):
    messages: tuple[SubmissionMessage, ...] = Field(min_length=1, max_length=40)


class SubmissionCharacterProposal(DomainModel):
    display_name: str = Field(min_length=1)
    identity: str = Field(min_length=1)
    core_desire: str = Field(min_length=1)
    current_goal: str = Field(min_length=1)
    location: str = Field(min_length=1)
    emotional_state: str | None = None
    resources: tuple[str, ...] = ()


class SubmissionFactProposal(DomainModel):
    statement: str = Field(min_length=1)
    visibility: Literal["public", "private", "secret"]
    known_by: tuple[int, ...] = Field(
        default=(),
        description=(
            "Zero-based indexes into the effective characters array. Must be empty "
            "for public facts and non-empty for private or secret facts."
        ),
    )
    supersedes_ref: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def knowledge_refs_match_visibility(self) -> Self:
        if any(item < 0 for item in self.known_by):
            raise ValueError("fact character refs must be non-negative")
        if len(self.known_by) != len(set(self.known_by)):
            raise ValueError("fact character refs must be unique")
        if self.visibility == "public" and self.known_by:
            raise ValueError("public fact cannot have character refs")
        if self.visibility != "public" and not self.known_by:
            raise ValueError("restricted fact requires character refs")
        return self


class SubmissionDraftDelta(DomainModel):
    title: str | None = None
    genre: str | None = None
    theme: str | None = None
    tone: str | None = None
    world_rules: tuple[str, ...] | None = None
    facts: tuple[SubmissionFactProposal, ...] | None = None
    characters: tuple[SubmissionCharacterProposal, ...] | None = Field(
        default=None,
        max_length=4,
    )
    initial_time: str | None = None
    initial_location: str | None = None
    initial_incident: str | None = None
    pressures: tuple[str, ...] | None = None


class SubmissionModelOutput(DomainModel):
    reply: str = Field(min_length=1, max_length=8_000)
    delta: SubmissionDraftDelta


def _local_identifier(text: str, *, prefix: str, index: int) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.casefold()).strip("-")
    suffix = slug or hashlib.sha256(text.encode("utf-8")).hexdigest()[:10]
    return f"{prefix}{suffix or index + 1}"


def _character_proposals(
    draft: SubmissionDraft,
) -> tuple[SubmissionCharacterProposal, ...]:
    return tuple(
        SubmissionCharacterProposal(
            display_name=item.display_name,
            identity=item.identity,
            core_desire=item.core_desire,
            current_goal=item.current_goal,
            location=item.location,
            emotional_state=item.emotional_state,
            resources=item.resources,
        )
        for item in draft.characters
    )


def _fact_proposals(
    draft: SubmissionDraft,
) -> tuple[SubmissionFactProposal, ...]:
    character_refs = {item.id: index for index, item in enumerate(draft.characters)}
    fact_refs = {item.id: index for index, item in enumerate(draft.facts)}
    return tuple(
        SubmissionFactProposal(
            statement=item.statement,
            visibility=item.visibility,
            known_by=tuple(character_refs[owner] for owner in item.known_by),
            supersedes_ref=(
                fact_refs[item.supersedes_fact_id]
                if item.supersedes_fact_id is not None
                else None
            ),
        )
        for item in draft.facts
    )


def apply_submission_delta(
    draft: SubmissionDraft,
    delta: SubmissionDraftDelta,
) -> SubmissionDraft:
    character_inputs = (
        delta.characters
        if delta.characters is not None
        else _character_proposals(draft)
    )
    existing_characters_by_name = {
        item.display_name.casefold(): item for item in draft.characters
    }
    character_ids: list[str] = []
    used_character_ids: set[str] = set()
    for character_index, character_input in enumerate(character_inputs):
        existing_character = existing_characters_by_name.get(
            character_input.display_name.casefold()
        )
        character_id = existing_character.id if existing_character is not None else None
        if character_id is None and character_index < len(draft.characters):
            positional_character_id = draft.characters[character_index].id
            if positional_character_id not in used_character_ids:
                character_id = positional_character_id
        character_id = character_id or _local_identifier(
            character_input.display_name,
            prefix="character-",
            index=character_index,
        )
        character_id_base = character_id
        character_id_suffix = 2
        while character_id in used_character_ids:
            character_id = f"{character_id_base}-{character_id_suffix}"
            character_id_suffix += 1
        used_character_ids.add(character_id)
        character_ids.append(character_id)

    facts: list[InitialFact]
    if delta.facts is None:
        facts = list(draft.facts)
    else:
        fact_inputs = delta.facts
        existing_facts_by_statement = {
            item.statement.casefold(): item for item in draft.facts
        }
        fact_ids: list[str] = []
        used_fact_ids: set[str] = set()
        for fact_index, fact_input in enumerate(fact_inputs):
            existing_fact = existing_facts_by_statement.get(
                fact_input.statement.casefold()
            )
            fact_id = existing_fact.id if existing_fact is not None else None
            if fact_id is None and fact_index < len(draft.facts):
                positional_fact_id = draft.facts[fact_index].id
                if positional_fact_id not in used_fact_ids:
                    fact_id = positional_fact_id
            fact_id = fact_id or _local_identifier(
                fact_input.statement,
                prefix="fact:",
                index=fact_index,
            )
            fact_id_base = fact_id
            fact_id_suffix = 2
            while fact_id in used_fact_ids:
                fact_id = f"{fact_id_base}-{fact_id_suffix}"
                fact_id_suffix += 1
            used_fact_ids.add(fact_id)
            fact_ids.append(fact_id)

        facts = []
        for fact_index, fact_input in enumerate(fact_inputs):
            unknown_refs = set(fact_input.known_by) - set(range(len(character_ids)))
            if unknown_refs:
                raise ValueError(
                    "submission fact has unknown character refs: "
                    f"{sorted(unknown_refs)}"
                )
            if (
                fact_input.supersedes_ref is not None
                and not 0 <= fact_input.supersedes_ref < len(fact_ids)
            ):
                raise ValueError(
                    "submission fact has unknown supersedes_ref: "
                    f"{fact_input.supersedes_ref}"
                )
            if fact_input.supersedes_ref == fact_index:
                raise ValueError("submission fact cannot supersede itself")
            facts.append(
                InitialFact(
                    id=fact_ids[fact_index],
                    statement=fact_input.statement,
                    visibility=fact_input.visibility,
                    known_by=tuple(character_ids[ref] for ref in fact_input.known_by),
                    supersedes_fact_id=(
                        fact_ids[fact_input.supersedes_ref]
                        if fact_input.supersedes_ref is not None
                        else None
                    ),
                )
            )

    known_facts_by_character = {
        character_id: tuple(fact.id for fact in facts if character_id in fact.known_by)
        for character_id in character_ids
    }
    characters = tuple(
        SubmissionCharacter(
            id=character_ids[index],
            display_name=character_input.display_name,
            identity=character_input.identity,
            core_desire=character_input.core_desire,
            current_goal=character_input.current_goal,
            known_fact_ids=known_facts_by_character[character_ids[index]],
            location=character_input.location,
            emotional_state=character_input.emotional_state,
            resources=character_input.resources,
        )
        for index, character_input in enumerate(character_inputs)
    )
    updates = {
        name: value
        for name, value in delta.model_dump().items()
        if value is not None and name not in {"facts", "characters"}
    }
    return SubmissionDraft(
        **{
            **draft.model_dump(),
            **updates,
            "facts": tuple(facts),
            "characters": characters,
        }
    )


class SubmissionConversationResponse(DomainModel):
    message: SubmissionMessage
    draft: SubmissionDraft
    review: ReviewResult
    runnable: bool
    missing_requirements: tuple[str, ...]


class SubmissionStatus(DomainModel):
    runnable: bool
    missing_requirements: tuple[str, ...]
    review: ReviewResult
    finalized: bool = False
    updated_at: datetime


class SubmissionWorkspaceState(DomainModel):
    draft: SubmissionDraft
    messages: tuple[SubmissionMessage, ...]
    status: SubmissionStatus


class SubmissionWorkspaceStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.directory = root / "submission"
        self.conversation_path = self.directory / "conversation.jsonl"
        self.draft_path = self.directory / "draft.md"
        self.status_path = self.directory / "status.json"

    def exists(self) -> bool:
        return (
            self.conversation_path.is_file()
            and self.draft_path.is_file()
            and self.status_path.is_file()
        )

    def load(self) -> SubmissionWorkspaceState:
        draft = SubmissionDraft.model_validate(
            load_json_envelope(
                self.draft_path,
                schema="story-engine/submission-draft/v1",
            )
        )
        messages = tuple(
            SubmissionMessage.model_validate_json(line)
            for line in self.conversation_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
        if not messages:
            raise ValueError("submission conversation is empty")
        status = SubmissionStatus.model_validate_json(
            self.status_path.read_text(encoding="utf-8")
        )
        return SubmissionWorkspaceState(
            draft=draft,
            messages=messages,
            status=status,
        )

    def save(self, state: SubmissionWorkspaceState) -> None:
        if state.draft.id != self.root.name:
            raise ValueError("submission draft does not match workspace id")
        groups: dict[str, int] = {}
        for message in state.messages:
            if message.role != "assistant":
                continue
            group_id = message.metadata.versionGroupId or message.id
            groups.setdefault(group_id, 0)
            if message.metadata.active:
                groups[group_id] += 1
        if any(count != 1 for count in groups.values()):
            raise ValueError(
                "each persisted submission response group requires one active version"
            )
        conversation = "".join(
            f"{message.model_dump_json()}\n" for message in state.messages
        )
        draft_body = dump_json_envelope(
            schema="story-engine/submission-draft/v1",
            title=state.draft.title or "Untitled Submission",
            payload=state.draft.model_dump(mode="json"),
            metadata={"project_id": state.draft.id},
            body=(
                f"# {state.draft.title or 'Untitled Submission'}\n\n"
                f"- Genre: {state.draft.genre or 'Unspecified'}\n"
                f"- Theme: {state.draft.theme or 'Unspecified'}\n"
                f"- Tone: {state.draft.tone or 'Unspecified'}"
            ),
        )
        with ProjectLock(self.root):
            atomic_write_text(self.conversation_path, conversation)
            atomic_write_text(self.draft_path, draft_body)
            atomic_write_text(
                self.status_path,
                f"{state.status.model_dump_json(indent=2)}\n",
            )

    def mark_finalized(self) -> None:
        state = self.load()
        self.save(
            state.model_copy(
                update={
                    "status": state.status.model_copy(
                        update={"finalized": True, "updated_at": datetime.now(UTC)}
                    )
                }
            )
        )


class SubmissionDiscussionService:
    def __init__(self, model_gateway: ModelGateway) -> None:
        self.model_gateway = model_gateway

    async def respond(
        self,
        request: SubmissionConversationRequest,
    ) -> SubmissionConversationResponse:
        example_draft = SubmissionDraft.from_package(fog_harbor_submission())
        example_output = SubmissionModelOutput(
            reply="我会根据你的要求更新设定, 并指出仍需补充的内容。",
            delta=SubmissionDraftDelta(
                title=example_draft.title,
                genre=example_draft.genre,
                theme=example_draft.theme,
                tone=example_draft.tone,
                world_rules=example_draft.world_rules,
                facts=_fact_proposals(example_draft),
                characters=_character_proposals(example_draft),
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
        draft = apply_submission_delta(request.draft, output.delta)
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


class SubmissionService:
    def __init__(self, projects_root: Path) -> None:
        self.projects_root = projects_root

    def finalize(self, package: SubmissionPackage) -> ProjectSnapshot:
        self._ensure_runnable(package)
        seed = ProjectSeed(
            id=package.id,
            title=package.title,
            genre=package.genre,
            theme=package.theme,
            tone=package.tone,
            world=WorldState(
                current_time=package.initial_time,
                current_location=package.initial_location,
                rules=package.world_rules,
                active_pressures=package.pressures,
                public_fact_ids=tuple(
                    fact.id for fact in package.facts if fact.visibility == "public"
                ),
                world_variables={
                    "initial_incident": package.initial_incident,
                    "round": 0,
                },
            ),
            characters=tuple(
                Character(
                    id=item.id,
                    display_name=item.display_name,
                    type="active",
                    identity=item.identity,
                    core_desire=item.core_desire,
                    current_goal=item.current_goal,
                    known_fact_ids=item.known_fact_ids,
                    location=item.location,
                    emotional_state=item.emotional_state,
                    resources=item.resources,
                )
                for item in package.characters
            ),
            facts=tuple(
                Fact(
                    **fact.model_dump(),
                    source_event_id=f"submission:{package.id}",
                    introduced_at=datetime.now(UTC),
                )
                for fact in package.facts
            ),
        )
        root = self.projects_root / package.id
        snapshot = ProjectStore(root).create(seed)
        WikiStore(root, "main").initialize(snapshot)
        return snapshot

    @staticmethod
    def _ensure_runnable(package: SubmissionPackage) -> None:
        distinct_goals = {character.current_goal for character in package.characters}
        if not package.pressures and len(distinct_goals) < 2:
            raise SubmissionNotRunnableError(
                "submission requires world pressure or conflicting character goals"
            )


def fog_harbor_submission() -> SubmissionPackage:
    """Return the deterministic two-character acceptance fixture."""
    return SubmissionPackage(
        id="fog-harbor",
        title="雾港",
        genre="悬疑",
        theme="真相与亲情之间的选择",
        tone="克制、现实、缓慢积压",
        world_rules=(
            "灯塔控制港口夜航",
            "暴风雨时港口必须依赖灯塔或备用航标",
        ),
        facts=(
            InitialFact(
                id="fact:lighthouse-controls-night-navigation",
                statement="灯塔控制雾港的夜间航行。",
                visibility="public",
            ),
            InitialFact(
                id="fact:storm-requires-navigation-light",
                statement="暴风雨时船只必须依赖灯塔或备用航标。",
                visibility="public",
            ),
            InitialFact(
                id="secret:chen-father-disappearance",
                statement="陈默的父亲在灯塔附近失踪。",
                visibility="secret",
                known_by=("chen-mo",),
            ),
            InitialFact(
                id="secret:lin-unfiled-duty-roster",
                statement="林岚保留了一份未归档的值班表。",
                visibility="secret",
                known_by=("lin-lan",),
            ),
        ),
        characters=(
            SubmissionCharacter(
                id="chen-mo",
                display_name="陈默",
                identity="从外地返回雾港的机械工程师",
                core_desire="找到父亲失踪的真相",
                current_goal="查明灯塔熄灭原因",
                known_fact_ids=("secret:chen-father-disappearance",),
                location="灯塔入口",
                emotional_state="紧张但专注",
                resources=("铜钥匙",),
            ),
            SubmissionCharacter(
                id="lin-lan",
                display_name="林岚",
                identity="雾港港务所值班员",
                core_desire="保护进港船只和港务所声誉",
                current_goal="让客船安全进入雾港",
                known_fact_ids=("secret:lin-unfiled-duty-roster",),
                location="港务所",
                emotional_state="警觉",
                resources=("港务电台",),
            ),
        ),
        initial_time="暴风雨前夜",
        initial_location="雾港",
        initial_incident="灯塔突然熄灭",
        pressures=("客船即将进入近港航道",),
    )
