from pathlib import Path

from story_engine.domain.models import StoryEvent
from story_engine.workspace.atomic import atomic_write_text
from story_engine.workspace.documents import (
    EventDocument,
    load_document,
    render_event,
)
from story_engine.workspace.lock import ProjectLock


class EventStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def append(self, event: StoryEvent) -> Path:
        with ProjectLock(self.root):
            path = self.root / "events" / f"{event.sequence:06d}.md"
            atomic_write_text(path, render_event(event), overwrite=False)
            return path

    def list_events(self) -> tuple[StoryEvent, ...]:
        events: list[StoryEvent] = []
        for path in sorted((self.root / "events").glob("*.md")):
            document, _ = load_document(path, EventDocument)
            events.append(document.to_domain())
        return tuple(events)

    def next_sequence(self) -> int:
        events = self.list_events()
        return 1 if not events else events[-1].sequence + 1
