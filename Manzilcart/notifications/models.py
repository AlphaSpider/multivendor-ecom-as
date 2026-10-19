"""
Notification delivery: models, provider backends and the queue.

The database row *is* the queue
-------------------------------
A :class:`Notification` row carries its own status, attempt count, next attempt time
and idempotency key. That gives retries, backoff and duplicate suppression without
introducing Celery, RabbitMQ or an outbox table - a deliberate choice for a small
team on a small budget (see ``docs``/progress notes for the trade-off).

None of that replaces the outbox pattern's guarantees for *cross-system* delivery:
the row and the business change are committed together, and a worker retries later,
which is exactly the behaviour the specification asks for. What it does not give us is
exactly-once delivery to a provider - hence idempotency keys and provider receipts.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils import timezone

from core.models import TimeStampedModel


class NotificationQuerySet(models.QuerySet):
    def due(self, *, now=None):
        now = now or timezone.now()
        return self.filter(
            status=Notification.Status.PENDING,
            next_attempt_at__lte=now,
        )

    def pending(self):
        return self.filter(status=Notification.Status.PENDING)


class Notification(TimeStampedModel):
    """
    One outbound message and its delivery state.

    ``recipient`` holds the destination (E.164 number or email address). It is
    operational data: access is restricted to authorised staff in Django Admin.
    """

    class Channel(models.TextChoices):
        WHATSAPP = "whatsapp", "WhatsApp"
        SMS = "sms", "SMS"
        EMAIL = "email", "Email"

    class Kind(models.TextChoices):
        OTP = "otp", "Mobile verification code"
        AUCTION_SELECTION = "auction_selection", "Auction: bid selected"
        AUCTION_FALLBACK = "auction_fallback", "Auction: fallback invitation"
        AUCTION_OUTCOME = "auction_outcome", "Auction: outcome"
        AUCTION_OUTBID = "auction_outbid", "Auction: outbid notice"
        OFFER_RECEIVED = "offer_received", "Offer received"
        DEAL_ACCEPTED = "deal_accepted", "Deal accepted"
        DEAL_CANCELLATION = "deal_cancellation", "Cancellation update"
        OPS_SERVICE_REQUEST = "ops_service_request", "Operations: service request"
        ACCOUNT = "account", "Account notice"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        SENT = "sent", "Sent"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"

    channel = models.CharField(max_length=16, choices=Channel.choices, db_index=True)
    kind = models.CharField(max_length=32, choices=Kind.choices, db_index=True)
    recipient = models.CharField(max_length=254)
    subject = models.CharField(max_length=200, blank=True)
    body = models.TextField()

    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    attempts = models.PositiveSmallIntegerField(default=0)
    max_attempts = models.PositiveSmallIntegerField(default=5)
    next_attempt_at = models.DateTimeField(default=timezone.now, db_index=True)
    last_error = models.CharField(max_length=500, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    provider = models.CharField(max_length=32, blank=True)
    provider_message_id = models.CharField(max_length=128, blank=True)

    # Duplicate suppression: the same key can only ever produce one row.
    idempotency_key = models.CharField(
        max_length=190,
        blank=True,
        null=True,
        unique=True,
        help_text="Set by the producer so a retried task cannot send twice.",
    )

    # Context (all optional) so operators can trace a message back to its subject.
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="notifications",
    )
    emirate = models.ForeignKey(
        "locations.Emirate",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="notifications",
    )
    entity_type = models.CharField(max_length=64, blank=True)
    entity_id = models.CharField(max_length=64, blank=True)

    objects = NotificationQuerySet.as_manager()

    class Meta:
        verbose_name = "notification"
        verbose_name_plural = "notifications"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "next_attempt_at"], name="notification_due_idx"),
            models.Index(fields=["entity_type", "entity_id"], name="notification_entity_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.get_channel_display()} {self.get_kind_display()} -> {self.recipient} [{self.status}]"

    # -- state transitions --------------------------------------------------
    @property
    def can_retry(self) -> bool:
        return (
            self.status == self.Status.PENDING
            and self.attempts < self.max_attempts
        )

    def mark_sent(self, *, provider: str, provider_message_id: str = "") -> None:
        self.status = self.Status.SENT
        self.provider = provider[:32]
        self.provider_message_id = (provider_message_id or "")[:128]
        self.sent_at = timezone.now()
        self.last_error = ""
        self.save(
            update_fields=[
                "status",
                "provider",
                "provider_message_id",
                "sent_at",
                "last_error",
                "updated_at",
            ]
        )

    def mark_failed(self, *, error: str, retry_at=None) -> None:
        """
        Record a failed attempt.

        The status stays ``PENDING`` while attempts remain - a failed attempt is not a
        final failure - so the same row is retried by the worker with backoff.
        """
        self.attempts += 1
        self.last_error = (error or "")[:500]
        if self.attempts >= self.max_attempts:
            self.status = self.Status.FAILED
            self.next_attempt_at = timezone.now()
        else:
            self.status = self.Status.PENDING
            self.next_attempt_at = retry_at or timezone.now()
        self.save(
            update_fields=[
                "attempts",
                "last_error",
                "status",
                "next_attempt_at",
                "updated_at",
            ]
        )

    def cancel(self) -> None:
        self.status = self.Status.CANCELLED
        self.save(update_fields=["status", "updated_at"])
