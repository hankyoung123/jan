import json
from collections.abc import Mapping
from pathlib import Path
from threading import RLock
from typing import Any

from pydantic import Field, model_validator

from story_engine.domain.models import DomainModel
from story_engine.models.contracts import ModelProfile
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


def _legacy_profile(
    payload: Mapping[str, Any],
    *,
    profile_id: str,
    task_type: str,
) -> ModelProfile:
    provider_id = payload.get("provider_id")
    model = payload.get("model")
    enabled = payload.get("enabled", True)
    model_ref = None
    if enabled is not False and isinstance(provider_id, str) and isinstance(model, str):
        model_ref = f"{provider_id.strip()}/{model.strip()}"
    return ModelProfile.model_validate(
        {
            "id": profile_id,
            "task_type": task_type,
            "model_ref": model_ref,
            "max_output_tokens": payload.get("max_output_tokens", 2048),
            "timeout_seconds": payload.get("timeout_seconds", 60),
            "temperature": payload.get("temperature"),
        }
    )


def _migrate_schema_three(payload: Mapping[str, Any]) -> RegistryState:
    legacy_profiles = payload.get("profiles")
    if not isinstance(legacy_profiles, list):
        raise ValueError("schema 3 registry profiles must be a list")

    migrated: list[ModelProfile] = []
    wiki_source: Mapping[str, Any] | None = None
    for item in legacy_profiles:
        if not isinstance(item, Mapping):
            raise ValueError("schema 3 registry profile must be an object")
        task_type = item.get("task_type")
        profile_id = item.get("id")
        if task_type in {"projection", "memory_consolidation", "wiki_maintenance"}:
            if wiki_source is None or task_type == "projection":
                wiki_source = item
            continue
        if task_type not in {"actor", "game_master", "editor", "writer"}:
            continue
        if not isinstance(profile_id, str):
            raise ValueError("schema 3 registry profile ID must be a string")
        migrated.append(
            _legacy_profile(item, profile_id=profile_id, task_type=task_type)
        )

    if wiki_source is not None:
        migrated.append(
            _legacy_profile(
                wiki_source,
                profile_id="wiki-maintenance",
                task_type="wiki_maintenance",
            )
        )

    by_id = {profile.id: profile for profile in migrated}
    for default in default_registry().profiles:
        by_id.setdefault(default.id, default)
    return RegistryState(profiles=tuple(by_id.values()))


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
                if isinstance(payload, Mapping) and payload.get("schema_version") == 3:
                    migrated = _migrate_schema_three(payload)
                    backup = self.path.with_suffix(
                        f"{self.path.suffix}.schema-3.bak"
                    )
                    if not backup.exists():
                        atomic_write_text(backup, source, overwrite=False)
                    self.save(migrated)
                    return migrated
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
