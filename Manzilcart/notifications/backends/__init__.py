"""
Provider backends.

Every transport (WhatsApp, SMS, email) is reached through a small interface so the
application never talks to a vendor SDK directly. Swapping providers, or running
against a mock in staging, is a configuration change.

Current state: ``mock`` for everything, per the agreed plan. The live backends are
present but refuse to run until credentials are configured, and they fail loudly
rather than silently pretending a message was delivered.
"""

from __future__ import annotations

from notifications.backends.base import (
    BaseNotificationBackend,
    DeliveryResult,
    EmailBackend,
    MockNotificationBackend,
    SMSBackend,
    WhatsAppCloudBackend,
    get_backend,
)

__all__ = [
    "BaseNotificationBackend",
    "DeliveryResult",
    "EmailBackend",
    "MockNotificationBackend",
    "SMSBackend",
    "WhatsAppCloudBackend",
    "get_backend",
]
