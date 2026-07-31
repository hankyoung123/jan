import json
from pathlib import Path
from threading import RLock

from pydantic import Field, model_validator

from story_engine.domain.models import DomainModel
from story_engine.models.contracts import ModelProfile
from story_engine.models.errors import (
    ModelConfigurationError,
    ProfileNotFoundError,
)
from story_engine.workspace.atomic import atomic_write_text


class RegistryState(DomainModel):
    schema_version: int = Field(default=2, ge=2)
    profiles: tuple[ModelProfile, ...]

    @model_validator(mode="after")
    def profile_ids_are_unique(self) -> "RegistryState":
        profile_ids = [profile.id for profile in self.profiles]
        if len(profile_ids) != len(set(profile_ids)):
            raise ValueError("profile IDs must be unique")
        return self


def default_registry() -> RegistryState:
    profiles = (
        ModelProfile(
            id="character",
            name="Character",
            task_type="character",
            provider_id="llamacpp",
            model="qwen3-8b",
            temperature=0.7,
        ),
        ModelProfile(
            id="resolver",
            name="Resolver",
            task_type="resolver",
            provider_id="openai",
            model="gpt-5-mini",
            temperature=0.2,
        ),
        ModelProfile(
            id="editor",
            name="Editor",
            task_type="editor",
            provider_id="openai",
            model="gpt-5-mini",
            temperature=0.1,
        ),
        ModelProfile(
            id="writer",
            name="Writer",
            task_type="writer",
            provider_id="openai",
            model="gpt-5-mini",
            temperature=0.8,
            max_output_tokens=4096,
        ),
        ModelProfile(
            id="embedding",
            name="Embedding",
            task_type="embedding",
            provider_id="llamacpp",
            model="bge-m3",
            temperature=None,
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
                payload = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(payload, dict) and payload.get("schema_version") == 1:
                    payload = {
                        "schema_version": 2,
                        "profiles": payload.get("profiles", []),
                    }
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

    def get_profile(self, profile_id: str) -> ModelProfile:
        for profile in self.load().profiles:
            if profile.id == profile_id:
                return profile
        raise ProfileNotFoundError(f"profile {profile_id!r} does not exist")

    def upsert_profile(self, profile: ModelProfile) -> RegistryState:
        with self._lock:
            state = self.load()
            profiles = tuple(
                profile if current.id == profile.id else current
                for current in state.profiles
            )
            if not any(current.id == profile.id for current in state.profiles):
                profiles = (*profiles, profile)
            return self.save(state.model_copy(update={"profiles": profiles}))
