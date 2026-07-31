import pytest

from story_engine.evolution.execution import (
    TurnAlreadyRunningError,
    TurnExecutionRegistry,
    TurnNotRunningError,
)


def test_one_project_has_only_one_active_turn_execution() -> None:
    registry = TurnExecutionRegistry()
    execution = registry.begin("fog-harbor")
    registry.identify("fog-harbor", execution, "turn-000001")

    with pytest.raises(TurnAlreadyRunningError):
        registry.begin("fog-harbor")

    cancellation = registry.cancel("fog-harbor")

    assert cancellation.turn_id == "turn-000001"
    assert execution.cancellation.is_set()


def test_cancel_and_completion_have_one_atomic_winner() -> None:
    cancelled_registry = TurnExecutionRegistry()
    cancelled = cancelled_registry.begin("fog-harbor")
    cancelled_registry.cancel("fog-harbor")

    assert cancelled_registry.claim_completion("fog-harbor", cancelled) is False

    completed_registry = TurnExecutionRegistry()
    completed = completed_registry.begin("fog-harbor")

    assert completed_registry.claim_completion("fog-harbor", completed) is True
    with pytest.raises(TurnNotRunningError):
        completed_registry.cancel("fog-harbor")
