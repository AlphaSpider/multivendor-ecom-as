"""
Email transport (Django 6.1 ``MAILERS`` API).

Credentials come from the ``MAILERS`` setting; nothing is hardcoded. A successful
``send_messages`` call only proves the SMTP server accepted the message, so the result
records that distinction explicitly.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.core.mail import EmailMultiAlternatives, mailers

from core.exceptions import ServiceUnavailableError

logger = logging.getLogger("manzilcart.notifications.email")


def send_email_notification(notification, alias: str = "default"):
    """Deliver an email :class:`~notifications.models.Notification`."""
    from notifications.backends.base import DeliveryResult

    if not notification.recipient:
        raise ServiceUnavailableError("Notification has no recipient address.")

    message = EmailMultiAlternatives(
        subject=notification.subject or "ManzilCart",
        body=notification.body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[notification.recipient],
    )

    try:
        mailer = mailers[alias]
        sent_count = mailer.send_messages([message])
    except Exception as exc:  # noqa: BLE001 - provider errors are reported, not raised
        logger.warning("Email delivery failed for notification %s: %s", notification.pk, exc)
        return DeliveryResult(
            accepted=False,
            provider="email",
            detail=str(exc)[:300],
            retryable=True,
        )

    if not sent_count:
        return DeliveryResult(
            accepted=False,
            provider="email",
            detail="Mail backend accepted no messages.",
            retryable=True,
        )

    # The provider accepted the message; delivery is not yet confirmed.
    return DeliveryResult(
        accepted=True,
        provider="email",
        provider_message_id="",
        detail="Accepted by the mail backend.",
    )
