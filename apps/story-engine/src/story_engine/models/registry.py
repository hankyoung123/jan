import json
from pathlib import Path
from threading import RLock

from pydantic import Field, model_validator

from story_engine.domain.models import DomainModel
from story_engine.models.contracts import ModelProfile, ModelProfilePatch
from story_engine.models.errors import (
    ModelConfigurationError,
    ProfileNotFoundError,
)
from story_engine.workspace.atomic import atomic_write_text


class RegistryState(DomainModel):
    schema_version: int = Field(default=4, ge=4)
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
            id="actor",
            task_type="actor",
            temperature=0.7,
        ),
        ModelProfile(
            id="game-master",
            task_type="game_master",
            temperature=0.2,
        ),
        ModelProfile(
            id="wiki-maintenance",
            task_type="wiki_maintenance",
            temperature=0.1,
            max_output_tokens=4096,
            timeout_seconds=120,
        ),
        ModelProfile(
            id="editor",
            task_type="editor",
            temperature=0.1,
        ),
        ModelProfile(
            id="writer",
            task_type="writer",
            temperature=0.8,
            max_output_tokens=4096,
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

    def patch_profile(
        self,
        profile_id: str,
        patch: ModelProfilePatch,
    ) -> ModelProfile:
        with self._lock:
            state = self.load()
            current = next(
                (profile for profile in state.profiles if profile.id == profile_id),
                None,
            )
            if current is None:
                raise ProfileNotFoundError(f"profile {profile_id!r} does not exist")
            updates = {
                field: getattr(patch, field) for field in patch.model_fields_set
            }
            validated = ModelProfile.model_validate(
                current.model_copy(update=updates).model_dump(mode="json")
            )
            profiles = tuple(
                validated if profile.id == profile_id else profile
                for profile in state.profiles
            )
            self.save(state.model_copy(update={"profiles": profiles}))
            return validated
