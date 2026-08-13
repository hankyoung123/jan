# ruff: noqa: RUF001

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import Field, JsonValue, ValidationError, model_validator

from story_engine.domain.errors import DomainError
from story_engine.domain.models import (
    DomainModel,
    InitialFact,
    Relationship,
    ReviewResult,
)
from story_engine.models.contracts import (
    ImageMessagePart,
    ImageUrl,
    Message,
    TextMessagePart,
)
from story_engine.submission.discussion import SubmissionDiscussionService
from story_engine.submission.project import SubmissionService
from story_engine.submission.reducer import reduce_submission_draft
from story_engine.workspace.atomic import atomic_write_text
from story_engine.workspace.documents import dump_json_envelope, load_json_envelope
from story_engine.workspace.lock import ProjectLock

__all__ = [
    "SubmissionCharacter",
    "SubmissionCharacterProposal",
    "SubmissionConversationRequest",
    "SubmissionConversationResponse",
    "SubmissionConversationUpdate",
    "SubmissionDiscussionService",
    "SubmissionDraft",
    "SubmissionDraftDelta",
    "SubmissionFactProposal",
    "SubmissionFilePart",
    "SubmissionMessage",
    "SubmissionMessageMetadata",
    "SubmissionMessagePart",
    "SubmissionModelOutput",
    "SubmissionNotRunnableError",
    "SubmissionPackage",
    "SubmissionReasoningPart",
    "SubmissionService",
    "SubmissionStatus",
    "SubmissionTextPart",
    "SubmissionToolPart",
    "SubmissionWorkspaceState",
    "SubmissionWorkspaceStore",
    "fog_harbor_submission",
    "last_ferry_before_submission",
    "reduce_submission_draft",
]


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
    relationships: tuple[Relationship, ...] = ()
    capabilities: tuple[str, ...] = ()
    conditions: tuple[str, ...] = ()
    location: str = Field(min_length=1)
    emotional_state: str | None = None
    resources: tuple[str, ...] = ()

    @model_validator(mode="before")
    @classmethod
    def default_player_display_name(cls, value: Any) -> Any:
        if not isinstance(value, dict) or value.get("id") != "player":
            return value
        normalized = str(value.get("display_name") or "").strip()
        if not normalized:
            return {**value, "display_name": "来访者"}
        if normalized.casefold() in {"你", "user", "human"}:
            raise ValueError("player display_name must be a stable character name")
        return value


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
    scene_text: str = ""
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
        # Scene text belongs to the fixed runtime seed, not the submission editor.
        return cls.model_validate(
            package.model_dump(mode="json", exclude={"scene_text"})
        )

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
    character_ref: str | None = Field(
        default=None,
        pattern=r"^c[0-9]+$",
        description="Stable local character reference from the supplied draft.",
    )
    display_name: str = Field(min_length=1)
    identity: str = Field(min_length=1)
    core_desire: str = Field(min_length=1)
    current_goal: str = Field(min_length=1)
    location: str = Field(min_length=1)
    emotional_state: str | None = None
    resources: tuple[str, ...] = ()


class SubmissionFactProposal(DomainModel):
    fact_ref: str | None = Field(
        default=None,
        pattern=r"^f[0-9]+$",
        description="Stable local fact reference from the supplied draft.",
    )
    statement: str = Field(min_length=1)
    visibility: Literal["public", "private", "secret"]
    known_by: tuple[str | int, ...] = Field(
        default=(),
        description=(
            "Stable character refs such as c0 or c1 from the supplied draft. "
            "Zero-based indexes are accepted only for legacy drafts. Must be empty "
            "for public facts and non-empty for private or secret facts."
        ),
    )
    supersedes_ref: str | int | None = Field(
        default=None,
        description="Stable fact ref such as f0; numeric indexes are legacy only.",
    )

    @model_validator(mode="after")
    def knowledge_refs_match_visibility(self) -> Self:
        for item in self.known_by:
            if isinstance(item, int) and item < 0:
                raise ValueError("fact character refs must be non-negative")
            if isinstance(item, str) and not item.startswith("c"):
                raise ValueError("fact character refs must use c-prefixed refs")
        if isinstance(self.supersedes_ref, int) and self.supersedes_ref < 0:
            raise ValueError("fact supersedes ref must be non-negative")
        if (
            isinstance(self.supersedes_ref, str)
            and not self.supersedes_ref.startswith("f")
        ):
            raise ValueError("fact supersedes ref must use an f-prefixed ref")
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


apply_submission_delta = reduce_submission_draft


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


