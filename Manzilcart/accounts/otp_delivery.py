"""
OTP delivery channels.

Two channels are supported for sign-in codes:

* **WhatsApp** is deliberately a stub. The existing notification queue records the
  message and, in DEBUG, echoes it to the console so development can continue without
  a provider account. Replace the TODO with the real WhatsApp Cloud API call later.
* **Email** is sent through a dedicated SMTP backend using the single active
  :class:`~accounts.models.EmailSender` row. Its SMTP password is read from the
  environment, never from the database. This is independent of the site's default
  mailer, which is deliberately a console backend in development.

Delivery failures are logged and swallowed: a messaging outage must never prevent an
OTP challenge from being created or break the login flow.
"""

from __future__ import annotations

import logging
import os

from django.conf import settings
from django.core.mail import EmailMessage
from django.core.mail.backends.smtp import EmailBackend

from accounts.models import EmailSender
from core.logging_utils import mask_email, mask_phone

logger = logging.getLogger("manzilcart.accounts.otp")

SMTP_PASSWORD_ENV = "OTP_EMAIL_SMTP_PASSWORD"
EMAIL_SUBJECT = "Your ManzilCart verification code"


def _debug_echo(channel: str, recipient: str, otp: str) -> None:
    """Print a code only in development; production logs never contain OTPs."""
    if not settings.DEBUG:
        return
    masked = mask_phone(recipient) if channel == "whatsapp" else mask_email(recipient)
    print(f"[DEV ONLY][{channel.upper()}] {masked}: {otp}", flush=True)


def send_otp_whatsapp(phone: str, otp: str, **kwargs) -> bool:
    """
    Deliver an OTP over WhatsApp.

    TODO: replace the queued mock message with the real WhatsApp Cloud API call once
    credentials and an approved template are available. Until then the notification
    service records the message and echoes it to the console when ``DEBUG`` is on.
    """
    from notifications.services import send_otp

    _debug_echo("whatsapp", phone, otp)
    try:
        send_otp(
            phone=phone,
            code=otp,
            ttl_seconds=settings.OTP_TTL_SECONDS,
            user=kwargs.get("user"),
            idempotency_key=kwargs.get("idempotency_key"),
        )
    except Exception:  # noqa: BLE001 - delivery must never break login
        logger.exception("WhatsApp OTP stub failed for %s", mask_phone(phone))
        return False
    return True


def send_otp_email(email: str, otp: str, *, sender: EmailSender | None = None) -> bool:
    """Send an OTP from the configured active sender account. Never raises."""
    _debug_echo("email", email, otp)

    sender = sender or EmailSender.objects.filter(is_active=True).first()
    if sender is None:
        logger.warning("OTP email skipped: no active EmailSender is configured.")
        return False

    password = os.environ.get(SMTP_PASSWORD_ENV, "")
    if not password:
        logger.warning(
            "OTP email skipped: %s is not set for sender %s.",
            SMTP_PASSWORD_ENV,
            sender.pk,
        )
        return False

    minutes = max(1, settings.OTP_TTL_SECONDS // 60)
    body = (
        f"{otp} is your ManzilCart verification code. "
        f"It expires in {minutes} minute(s). Do not share it with anyone."
    )

    try:
        # Construct the SMTP backend directly: the site's default MAILERS alias is
        # intentionally a console backend in development, but OTP email must always
        # use the configured SMTP account.
        connection = EmailBackend(
            # The alias tells Django 6.1+ to use the modern MAILERS-style
            # constructor path, which does not read removed EMAIL_* settings.
            alias="otp",
            host=sender.smtp_host,
            port=sender.smtp_port,
            username=sender.smtp_username,
            password=password,
            use_tls=sender.use_tls,
            use_ssl=False,
            timeout=10,
            fail_silently=False,
        )
        message = EmailMessage(
            subject=EMAIL_SUBJECT,
            body=body,
            from_email=sender.sender_email,
            to=[email],
            connection=connection,
        )
        sent = message.send(fail_silently=False)
    except Exception:  # noqa: BLE001 - provider errors are reported, not raised
        logger.exception(
            "OTP email delivery failed via sender %s for %s",
            sender.pk,
            mask_email(email),
        )
        return False

    if not sent:
        logger.warning(
            "OTP email via sender %s was accepted by no recipients for %s",
            sender.pk,
            mask_email(email),
        )
        return False
    return True
