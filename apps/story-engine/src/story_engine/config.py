from dataclasses import dataclass, field
from pathlib import Path
from secrets import token_urlsafe


@dataclass(frozen=True, slots=True)
class EngineSettings:
    """Process-local settings that are never serialized into story projects."""

    session_token: str = field(default_factory=lambda: token_urlsafe(32))
    host: str = "127.0.0.1"
    port: int = 0
    projects_root: Path = field(default_factory=lambda: Path.cwd() / "projects")

    def __post_init__(self) -> None:
        if not self.session_token:
            raise ValueError("session_token must not be empty")
        if self.host not in {"127.0.0.1", "localhost"}:
            raise ValueError("story engine must bind to the loopback interface")
        if not 0 <= self.port <= 65535:
            raise ValueError("port must be between 0 and 65535")
