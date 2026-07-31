class DomainError(Exception):
    """Base class for errors caused by domain rule violations."""


class InvalidTransitionError(DomainError):
    """Raised when a candidate lifecycle transition is not allowed."""

