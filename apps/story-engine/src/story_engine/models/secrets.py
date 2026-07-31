from typing import Protocol

import keyring
from keyring.errors import KeyringError

from story_engine.models.errors import ModelConfigurationError

SERVICE_NAME = "ai-story-evolution-engine"


class SecretStore(Protocol):
    def get(self, provider_id: str) -> str | None: ...

    def set(self, provider_id: str, secret: str) -> None: ...

    def delete(self, provider_id: str) -> None: ...


class KeyringSecretStore:
    def get(self, provider_id: str) -> str | None:
        try:
            return keyring.get_password(SERVICE_NAME, provider_id)
        except KeyringError as error:
            raise ModelConfigurationError(
                "operating-system keychain is unavailable"
            ) from error

    def set(self, provider_id: str, secret: str) -> None:
        try:
            keyring.set_password(SERVICE_NAME, provider_id, secret)
        except KeyringError as error:
            raise ModelConfigurationError(
                "failed to save credential in keychain"
            ) from error

    def delete(self, provider_id: str) -> None:
        try:
            keyring.delete_password(SERVICE_NAME, provider_id)
        except keyring.errors.PasswordDeleteError:
            return
        except KeyringError as error:
            raise ModelConfigurationError(
                "failed to delete credential from keychain"
            ) from error


class MemorySecretStore:
    def __init__(self) -> None:
        self._values: dict[str, str] = {}

    def get(self, provider_id: str) -> str | None:
        return self._values.get(provider_id)

    def set(self, provider_id: str, secret: str) -> None:
        self._values[provider_id] = secret

    def delete(self, provider_id: str) -> None:
        self._values.pop(provider_id, None)
