import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from story_engine.models.contracts import ModelProfile, ProviderConfig
from story_engine.models.errors import ProviderNotFoundError
from story_engine.models.registry import ProfileRegistry


def test_default_registry_has_each_required_task_profile(tmp_path: Path) -> None:
    registry = ProfileRegistry(tmp_path / "model-registry.json")

    state = registry.load()

    assert {profile.task_type for profile in state.profiles} == {
        "character",
        "resolver",
        "editor",
        "writer",
        "embedding",
    }
    assert not registry.path.exists()


def test_registry_persists_non_secret_configuration_atomically(tmp_path: Path) -> None:
    path = tmp_path / "config" / "model-registry.json"
    registry = ProfileRegistry(path)
    provider = ProviderConfig(
        id="test-remote",
        name="Test Remote",
        kind="remote",
        base_url="https://models.example/v1",
        requires_api_key=True,
    )
    registry.upsert_provider(provider)
    profile = ModelProfile(
        id="test-writer",
        name="Test Writer",
        task_type="writer",
        provider_id=provider.id,
        model="test-model",
    )

    registry.upsert_profile(profile)

    reloaded = ProfileRegistry(path).load()
    assert registry.get_profile(profile.id) == profile
    assert provider in reloaded.providers
    serialized = path.read_text(encoding="utf-8")
    assert '"api_key":' not in serialized
    assert not list(path.parent.glob("*.tmp"))
    assert json.loads(serialized)["schema_version"] == 1


def test_profile_cannot_reference_unknown_provider(tmp_path: Path) -> None:
    registry = ProfileRegistry(tmp_path / "models.json")
    profile = ModelProfile(
        id="unknown-provider-profile",
        name="Unknown",
        task_type="editor",
        provider_id="missing",
        model="model",
    )

    with pytest.raises(ProviderNotFoundError):
        registry.upsert_profile(profile)


@pytest.mark.parametrize(
    ("kind", "base_url"),
    [
        ("remote", "http://models.example/v1"),
        ("local", "http://192.168.1.2:8080/v1"),
        ("remote", "https://user:password@models.example/v1"),
    ],
)
def test_provider_endpoint_security_rules(
    kind: str,
    base_url: str,
) -> None:
    with pytest.raises(ValidationError):
        ProviderConfig(
            id="unsafe",
            name="Unsafe",
            kind=kind,  # type: ignore[arg-type]
            base_url=base_url,
        )
