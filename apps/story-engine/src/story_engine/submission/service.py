from pathlib import Path
from typing import Self

from pydantic import Field, model_validator

from story_engine.domain.errors import DomainError
from story_engine.domain.models import Character, DomainModel, WorldState
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
    known_fact_ids: tuple[str, ...] = Field(min_length=1)
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
    public_fact_ids: tuple[str, ...] = Field(min_length=1)
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
        private_facts = [
            fact_id
            for character in self.characters
            for fact_id in character.known_fact_ids
        ]
        if len(private_facts) != len(set(private_facts)):
            raise ValueError("private facts must belong to exactly one character")
        if set(private_facts) & set(self.public_fact_ids):
            raise ValueError("private facts cannot also be public")
        return self


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
                public_fact_ids=package.public_fact_ids,
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
        public_fact_ids=(
            "fact:lighthouse-controls-night-navigation",
            "fact:storm-requires-navigation-light",
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
