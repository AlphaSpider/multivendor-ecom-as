"""
Notification queue and delivery service.

Everything that "sends something" goes through here, so retry behaviour, idempotency
and audit logging are implemented once.

Guarantees
----------
* ``queue_notification`` is safe to call twice with the same ``idempotency_key``: the
  second call returns the existing row instead of creating a duplicate.
* ``deliver`` takes a row lock, so two workers racing on the same row cannot both send
  it in the same pass.
* A delivery failure never raises into the caller's business transaction: a failed
  operations alert must not roll back an accepted deal.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from core.exceptions import ServiceUnavailableError
from notifications.backends.base import get_backend
from notifications.models import Notification

logger = logging.getLogger("manzilcart.notifications")


def backoff_seconds(attempts: int) -> int:
    """
    Exponential backoff, capped, for the next delivery attempt.

    Attempt 1 -> base, 2 -> 2*base, 3 -> 4*base ... capped at one hour.
    """
    base = settings.NOTIFICATION_BACKOFF_BASE_SECONDS
    return min(base * (2 ** max(0, attempts - 1)), 3600)


@transaction.atomic
def queue_notification(
    *,
    channel: str,
    kind: str,
    recipient: str,
    body: str,
    subject: str = "",
    user=None,
    emirate=None,
    entity=None,
    idempotency_key: str | None = None,
    max_attempts: int | None = None,
    send_now: bool = True,
) -> Notification:
    """
    Create (or reuse) a notification and optionally attempt delivery immediately.

    ``entity`` is any model instance the notification relates to; its type and primary
    key are stored for operator traceability.
    """
    entity_type = entity_id = ""
    if entity is not None:
        entity_type = entity._meta.label_lower
        entity_id = str(entity.pk)

    notification = None
    if idempotency_key:
        existing = Notification.objects.filter(idempotency_key=idempotency_key).first()
        if existing is not None:
            return existing

    try:
        with transaction.atomic():
            notification = Notification.objects.create(
                channel=channel,
                kind=kind,
                recipient=recipient,
                subject=subject[:200],
                body=body,
                max_attempts=max_attempts or settings.NOTIFICATION_MAX_ATTEMPTS,
                user=user,
                emirate=emirate,
                entity_type=entity_type[:64],
                entity_id=entity_id[:64],
                idempotency_key=idempotency_key,
            )
    except IntegrityError:
        # Lost a race on the idempotency key: the other caller's row wins.
        if idempotency_key:
            existing = Notification.objects.filter(idempotency_key=idempotency_key).first()
            if existing is not None:
                return existing
        raise

    if send_now:
        deliver(notification)
    return notification


def deliver(notification: Notification | None) -> Notification | None:
    """
    Attempt to deliver one notification.

    Never raises for provider problems: the failure is recorded on the row so the
    worker can retry. Callers are business transactions that must not be rolled back by
    a messaging outage.
    """
    if notification is None:
        return None

    with transaction.atomic():
        # Lock the row so concurrent workers cannot both attempt it.
        locked = (
            Notification.objects.select_for_update()
            .filter(pk=notification.pk)
            .first()
        )
        if locked is None:
            return None
        if locked.status != Notification.Status.PENDING:
            return locked

        try:
            backend = get_backend(locked.channel)
            result = backend.send(locked)
        except ServiceUnavailableError as exc:
            _record_failure(locked, error=exc.message, retryable=True)
            return locked
        except Exception as exc:  # noqa: BLE001 - never propagate provider errors
            logger.exception("Unexpected notification backend error")
            _record_failure(locked, error=str(exc), retryable=True)
            return locked

        if result.accepted:
            locked.mark_sent(
                provider=result.provider, provider_message_id=result.provider_message_id
            )
        else:
            _record_failure(locked, error=result.detail or "Provider rejected the message", retryable=result.retryable)
        return locked


def _record_failure(notification: Notification, *, error: str, retryable: bool) -> None:
    if retryable:
        retry_at = timezone.now() + timezone.timedelta(
            seconds=backoff_seconds(notification.attempts + 1)
        )
    else:
        # Non-retryable: burn the attempt budget so the worker stops trying.
        retry_at = timezone.now()
        notification.attempts = notification.max_attempts - 1
    notification.mark_failed(error=error, retry_at=retry_at)
    logger.info(
        "Notification %s attempt %s failed: %s",
        notification.pk,
        notification.attempts,
        error[:200],
    )


def retry_notification(notification: Notification, *, actor=None, request=None) -> Notification:
    """Manually re-queue a failed notification from the operations console."""
    from core.audit import record_audit
    from core.models import AuditEvent

    notification.status = Notification.Status.PENDING
    notification.attempts = 0
    notification.next_attempt_at = timezone.now()
    notification.save(
        update_fields=["status", "attempts", "next_attempt_at", "updated_at"]
    )
    record_audit(
        action=AuditEvent.Action.NOTIFICATION_RETRIED,
        actor=actor or getattr(request, "user", None),
        entity=notification,
        summary="Notification manually re-queued",
        request=request,
    )
    return deliver(notification)


# ---------------------------------------------------------------------------
# Message builders - one place per message type, so wording is reviewable
# ---------------------------------------------------------------------------


def send_otp(*, phone: str, code: str, ttl_seconds: int, idempotency_key: str | None = None):
    """
    Send a mobile verification code over WhatsApp (the primary channel).

    The body contains the code because that is the whole point of the message. It is
    never logged and never stored on the ``Notification`` row in mock mode... except
    that the row *does* hold the body, because a retry must be able to resend the same
    message. Rows are therefore operationally sensitive and access is restricted to
    staff.
    """
    minutes = max(1, ttl_seconds // 60)
    return queue_notification(
        channel=Notification.Channel.WHATSAPP,
        kind=Notification.Kind.OTP,
        recipient=phone,
        subject="Your ManzilCart verification code",
        body=(
            f"{code} is your ManzilCart verification code. "
            f"It expires in {minutes} minute(s). Do not share it with anyone."
        ),
        idempotency_key=idempotency_key,
    )


def send_email(*, kind: str, recipient: str, subject: str, body: str, **kwargs):
    """Queue a transactional email (deal updates, auction deadlines, ...)."""
    return queue_notification(
        channel=Notification.Channel.EMAIL,
        kind=kind,
        recipient=recipient,
        subject=subject,
        body=body,
        **kwargs,
    )


def send_sms(*, kind: str, recipient: str, body: str, **kwargs):
    """Queue an SMS (used for auction confirmations where email is not enough)."""
    return queue_notification(
        channel=Notification.Channel.SMS,
        kind=kind,
        recipient=recipient,
        body=body,
        **kwargs,
    )
