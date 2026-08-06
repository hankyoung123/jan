import os
from dataclasses import dataclass, field
from pathlib import Path
from secrets import token_urlsafe
from urllib.parse import urlparse


@dataclass(frozen=True, slots=True)
class EngineSettings:
    """Process-local settings that are never serialized into story projects."""

    session_token: str = field(default_factory=lambda: token_urlsafe(32))
    host: str = "127.0.0.1"
    port: int = 0
    projects_root: Path = field(default_factory=lambda: Path.cwd() / "projects")
    model_registry_path: Path = field(
        default_factory=lambda: Path.cwd() / "config" / "model-registry.json"
    )
    model_base_url: str | None = field(
        default_factory=lambda: os.environ.get("STORY_ENGINE_MODEL_BASE_URL")
    )
    model_api_key: str | None = field(
        default_factory=lambda: os.environ.get("STORY_ENGINE_MODEL_API_KEY")
    )
    allowed_origins: tuple[str, ...] = (
        "http://127.0.0.1:1420",
        "http://localhost:1420",
        "http://tauri.localhost",
        "tauri://localhost",
    )

    def __post_init__(self) -> None:
        if not self.session_token:
            raise ValueError("session_token must not be empty")
        if self.host not in {"127.0.0.1", "localhost"}:
            raise ValueError("story engine must bind to the loopback interface")
        if not 0 <= self.port <= 65535:
            raise ValueError("port must be between 0 and 65535")
        if bool(self.model_base_url) != bool(self.model_api_key):
            raise ValueError("model bridge URL and API key must be configured together")
        if self.model_base_url is not None:
            parsed = urlparse(self.model_base_url)
            if (
                parsed.scheme != "http"
                or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
                or parsed.username
                or parsed.password
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError("model bridge must be an absolute loopback HTTP URL")
        if self.model_api_key is not None and len(self.model_api_key) < 32:
            raise ValueError("model bridge API key must contain at least 32 characters")

    @property
    def model_bridge_configured(self) -> bool:
        return self.model_base_url is not None and self.model_api_key is not None
