"""Read queries for conversations."""

from __future__ import annotations

from django.db.models import Count, Q

from conversations.models import Conversation, Message


def conversations_for(user):
    """Every thread the user participates in, newest activity first."""
    return (
        Conversation.objects.filter(Q(buyer=user) | Q(seller=user))
        .select_related(
            "listing",
            "listing__emirate",
            "auction",
            "auction__emirate",
            "buyer",
            "seller",
        )
        .prefetch_related("listing__images", "auction__images")
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


def unread_total_for(user) -> int:
    """
    Total unread messages for ``user`` across every thread they participate in.

    Same definition as the per-thread ``unread_count`` annotation: a message the
    other side sent and that has no ``read_at`` yet. Marketplace-team
    (moderation) threads count too, so the shared nav badge covers them.
    """
    if user is None or not getattr(user, "is_authenticated", False):
        return 0
    total = (
        Message.objects.filter(
            Q(conversation__buyer=user) | Q(conversation__seller=user),
            read_at__isnull=True,
        )
        .exclude(sender=user)
        .count()
    )
    # Governance chat uses ``is_read`` (not ``read_at``); staff replies wait
    # unread for the seller until the support thread is opened.
    from governance.models import ListingChatMessage  # lazy: cross-app

    total += ListingChatMessage.objects.filter(
        thread__customer_id=user.pk,
        is_read=False,
    ).exclude(sender_id=user.pk).count()
    return total


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
            "auction",
            "auction__category",
            "auction__emirate",
            "auction__service_area",
            "auction__seller",
            "buyer",
            "seller",
            "emirate",
        )
        .first()
    )


def messages_for(conversation):
    return Message.objects.filter(conversation=conversation).select_related("sender")
