import json
from pathlib import Path
from threading import RLock

from pydantic import Field, model_validator

from story_engine.domain.models import DomainModel
from story_engine.models.contracts import ModelProfile, ProviderConfig
from story_engine.models.errors import (
    ModelConfigurationError,
    ProfileNotFoundError,
    ProviderNotFoundError,
)
from story_engine.workspace.atomic import atomic_write_text


class RegistryState(DomainModel):
    schema_version: int = Field(default=1, ge=1)
    providers: tuple[ProviderConfig, ...]
    profiles: tuple[ModelProfile, ...]

    @model_validator(mode="after")
    def references_are_valid(self) -> "RegistryState":
        provider_ids = [provider.id for provider in self.providers]
        profile_ids = [profile.id for profile in self.profiles]
        if len(provider_ids) != len(set(provider_ids)):
            raise ValueError("provider IDs must be unique")
        if len(profile_ids) != len(set(profile_ids)):
            raise ValueError("profile IDs must be unique")
        unknown = {
            profile.provider_id
            for profile in self.profiles
            if profile.provider_id not in provider_ids
        }
        if unknown:
            raise ValueError(f"profiles reference unknown providers: {sorted(unknown)}")
        return self


def default_registry() -> RegistryState:
    providers = (
        ProviderConfig(
            id="local-jan",
            name="Jan Local",
            kind="local",
            base_url="http://127.0.0.1:1337/v1",
        ),
        ProviderConfig(
            id="remote-openai",
            name="OpenAI Compatible",
            kind="remote",
            base_url="https://api.openai.com/v1",
            requires_api_key=True,
        ),
    )
    profiles = (
        ModelProfile(
            id="character",
            name="Character",
            task_type="character",
            provider_id="local-jan",
            model="qwen3-8b",
            temperature=0.7,
        ),
        ModelProfile(
            id="resolver",
            name="Resolver",
            task_type="resolver",
            provider_id="remote-openai",
            model="gpt-5-mini",
            temperature=0.2,
        ),
        ModelProfile(
            id="editor",
            name="Editor",
            task_type="editor",
            provider_id="remote-openai",
            model="gpt-5-mini",
            temperature=0.1,
        ),
        ModelProfile(
            id="writer",
            name="Writer",
            task_type="writer",
            provider_id="remote-openai",
            model="gpt-5-mini",
            temperature=0.8,
            max_output_tokens=4096,
        ),
        ModelProfile(
            id="embedding",
            name="Embedding",
            task_type="embedding",
            provider_id="local-jan",
            model="bge-m3",
            temperature=None,
        ),
    )
    return RegistryState(providers=providers, profiles=profiles)


class ProfileRegistry:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = RLock()

    def load(self) -> RegistryState:
        with self._lock:
            if not self.path.exists():
                return default_registry()
            try:
                return RegistryState.model_validate_json(
                    self.path.read_text(encoding="utf-8")
                )
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

    def get_provider(self, provider_id: str) -> ProviderConfig:
        for provider in self.load().providers:
            if provider.id == provider_id:
                return provider
        raise ProviderNotFoundError(f"provider {provider_id!r} does not exist")

    def get_profile(self, profile_id: str) -> ModelProfile:
        for profile in self.load().profiles:
            if profile.id == profile_id:
                return profile
        raise ProfileNotFoundError(f"profile {profile_id!r} does not exist")

    def upsert_provider(self, provider: ProviderConfig) -> RegistryState:
        with self._lock:
            state = self.load()
            providers = tuple(
                provider if current.id == provider.id else current
                for current in state.providers
            )
            if not any(current.id == provider.id for current in state.providers):
                providers = (*providers, provider)
            return self.save(state.model_copy(update={"providers": providers}))

    def upsert_profile(self, profile: ModelProfile) -> RegistryState:
        with self._lock:
            state = self.load()
            if profile.provider_id not in {provider.id for provider in state.providers}:
                raise ProviderNotFoundError(
                    f"provider {profile.provider_id!r} does not exist"
                )
            profiles = tuple(
                profile if current.id == profile.id else current
                for current in state.profiles
            )
            if not any(current.id == profile.id for current in state.profiles):
                profiles = (*profiles, profile)
            return self.save(state.model_copy(update={"profiles": profiles}))
