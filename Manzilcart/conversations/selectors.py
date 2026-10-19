"""Read queries for conversations."""

from __future__ import annotations

from django.db.models import Count, Q

from conversations.models import Conversation, Message


def conversations_for(user):
    """Every thread the user participates in, newest activity first."""
    return (
        Conversation.objects.filter(Q(buyer=user) | Q(seller=user))
        .select_related("listing", "listing__emirate", "buyer", "seller")
        .prefetch_related("listing__images")
        .annotate(
            unread_count=Count(
                "messages",
                filter=Q(messages__read_at__isnull=True)
                & ~Q(messages__sender=user),
                distinct=True,
            )
        )
        .order_by("-last_message_at", "-id")
    )


def conversation_for_participant(pk: int, user) -> Conversation | None:
    """
    Fetch a conversation only if ``user`` is one of its two participants.

    Returning ``None`` for both "missing" and "not yours" lets the view raise a plain
    404 without disclosing that someone else's thread exists.
    """
    if user is None or not getattr(user, "is_authenticated", False):
        return None
    return (
        Conversation.objects.filter(pk=pk)
        .filter(Q(buyer=user) | Q(seller=user))
        .select_related(
            "listing",
            "listing__category",
            "listing__emirate",
            "listing__service_area",
            "listing__seller",
            "buyer",
            "seller",
            "emirate",
        )
        .first()
    )


def messages_for(conversation):
    return Message.objects.filter(conversation=conversation).select_related("sender")
