import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Self

from pydantic import Field, ValidationError, model_validator

from story_engine.domain.errors import DomainError
from story_engine.domain.models import (
    Character,
    DomainModel,
    Fact,
    FactCandidate,
    ReviewIssue,
    ReviewResult,
    WorldState,
)
from story_engine.models.contracts import Message, ModelRequest
from story_engine.models.gateway import ModelGateway
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
        min_length=1,
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
    facts: tuple[FactCandidate, ...] = Field(min_length=1)
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
    facts: tuple[FactCandidate, ...] = ()
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
        creative_direction = (self.title, self.genre, self.theme, self.tone)
        if not all(value.strip() for value in creative_direction):
            missing.append("创作方向")
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


class SubmissionConversationRequest(DomainModel):
    draft: SubmissionDraft
    messages: tuple[Message, ...] = Field(min_length=1, max_length=40)

    @model_validator(mode="after")
    def history_is_user_controlled(self) -> Self:
        if any(message.role == "system" for message in self.messages):
            raise ValueError("system messages are not accepted from submission clients")
        if self.messages[-1].role != "user":
            raise ValueError("last submission message must be user")
        return self


class SubmissionModelOutput(DomainModel):
    reply: str = Field(min_length=1, max_length=8_000)
    draft: SubmissionDraft
    review: ReviewResult

    @model_validator(mode="after")
    def review_uses_submission_mode(self) -> Self:
        if self.review.mode != "submission_review":
            raise ValueError("submission discussion requires submission_review mode")
        return self


class SubmissionConversationResponse(DomainModel):
    reply: str
    draft: SubmissionDraft
    review: ReviewResult
    runnable: bool
    missing_requirements: tuple[str, ...]


class SubmissionDiscussionService:
    def __init__(self, model_gateway: ModelGateway) -> None:
        self.model_gateway = model_gateway

    async def respond(
        self,
        request: SubmissionConversationRequest,
    ) -> SubmissionConversationResponse:
        fact_boundary_example = {
            "facts": [
                FactCandidate(
                    id="fact:public-example",
                    statement="所有角色都知道的公共事实。",
                    visibility="public",
                ).model_dump(mode="json"),
                FactCandidate(
                    id="fact:secret-example",
                    statement="只有示例角色甲知道的秘密。",
                    visibility="secret",
                    known_by=("example-a",),
                ).model_dump(mode="json"),
            ],
            "character_fact_links": [
                {
                    "character_id": "example-a",
                    "known_fact_ids": ["fact:secret-example"],
                },
                {"character_id": "example-b", "known_fact_ids": []},
            ],
        }
        example_output = SubmissionModelOutput(
            reply="我会根据你的要求更新设定, 并指出仍需补充的内容。",
            draft=request.draft,
            review=ReviewResult(
                mode="submission_review",
                passed=False,
                summary="初始设定仍需补充。",
            ),
        )
        system_prompt = (
            "You are the Story Engine submission Editor. Discuss only creative "
            "direction, world rules, two to four initial active characters, and "
            "the concrete initial situation. Do not create an outline or future "
            "plot. Update the supplied SubmissionDraft, keep its id unchanged, "
            "give every fact a concrete statement and visibility, give every "
            "character an explicit private fact boundary and current goal, and "
            "return exactly the requested JSON schema. Keep all text concise. "
            "Fact knowledge boundaries are strict: public facts must use an empty "
            "known_by array and must never appear in any character's known_fact_ids. "
            "Private or secret facts must list every knowing character id in known_by, "
            "and those same characters must list the fact id in known_fact_ids. "
            "FACT KNOWLEDGE BOUNDARY EXAMPLE: "
            f"{json.dumps(fact_boundary_example, ensure_ascii=False)} "
            "The following example demonstrates the required JSON output shape; "
            "update its values from the conversation. EXAMPLE JSON OUTPUT: "
            f"{json.dumps(example_output.model_dump(mode='json'), ensure_ascii=False)} "
            "Current draft: "
            f"{json.dumps(request.draft.model_dump(mode='json'), ensure_ascii=False)}"
        )
        response = await self.model_gateway.complete(
            ModelRequest(
                profile_id="editor",
                task_type="editor",
                messages=(
                    Message(role="system", content=system_prompt),
                    *request.messages,
                ),
                output_schema=json.dumps(
                    SubmissionModelOutput.model_json_schema(),
                    ensure_ascii=False,
                ),
                max_output_tokens=8192,
                timeout_seconds=120,
                temperature=0.2,
            )
        )
        output = SubmissionModelOutput.model_validate(response.parsed_output)
        if output.draft.id != request.draft.id:
            raise ValueError("submission Editor cannot change the project id")

        missing = output.draft.missing_requirements()
        review = output.review
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
                issues=(*review.issues, *deterministic_issues),
            )
        runnable = output.draft.to_package() is not None and review.passed
        return SubmissionConversationResponse(
            reply=output.reply,
            draft=output.draft,
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
        return ProjectStore(self.projects_root / package.id).create(seed)

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
            FactCandidate(
                id="fact:lighthouse-controls-night-navigation",
                statement="灯塔控制雾港的夜间航行。",
                visibility="public",
            ),
            FactCandidate(
                id="fact:storm-requires-navigation-light",
                statement="暴风雨时船只必须依赖灯塔或备用航标。",
                visibility="public",
            ),
            FactCandidate(
                id="secret:chen-father-disappearance",
                statement="陈默的父亲在灯塔附近失踪。",
                visibility="secret",
                known_by=("chen-mo",),
            ),
            FactCandidate(
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
