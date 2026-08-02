from threading import RLock


class BranchAlreadyActiveError(RuntimeError):
    """Raised when a branch already has a live simulation session."""


class SessionExecutionRegistry:
    """Enforce one live writer for each project branch."""

    def __init__(self) -> None:
        self._owners: dict[tuple[str, str], str] = {}
        self._lock = RLock()

    def claim(self, project_id: str, branch_id: str, session_id: str) -> None:
        key = (project_id, branch_id)
        with self._lock:
            owner = self._owners.get(key)
            if owner is not None:
                raise BranchAlreadyActiveError(
                    f"branch {project_id}/{branch_id} is already owned by {owner}"
                )
            self._owners[key] = session_id

    def release(self, project_id: str, branch_id: str, session_id: str) -> None:
        key = (project_id, branch_id)
        with self._lock:
            if self._owners.get(key) == session_id:
                self._owners.pop(key)

    def owner(self, project_id: str, branch_id: str) -> str | None:
        with self._lock:
            return self._owners.get((project_id, branch_id))