def last_ferry_before_submission() -> SubmissionPackage:
    """The fixed MVP world used for interactive world-session integration tests."""
    return SubmissionPackage(
        id="last-ferry-before",
        title="末班船之前",
        genre="悬疑",
        theme="事实、信任与错过的代价",
        tone="现实、克制、持续紧迫",
        world_rules=(
            "世界只因已提交的 ResolvedEvent 改变。",
            "门锁、物品归属、人物位置和时间线必须保持因果一致。",
            "末班船将在约四十分钟后离港，角色会依照自己的计划行动。",
        ),
        facts=(
            InitialFact(
                id="fact:stormy-hotel",
                statement="暴雨中的港口旅馆接待着等待末班船的人。",
                visibility="public",
            ),
            InitialFact(
                id="fact:last-ferry-time",
                statement="末班船将在约四十分钟后离港。",
                visibility="public",
            ),
            InitialFact(
                id="fact:player-message",
                statement="陈默收到一条署名林澈、约陈默到旅馆的消息。",
                visibility="secret",
                known_by=("player",),
            ),
            InitialFact(
                id="fact:player-knows-chen-kai",
                statement="陈默认识当地警员陈凯，可以尝试联系他。",
                visibility="private",
                known_by=("player",),
            ),
            InitialFact(
                id="truth:message-sender",
                statement="张野借用林澈遗失的旧手机发出了那条消息。",
                visibility="secret",
                known_by=("zhang-ye",),
            ),
            InitialFact(
                id="truth:why-player-was-called",
                statement="张野想借记者身份制造林澈主动约见的假象。",
                visibility="secret",
                known_by=("zhang-ye",),
            ),
            InitialFact(
                id="truth:lin-concealment",
                statement="林澈隐瞒了她曾替张野保管过一份港口交接记录。",
                visibility="secret",
                known_by=("lin-che",),
            ),
            InitialFact(
                id="truth:zhang-goal",
                statement="张野准备带着被篡改的交接记录搭末班船离开。",
                visibility="secret",
                known_by=("zhang-ye",),
            ),
            InitialFact(
                id="truth:locked-room-use",
                statement="二楼锁房是旅馆废弃的账房，记录曾被临时藏在那里。",
                visibility="secret",
                known_by=("innkeeper",),
            ),
            InitialFact(
                id="truth:room-entry",
                statement="今天傍晚张野进入过二楼锁房，店主从楼梯口看见了他。",
                visibility="secret",
                known_by=("innkeeper",),
            ),
            InitialFact(
                id="truth:key-item-location",
                statement="原始交接记录藏在张野旅行包的夹层里。",
                visibility="secret",
                known_by=("zhang-ye",),
            ),
            InitialFact(
                id="truth:event-timeline",
                statement=(
                    "18:05 林澈发现记录被调包；18:17 张野发出假消息；"
                    "18:31 张野回到旅馆。"
                ),
                visibility="secret",
                known_by=("lin-che", "zhang-ye"),
            ),
            InitialFact(
                id="truth:ferry-connection",
                statement="张野选择末班船，是因为船离港后港口监控的当夜备份会被转移。",
                visibility="secret",
                known_by=("zhang-ye",),
            ),
            InitialFact(
                id="truth:final",
                statement="港口事故并非林澈造成，张野篡改记录是为了掩盖自己的责任。",
                visibility="secret",
                known_by=("zhang-ye",),
            ),
        ),
        characters=(
            SubmissionCharacter(
                id="player",
                display_name="陈默",
                identity="本地调查记者",
                core_desire="弄清旧友求助消息背后的真相",
                current_goal="在末班船离港前查明发生了什么",
                known_fact_ids=(
                    "fact:player-message",
                    "fact:player-knows-chen-kai",
                ),
                location="旅馆一楼大厅",
                emotional_state="警觉",
                resources=("手机", "相机", "记者证", "钱包", "车钥匙"),
                capabilities=("调查采访", "摄影", "熟悉本地港口", "普通驾驶能力"),
                conditions=("右手轻伤",),
                relationships=(
                    Relationship(
                        character_id="lin-che",
                        description="林澈是陈默的旧友。",
                    ),
                ),
            ),
            SubmissionCharacter(
                id="lin-che",
                display_name="林澈",
                identity="陈默在港口工作的旧友",
                core_desire="避免旧事牵连到更多人",
                current_goal="确认张野是否会带着记录离开",
                known_fact_ids=("truth:lin-concealment", "truth:event-timeline"),
                location="旅馆一楼大厅",
                emotional_state="戒备而犹豫",
                resources=("旧手机",),
                relationships=(
                    Relationship(
                        character_id="player",
                        description="陈默是林澈仍愿意信任的旧友。",
                    ),
                ),
            ),
            SubmissionCharacter(
                id="zhang-ye",
                display_name="张野",
                identity="急于离开港口的货运承包人",
                core_desire="在记录被发现前脱身",
                current_goal="赶上末班船并保住旅行包",
                known_fact_ids=(
                    "truth:message-sender",
                    "truth:why-player-was-called",
                    "truth:zhang-goal",
                    "truth:key-item-location",
                    "truth:event-timeline",
                    "truth:ferry-connection",
                    "truth:final",
                ),
                location="旅馆一楼柜台附近",
                emotional_state="克制但随时准备离开",
                resources=("旅行包", "船票"),
                capabilities=("身体强壮", "熟悉港口货运路线"),
            ),
            SubmissionCharacter(
                id="innkeeper",
                display_name="店主",
                identity="经营港口旅馆的店主",
                core_desire="避免警察和媒体把旅馆卷入麻烦",
                current_goal="让今晚的客人尽快离开",
                known_fact_ids=("truth:locked-room-use", "truth:room-entry"),
                location="旅馆柜台",
                emotional_state="不耐烦",
                resources=("二楼备用钥匙",),
            ),
        ),
        initial_time="18:43",
        initial_location="港口旅馆",
        initial_incident="林澈否认发过那条约见消息，张野正在准备离开。",
        scene_text=(
            "雨已经下了很久。\n\n"
            "林澈坐在靠窗的位置。张野站在柜台附近。\n\n"
            "门口的地毯已经湿透，墙上的钟刚刚跳到 18:43。\n\n"
            "林澈看见陈默，没有起身。\n\n"
            "“你怎么来了？”"
        ),
        pressures=("张野会在末班船离港前按自己的计划行动",),
    )
