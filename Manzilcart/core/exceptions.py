"""Domain-level exceptions with user-safe messages."""

from __future__ import annotations


class DomainError(Exception):
    """
    Base class for expected, user-explainable failures.

    Views catch these and turn them into a message rather than a 500. The message
    is safe to show to end users, so never embed internal details or secrets.
    """

    default_message = "Something went wrong. Please try again."

    def __init__(self, message: str | None = None):
        super().__init__(message or self.default_message)
        self.message = message or self.default_message


class StateTransitionError(DomainError):
    """Raised when an action is not valid for the object's current state."""

    default_message = "That action is not available for the current status."


class PermissionDeniedError(DomainError):
    """Raised when a user is not allowed to perform an action."""

    default_message = "You are not allowed to perform that action."


class ServiceUnavailableError(DomainError):
    """Raised when a required provider or capability is not available."""

    default_message = "This service is temporarily unavailable. Please try again later."


class LocationError(DomainError):
    """Raised for invalid emirate / service-area combinations."""

    default_message = "That location is not currently supported."


class RateLimitedError(DomainError):
    """Raised when a throttle (OTP resend, bid spam, ...) is hit."""

    default_message = "Too many attempts. Please wait and try again."
