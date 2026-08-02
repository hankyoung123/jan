import os
from pathlib import Path
from threading import Lock, RLock, local
from types import TracebackType

_registry_guard = Lock()
_process_locks: dict[Path, RLock] = {}
_thread_state = local()


def _lock_file(descriptor: int) -> None:
    if os.name == "nt":
        import msvcrt

        os.lseek(descriptor, 0, os.SEEK_SET)
        msvcrt.locking(  # type: ignore[attr-defined]
            descriptor,
            msvcrt.LK_LOCK,  # type: ignore[attr-defined]
            1,
        )
        return
    import fcntl

    fcntl.flock(descriptor, fcntl.LOCK_EX)


def _unlock_file(descriptor: int) -> None:
    if os.name == "nt":
        import msvcrt

        os.lseek(descriptor, 0, os.SEEK_SET)
        msvcrt.locking(  # type: ignore[attr-defined]
            descriptor,
            msvcrt.LK_UNLCK,  # type: ignore[attr-defined]
            1,
        )
        return
    import fcntl

    fcntl.flock(descriptor, fcntl.LOCK_UN)


class ProjectLock:
    """Reentrant process and cross-process lock for one canonical workspace."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.path = self.root / ".story-engine/project.lock"
        with _registry_guard:
            self._process_lock = _process_locks.setdefault(self.root, RLock())

    def __enter__(self) -> "ProjectLock":
        self._process_lock.acquire()
        depths = getattr(_thread_state, "depths", None)
        if depths is None:
            depths = {}
            _thread_state.depths = depths
        handles = getattr(_thread_state, "handles", None)
        if handles is None:
            handles = {}
            _thread_state.handles = handles
        depth = depths.get(self.root, 0)
        if depth == 0:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
            if os.fstat(descriptor).st_size == 0:
                os.write(descriptor, b"0")
                os.fsync(descriptor)
            try:
                _lock_file(descriptor)
            except BaseException:
                os.close(descriptor)
                self._process_lock.release()
                raise
            handles[self.root] = descriptor
        depths[self.root] = depth + 1
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc_value, traceback
        depths = _thread_state.depths
        depth = depths[self.root] - 1
        if depth == 0:
            descriptor = _thread_state.handles.pop(self.root)
            try:
                _unlock_file(descriptor)
            finally:
                os.close(descriptor)
                depths.pop(self.root)
        else:
            depths[self.root] = depth
        self._process_lock.release()
