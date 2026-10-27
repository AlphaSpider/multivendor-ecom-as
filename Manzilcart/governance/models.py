"""
Governance models: shared moderation bookkeeping and the admin-to-customer chat.

Two concerns live here:

* ``ModerationGovernanceMixin`` - internal staff fields shared by every
  moderated item (``catalog.Listing``, ``auctions.Auction``). The seller-visible
  reason stays in each model's own ``moderation_note``; ``admin_notes`` is for
  staff only.
* ``ListingChatThread`` / ``ListingChatMessage`` - one moderated chat thread per
  fixed-price post **and** per auction, keyed by a contenttypes generic foreign
  key so a single architecture serves both models.
"""

from __future__ import annotations

from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models


class ModerationGovernanceMixin(models.Model):
    """Internal moderation bookkeeping shared by moderated models."""

    admin_notes = models.TextField(
        blank=True,
        help_text="Internal staff notes. Never shown to the seller or buyers.",
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        help_text="Staff member who last recorded a moderation decision.",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        abstract = True


class ListingChatThread(models.Model):
    """Admin ↔ seller conversation about one fixed-price post or one auction."""

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        NEEDS_SELLER_REPLY = "needs_seller_reply", "Needs seller reply"
        RESOLVED = "resolved", "Resolved"

    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.PositiveBigIntegerField()
    item = GenericForeignKey("content_type", "object_id")

    customer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="governance_chat_threads",
        help_text="The seller who posted the item.",
    )
    status = models.CharField(
        max_length=24,
        choices=Status.choices,
        default=Status.OPEN,
        db_index=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    last_message_at = models.DateTimeField(null=True, blank=True, db_index=True)

    class Meta:
        verbose_name = "listing chat thread"
        verbose_name_plural = "listing chat threads"
        constraints = [
            # Exactly one thread per moderated item - the item's chat is part of
            # its identity, not a list of conversations.
            models.UniqueConstraint(
                fields=["content_type", "object_id"],
                name="governancechatthread_one_per_item",
            ),
        ]
        indexes = [
            models.Index(fields=["status", "last_message_at"], name="govchat_status_last_idx"),
        ]

    def __str__(self) -> str:
        return f"Admin chat about {self.item} ({self.get_status_display()})"

    def unread_messages_for_staff(self) -> int:
        """Customer messages staff has not read yet."""
        return self.messages.filter(is_read=False, sender_id=self.customer_id).count()

    def unread_messages_for_customer(self) -> int:
        """Staff messages the customer has not read yet."""
        return self.messages.exclude(sender_id=self.customer_id).filter(is_read=False).count()

    @classmethod
    def unread_staff_threads(cls) -> int:
        """Threads containing at least one unread customer message."""
        return (
            cls.objects.filter(
                messages__is_read=False,
                messages__sender_id=models.F("customer_id"),
            )
            .distinct()
            .count()
        )


class ListingChatMessage(models.Model):
    class SenderRole(models.TextChoices):
        ADMIN = "admin", "Marketplace team"
        CUSTOMER = "customer", "Seller"

    thread = models.ForeignKey(
        ListingChatThread, on_delete=models.CASCADE, related_name="messages"
    )
    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+"
    )
    message = models.TextField(max_length=4000)
    attachment = models.FileField(null=True, blank=True, max_length=255)
    is_read = models.BooleanField(default=False, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "listing chat message"
        verbose_name_plural = "listing chat messages"
        ordering = ["created_at", "id"]
        indexes = [
            models.Index(fields=["thread", "created_at"], name="govchatmsg_thread_idx"),
        ]

    def __str__(self) -> str:
        role_label = dict(self.SenderRole.choices)[self.sender_role]
        return f"{role_label}: {self.message[:40]}"

    @property
    def sender_role(self) -> str:
        staff = getattr(self.sender, "is_staff", False)
        if staff and self.sender_id != self.thread.customer_id:
            return self.SenderRole.ADMIN
        return self.SenderRole.CUSTOMER
