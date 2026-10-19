"""Backend interface and registry used by the notification service."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from django.conf import settings
from django.utils.module_loading import import_string

from core.exceptions import ServiceUnavailableError

logger = logging.getLogger("manzilcart.notifications")


@dataclass(frozen=True)
class DeliveryResult:
    """
    Outcome of a single delivery attempt.

    ``accepted`` means the provider accepted the message. It does **not** mean the
    message reached the handset: delivery receipts, where a provider offers them, are
    recorded separately. The specification is explicit that a successful API call must
    not be reported as a delivered message.
    """

    accepted: bool
    provider: str
    provider_message_id: str = ""
    detail: str = ""
    retryable: bool = True


class BaseNotificationBackend:
    """Interface every transport implements."""

    name = "base"
    #: Channels this backend can deliver.
    channels: tuple[str, ...] = ()

    def send(self, notification) -> DeliveryResult:
        raise NotImplementedError


class MockNotificationBackend(BaseNotificationBackend):
    """
    Records messages in memory instead of sending them.

    Used in development, staging and the test suite. The code is *not* persisted: an
    in-memory outbox lets tests assert on the content, and the console echo (enabled
    only while ``DEBUG`` is on) lets a developer read an OTP. Nothing is written to
    the log in production.
    """

    name = "mock"
    channels = ("whatsapp", "sms", "email")

    # Class-level so tests can inspect and clear it.
    outbox: list[dict] = []

    def send(self, notification) -> DeliveryResult:
        record = {
            "channel": notification.channel,
            "kind": notification.kind,
            "recipient": notification.recipient,
            "subject": notification.subject,
            "body": notification.body,
        }
        MockNotificationBackend.outbox.append(record)

        if settings.DEBUG and settings.NOTIFICATIONS_MODE == "mock":
            # Development only. Never enabled in production: the specification forbids
            # writing OTPs to logs, and this path is unreachable when DEBUG is off.
            self._echo_to_console(record)

        return DeliveryResult(
            accepted=True,
            provider=self.name,
            provider_message_id=f"mock-{notification.pk}",
            detail="Recorded by mock backend.",
        )

    @staticmethod
    def _echo_to_console(record: dict) -> None:
        banner = "=" * 68
        print(
            f"\n{banner}\n"
            f"[DEV ONLY] {record['channel'].upper()} -> {record['recipient']}\n"
            f"{record['subject']}\n{record['body']}\n{banner}\n",
            flush=True,
        )


class WhatsAppCloudBackend(BaseNotificationBackend):
    """
    WhatsApp Business Platform (Cloud API) - the agreed OTP channel.

    Not enabled yet: credentials and an approved message template are required. Until
    then this raises instead of silently dropping a verification code.
    """

    name = "whatsapp_cloud"
    channels = ("whatsapp",)

    def send(self, notification) -> DeliveryResult:
        token = getattr(settings, "WHATSAPP_ACCESS_TOKEN", "")
        phone_number_id = getattr(settings, "WHATSAPP_PHONE_NUMBER_ID", "")
        if not token or not phone_number_id:
            raise ServiceUnavailableError(
                "WhatsApp delivery is not configured (credentials missing)."
            )
        raise ServiceUnavailableError(
            "WhatsApp delivery is not implemented yet. See notifications/backends/whatsapp.py."
        )


class SMSBackend(BaseNotificationBackend):
    """SMS transport, used for auction confirmation/fallback where WhatsApp is not enough."""

    name = "sms"
    channels = ("sms",)

    def send(self, notification) -> DeliveryResult:
        if not getattr(settings, "SMS_API_KEY", ""):
            raise ServiceUnavailableError("SMS delivery is not configured.")
        raise ServiceUnavailableError(
            "SMS delivery is not implemented yet. See notifications/backends/sms.py."
        )


class EmailBackend(BaseNotificationBackend):
    """Transactional email (Amazon SES or any SMTP provider)."""

    name = "email"
    channels = ("email",)

    def send(self, notification) -> DeliveryResult:
        from notifications.backends.email_ import send_email_notification

        return send_email_notification(notification)


_BACKEND_PATHS = {
    "mock": "notifications.backends.MockNotificationBackend",
    "whatsapp_cloud": "notifications.backends.WhatsAppCloudBackend",
    "sms": "notifications.backends.SMSBackend",
    "email": "notifications.backends.EmailBackend",
}

#: Channel -> backend name used when ``NOTIFICATIONS_MODE`` is ``live``.
LIVE_CHANNEL_BACKENDS = {
    "whatsapp": "whatsapp_cloud",
    "sms": "sms",
    "email": "email",
}


def get_backend(channel: str) -> BaseNotificationBackend:
    """
    Return the backend to use for ``channel``.

    In ``mock`` mode (the default) everything is recorded in memory. In ``live`` mode
    each channel maps to its provider backend.
    """
    if settings.NOTIFICATIONS_MODE == "mock":
        return MockNotificationBackend()

    backend_name = LIVE_CHANNEL_BACKENDS.get(channel)
    if backend_name is None:
        raise ServiceUnavailableError(f"No provider configured for channel '{channel}'.")
    return import_string(_BACKEND_PATHS[backend_name])()
