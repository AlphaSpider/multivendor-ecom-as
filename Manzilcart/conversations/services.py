"""
Conversation and message operations.

The public functions in this module are the only supported write path for chat. They
enforce participant authorisation and message validation, and they publish a durable
message to the WebSocket group only *after* the database transaction commits. A
rolled-back message can therefore never appear in a browser.
"""

from __future__ import annotations

import logging
import uuid

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from conversations.models import Conversation, Message
from core.exceptions import PermissionDeniedError, RateLimitedError
from core.ratelimit import consume

logger = logging.getLogger("manzilcart.conversations")

MESSAGE_EVENT = "chat.message"


def group_name(conversation: Conversation | int) -> str:
    conversation_id = conversation.pk if isinstance(conversation, Conversation) else conversation
    return f"conversation.{conversation_id}"


def message_payload(message: Message) -> dict:
    """JSON-safe representation broadcast to the conversation group."""
    return {
        "id": message.pk,
        "conversation_id": message.conversation_id,
        "sender_id": message.sender_id,
        "sender_name": message.sender.display_name or "ManzilCart member",
        "body": message.body,
        "created_at": message.created_at.isoformat(),
        "client_message_id": (
            str(message.client_message_id) if message.client_message_id else None
        ),
    }


def _broadcast(message: Message) -> None:
    layer = get_channel_layer()
    if layer is None:  # pragma: no cover - misconfiguration is caught by checks
        logger.warning("No channel layer configured; message %s not broadcast", message.pk)
        return
    payload = message_payload(message)
    async_to_sync(layer.group_send)(
        group_name(message.conversation_id),
        {"type": MESSAGE_EVENT, "message": payload},
    )


@transaction.atomic
def start_conversation(
    item=None, buyer=None, *, listing=None, auction=None, request=None
) -> Conversation:
    """
    Return the buyer's existing thread for an item or create it.

    Exactly one of ``listing`` / ``auction`` is used. Only public items can start a
    thread. Once a thread exists it remains readable and writable by both parties even
    if the item is later reserved, awarded or sold, because the history is needed to
    resolve the deal.
    """
    if listing is None and auction is None and item is not None:
        if getattr(item, "_meta", None) is not None and item._meta.model_name == "auction":
            auction = item
        else:
            listing = item
    if (listing is None) == (auction is None):
        raise ValidationError("A conversation must have exactly one source item.")
    source = listing if listing is not None else auction

    if buyer is None or not getattr(buyer, "is_authenticated", False):
        raise PermissionDeniedError("Please sign in to contact the seller.")
    if source.seller_id == buyer.pk:
        raise PermissionDeniedError("You cannot start a conversation about your own item.")
    if not source.is_public:
        raise PermissionDeniedError("This item is not available for new messages.")

    lookup = {"listing": listing} if listing is not None else {"auction": auction}
    try:
        # Savepoint so a concurrent insert that trips the unique constraint does not
        # poison the outer transaction.
        with transaction.atomic():
            conversation, _ = Conversation.objects.get_or_create(
                buyer=buyer,
                defaults={"seller": source.seller, "emirate": source.emirate, **lookup},
                **lookup,
            )
    except IntegrityError:
        # Two tabs / two requests raced; the unique constraint picked a winner.
        conversation = Conversation.objects.get(buyer=buyer, **lookup)

    return conversation


def _normalise_client_id(value) -> uuid.UUID | None:
    if value in (None, ""):
        return None
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError) as exc:
        raise ValidationError({"client_message_id": "Invalid message identifier."}) from exc


def _clean_body(body: str) -> str:
    body = (body or "").strip()
    if not body:
        raise ValidationError({"body": "Write a message before sending."})
    if len(body) > settings.CHAT_MESSAGE_MAX_LENGTH:
        raise ValidationError(
            {"body": f"Messages are limited to {settings.CHAT_MESSAGE_MAX_LENGTH} characters."}
        )
    return body


@transaction.atomic
def send_message(
    conversation: Conversation,
    *,
    sender,
    body: str,
    client_message_id=None,
    request=None,
) -> tuple[Message, bool]:
    """
    Persist a message and schedule the WebSocket broadcast.

    Returns ``(message, created)``. A retry carrying a ``client_message_id`` already
    stored for this conversation returns the original row with ``created=False`` and
    does not broadcast it again.
    """
    if not conversation.is_participant(sender):
        raise PermissionDeniedError("You are not part of this conversation.")

    limit = settings.CHAT_MESSAGES_PER_MINUTE
    allowed, _remaining = consume(
        f"chat:{sender.pk}", limit=limit, window_seconds=60
    )
    if not allowed:
        raise RateLimitedError("You are sending messages too quickly. Please slow down.")

    body = _clean_body(body)
    client_id = _normalise_client_id(client_message_id)

    # Serialise sends within one thread. This protects the idempotency check and the
    # last_message_at ordering without holding a global lock.
    locked = Conversation.objects.select_for_update().get(pk=conversation.pk)

    if client_id is not None:
        existing = Message.objects.filter(
            conversation=locked, client_message_id=client_id
        ).first()
        if existing is not None:
            return existing, False

    message = Message(
        conversation=locked,
        sender=sender,
        body=body,
        client_message_id=client_id,
    )
    message.full_clean()
    message.save()

    now = timezone.now()
    Conversation.objects.filter(pk=locked.pk).update(
        last_message_at=now,
        updated_at=now,
    )
    locked.last_message_at = now

    transaction.on_commit(lambda: _broadcast(message))
    return message, True


@transaction.atomic
def mark_conversation_read(conversation: Conversation, *, reader) -> int:
    """Mark the other participant's messages as read. Returns the number updated."""
    if not conversation.is_participant(reader):
        raise PermissionDeniedError("You are not part of this conversation.")
    return (
        Message.objects.filter(conversation=conversation, read_at__isnull=True)
        .exclude(sender=reader)
        .update(read_at=timezone.now())
    )
