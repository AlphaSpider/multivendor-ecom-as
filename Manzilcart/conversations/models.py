"""
Buyer/seller conversations and their messages.

One conversation exists per (listing, buyer) pair. That is the unit of authorisation:
both the HTTP views and the WebSocket consumer check ``Conversation.is_participant``
before any message is read or written.

Messages are append-only. There is deliberately no edit or delete path: the chat is
evidence for a later dispute, and an edited message would make that evidence
unreliable. Moderation of abusive content happens through staff tooling, not by
rewriting history.
"""

from __future__ import annotations

import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q

from core.models import TimeStampedModel


class Conversation(TimeStampedModel):
    """A buyer's thread about one listing or one standalone auction."""

    listing = models.ForeignKey(
        "catalog.Listing",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="conversations",
    )
    auction = models.ForeignKey(
        "auctions.Auction",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="conversations",
        help_text="Set for a thread about a standalone auction.",
    )
    buyer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="buying_conversations",
    )
    seller = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="selling_conversations",
    )
    # Denormalised for scoping and admin reporting. Derived from the listing when the
    # conversation is created and never accepted from the browser.
    emirate = models.ForeignKey(
        "locations.Emirate",
        on_delete=models.PROTECT,
        related_name="conversations",
    )
    last_message_at = models.DateTimeField(null=True, blank=True, db_index=True)

    class Meta:
        ordering = ["-last_message_at", "-id"]
        verbose_name = "conversation"
        verbose_name_plural = "conversations"
        constraints = [
            models.UniqueConstraint(
                fields=["listing", "buyer"],
                condition=Q(listing__isnull=False),
                name="conversation_unique_listing_buyer",
            ),
            models.UniqueConstraint(
                fields=["auction", "buyer"],
                condition=Q(auction__isnull=False),
                name="conversation_unique_auction_buyer",
            ),
            models.CheckConstraint(
                condition=(
                    (Q(listing__isnull=False) & Q(auction__isnull=True))
                    | (Q(listing__isnull=True) & Q(auction__isnull=False))
                ),
                name="conversation_exactly_one_source",
            ),
            models.CheckConstraint(
                condition=~Q(buyer=F("seller")),
                name="conversation_participants_distinct",
            ),
        ]
        indexes = [
            models.Index(fields=["buyer", "-last_message_at"], name="conversation_buyer_idx"),
            models.Index(fields=["seller", "-last_message_at"], name="conversation_seller_idx"),
            models.Index(fields=["emirate", "-last_message_at"], name="conversation_emirate_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.buyer} / {self.seller} about {self.item_title}"

    # -- item facade --------------------------------------------------------
    @property
    def item_title(self) -> str:
        if self.listing_id:
            return self.listing.title
        return self.auction.title if self.auction_id else "Item"

    @property
    def service_area(self):
        if self.listing_id:
            return self.listing.service_area
        return self.auction.service_area if self.auction_id else None

    @property
    def cover_image(self):
        if self.listing_id:
            return self.listing.cover_image
        return self.auction.cover_image if self.auction_id else None

    @property
    def item_url(self) -> str:
        if self.listing_id:
            return self.listing.get_absolute_url()
        return self.auction.get_absolute_url() if self.auction_id else ""

    @property
    def item_kind(self) -> str:
        return "auction" if self.auction_id else "listing"

    # -- authorisation ------------------------------------------------------
    def is_participant(self, user) -> bool:
        if user is None or not getattr(user, "is_authenticated", False):
            return False
        return user.pk in {self.buyer_id, self.seller_id}

    def other_participant(self, user):
        """The person on the other side of the thread (``None`` for outsiders)."""
        if not self.is_participant(user):
            return None
        return self.seller if user.pk == self.buyer_id else self.buyer


class Message(models.Model):
    """
    A single persistent chat message.

    ``client_message_id`` is generated by the browser for each send attempt. Its
    conditional unique constraint makes retries idempotent: a reconnected socket that
    resends the same message cannot create a duplicate row.
    """

    conversation = models.ForeignKey(
        Conversation,
        on_delete=models.CASCADE,
        related_name="messages",
    )
    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="messages",
    )
    body = models.TextField(max_length=settings.CHAT_MESSAGE_MAX_LENGTH)
    client_message_id = models.UUIDField(
        null=True,
        blank=True,
        default=uuid.uuid4,
        editable=False,
        help_text="Idempotency key supplied by the sending client.",
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["created_at", "id"]
        verbose_name = "message"
        verbose_name_plural = "messages"
        constraints = [
            models.UniqueConstraint(
                fields=["conversation", "client_message_id"],
                condition=Q(client_message_id__isnull=False),
                name="message_unique_client_id_per_conversation",
            ),
        ]
        indexes = [
            models.Index(
                fields=["conversation", "-created_at", "-id"],
                name="message_thread_idx",
            ),
            models.Index(
                fields=["conversation", "sender", "read_at"],
                name="message_unread_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"Message #{self.pk or 'new'} in conversation {self.conversation_id}"

    def clean(self):
        super().clean()
        if not self.body or not self.body.strip():
            raise ValidationError({"body": "Write a message before sending."})
        if self.conversation_id and self.sender_id:
            if self.sender_id not in {
                self.conversation.buyer_id,
                self.conversation.seller_id,
            }:
                raise ValidationError(
                    {"sender": "Only conversation participants can send messages."}
                )
