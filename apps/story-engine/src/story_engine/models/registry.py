import json
from pathlib import Path
from threading import RLock

from pydantic import Field, model_validator

from story_engine.domain.models import DomainModel
from story_engine.models.contracts import AgentProfile, AgentProfilePatch, AgentType
from story_engine.models.errors import (
    ModelConfigurationError,
    ProfileNotFoundError,
)
from story_engine.workspace.atomic import atomic_write_text


class RegistryState(DomainModel):
    schema_version: int = Field(default=5, ge=5)
    profiles: tuple[AgentProfile, ...]

    @model_validator(mode="after")
    def profiles_cover_fixed_agent_types(self) -> "RegistryState":
        profile_ids = [profile.agent_type for profile in self.profiles]
        if len(profile_ids) != len(set(profile_ids)):
            raise ValueError("profile IDs must be unique")
        expected = {
            "actor",
            "game_master",
            "writer",
            "editor",
            "wiki_maintainer",
            "submission_editor",
        }
        if set(profile_ids) != expected:
            raise ValueError(
                "registry must contain exactly the six fixed Agent profiles"
            )
        return self


def default_registry() -> RegistryState:
    profiles = (
        AgentProfile(
            name="角色演员",
            agent_type="actor",
            default_system_prompt="忠实扮演角色, 只依据角色可知信息作出行动。",
            temperature=0.7,
        ),
        AgentProfile(
            name="游戏主持人",
            agent_type="game_master",
            default_system_prompt="公正推进世界状态, 依据行动和既有事实裁定结果。",
            temperature=0.2,
        ),
        AgentProfile(
            name="Wiki 维护者",
            agent_type="wiki_maintainer",
            default_system_prompt="只整理有来源支持的持久知识, 不创造新事实。",
            temperature=0.1,
            max_output_tokens=4096,
            timeout_seconds=120,
        ),
        AgentProfile(
            name="正文编辑",
            agent_type="editor",
            default_system_prompt="核验正文事实依据, 明确指出所有不受来源支持的陈述。",
            temperature=0.1,
        ),
        AgentProfile(
            name="正文作者",
            agent_type="writer",
            default_system_prompt="将允许揭示的历史写成连贯、克制且有文学性的小说正文。",
            temperature=0.8,
            max_output_tokens=None,
            timeout_seconds=300,
        ),
        AgentProfile(
            name="投稿编辑",
            agent_type="submission_editor",
            default_system_prompt=(
                "协助整理可运行的故事设定, 保持事实与角色知识边界一致。"
            ),
            temperature=0.2,
            max_output_tokens=4096,
            timeout_seconds=120,
        ),
    )
    return RegistryState(profiles=profiles)


class ProfileRegistry:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = RLock()

    def load(self) -> RegistryState:
        with self._lock:
            if not self.path.exists():
                return default_registry()
            try:
                source = self.path.read_text(encoding="utf-8")
                payload = json.loads(source)
                return RegistryState.model_validate(payload)
            except (OSError, ValueError) as error:
                raise ModelConfigurationError("model registry is invalid") from error

    def save(self, state: RegistryState) -> RegistryState:
        with self._lock:
            content = json.dumps(
                state.model_dump(mode="json"),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            atomic_write_text(self.path, f"{content}\n")
        return state

    def get_profile(self, profile_id: str) -> AgentProfile:
        for profile in self.load().profiles:
            if profile.agent_type == profile_id:
                return profile
        raise ProfileNotFoundError(f"profile {profile_id!r} does not exist")

    def upsert_profile(self, profile: AgentProfile) -> RegistryState:
        """Replace one of the six fixed Agent configurations."""
        with self._lock:
            state = self.load()
            profiles = tuple(
                profile
                if current.agent_type == profile.agent_type
                else current
                for current in state.profiles
            )
            if not any(
                current.agent_type == profile.agent_type
                for current in state.profiles
            ):
                raise ProfileNotFoundError(
                    f"Agent {profile.agent_type!r} does not exist"
                )
            return self.save(state.model_copy(update={"profiles": profiles}))

    def patch_profile(
        self,
        profile_id: AgentType,
        patch: AgentProfilePatch,
    ) -> AgentProfile:
        with self._lock:
            state = self.load()
            current = next(
                (
                    profile
                    for profile in state.profiles
                    if profile.agent_type == profile_id
                ),
                None,
            )
            if current is None:
                raise ProfileNotFoundError(f"profile {profile_id!r} does not exist")
            updates = {
                field: getattr(patch, field) for field in patch.model_fields_set
            }
            validated = AgentProfile.model_validate(
                current.model_copy(update=updates).model_dump(mode="json")
            )
            profiles = tuple(
                validated if profile.agent_type == profile_id else profile
                for profile in state.profiles
            )
            self.save(state.model_copy(update={"profiles": profiles}))
            return validated
