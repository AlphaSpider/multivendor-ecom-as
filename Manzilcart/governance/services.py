"""
Governance services: moderation decisions and the admin ↔ seller chat.

Two layers here:

* **Chat** - generic over any moderated item (``catalog.Listing`` or
  ``auctions.Auction``) through the ``ListingChatThread`` generic foreign key.
* **Moderation decisions** - atomic wrappers the admin actions call. They reuse
  the existing domain services where those exist (``transition_listing``,
  ``reject_auction``…), so no second state machine appears, and add the two new
  states (``changes_requested`` / ``suspended``) plus ``reviewed_by`` bookkeeping
  and a system message into the item's chat thread.
"""

from __future__ import annotations

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Count, F, IntegerField, OuterRef, Subquery, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from auctions import services as auction_services
from auctions.models import Auction, AuctionEvent
from catalog import services as catalog_services
from catalog.models import Listing
from moderation.models import AuctionReview, ListingReview
from core.audit import record_audit
from core.exceptions import PermissionDeniedError, StateTransitionError
from core.models import AuditEvent
from governance.models import ListingChatMessage, ListingChatThread
from notifications.models import Notification
from notifications.services import queue_notification

ChatItem = Listing | Auction

# The moderation statuses shared by both item types; the names below are the
# canonical strings used by the admin actions.
STATUS_TARGETS: dict[type, dict[str, str]] = {
    Listing: {name: getattr(Listing.Status, name.upper()) for name in (
        "pending", "approved", "rejected", "changes_requested", "suspended",
    )},
    Auction: {
        name: getattr(Auction.ModerationStatus, name.upper())
        for name in ("pending", "approved", "rejected", "changes_requested", "suspended")
    },
}


# ---------------------------------------------------------------------------
# Thread handling
# ---------------------------------------------------------------------------


def content_type_for(value):
    """ContentType for 'listing' | 'auction'; anything else returns None."""
    if value == "listing":
        return ContentType.objects.get_for_model(Listing)
    if value == "auction":
        return ContentType.objects.get_for_model(Auction)
    return None


def get_thread(item: ChatItem) -> ListingChatThread:
    """The (single) admin chat thread for a moderated item."""
    content_type = ContentType.objects.get_for_model(type(item))
    thread, _created = ListingChatThread.objects.get_or_create(
        content_type=content_type,
        object_id=item.pk,
        defaults={"customer": item.seller},
    )
    return thread


def _notify_customer(thread: ListingChatThread, *, subject: str, body: str, key: str) -> None:
    queue_notification(
        channel=Notification.Channel.WHATSAPP,
        kind=Notification.Kind.MODERATION,
        recipient=thread.customer.phone,
        subject=subject,
        body=body,
        entity=thread,
        idempotency_key=key,
    )


def _sender_is_customer(thread: ListingChatThread, sender) -> bool:
    return sender is not None and sender.pk == thread.customer_id


@transaction.atomic
def create_message(
    thread: ListingChatThread,
    *,
    sender,
    message: str = "",
    attachment=None,
    system: bool = False,
) -> ListingChatMessage:
    """
    Append one message to a thread; ``system=True`` records an automated note.

    The single ``is_read`` flag belongs to the *recipient*: a customer message
    waits unread for staff (``mark_customer_messages_read``), an admin message
    waits unread for the seller (``mark_staff_messages_read``).
    """
    clean = (message or "").strip()
    if not clean and attachment is None:
        raise ValidationError({"message": "Write a short message first."})
    if len(clean) > 4000:
        raise ValidationError({"message": "Keep messages under 4000 characters."})

    sender_is_customer = _sender_is_customer(thread, sender)
    if not system:
        staff = getattr(sender, "is_staff", False)
        if not sender_is_customer and not staff:
            raise PermissionDeniedError("Only marketplace staff or the seller can post here.")

    message = ListingChatMessage.objects.create(
        thread=thread,
        sender=sender or thread.customer,
        message=clean,
        attachment=attachment,
        is_read=False,
    )
    thread.last_message_at = message.created_at
    thread.status = (
        ListingChatThread.Status.NEEDS_SELLER_REPLY
        if staff_message(sender, thread)
        else ListingChatThread.Status.OPEN
    )
    thread.save(update_fields=["last_message_at", "status"])
    return message


def staff_message(sender, thread: ListingChatThread) -> bool:
    """An admin note or admin reply asks the seller to act."""
    return not _sender_is_customer(thread, sender)


def mark_customer_messages_read(thread: ListingChatThread) -> int:
    """Staff opened the thread: their customer messages are read."""
    return thread.messages.filter(is_read=False, sender_id=thread.customer_id).update(
        is_read=True
    )


def mark_staff_messages_read(thread: ListingChatThread) -> int:
    """The seller opened the thread from their dashboard."""
    return thread.messages.exclude(sender_id=thread.customer_id).filter(is_read=False).update(
        is_read=True
    )


def unread_staff_replies_subquery(content_type):
    """
    ORM expression counting a given item's unread customer messages.

    ``ListingChatThread`` is generic (contenttypes), so there is no reverse
    relation to join: the count is expressed as a correlated subquery instead,
    which keeps the changelists free of N+1 queries.
    """
    unread = ListingChatThread.objects.filter(
        content_type=content_type,
        object_id=OuterRef("pk"),
        messages__is_read=False,
        messages__sender_id=F("customer_id"),
    ).order_by("pk").values("pk").annotate(_c=Count("messages")).values("_c")[:1]
    return Coalesce(Subquery(unread, output_field=IntegerField()), Value(0))


def set_thread_status(thread: ListingChatThread, status: str) -> None:
    thread.status = status
    thread.save(update_fields=["status"])


# ---------------------------------------------------------------------------
# Moderation decision internals
# ---------------------------------------------------------------------------


def _apply_status(item: ChatItem, *, status_key: str, reviewer, note: str) -> ChatItem:
    """Write the governance state for either supported item type."""
    target = STATUS_TARGETS[type(item)][status_key]

    if isinstance(item, Listing):
        locked = catalog_services.transition_listing(
            item, target, actor=reviewer, reason=note
        )
        locked.moderation_note = note
        locked.reviewed_by = reviewer
        locked.reviewed_at = timezone.now()
        locked.save(
            update_fields=["moderation_note", "reviewed_by", "reviewed_at", "updated_at"]
        )
        return locked

    locked = Auction.objects.select_for_update().get(pk=item.pk)
    locked.moderation_status = target
    locked.moderation_note = note
    locked.reviewed_by = reviewer
    locked.reviewed_at = timezone.now()
    locked.save(
        update_fields=[
            "moderation_status",
            "moderation_note",
            "reviewed_by",
            "reviewed_at",
            "updated_at",
        ]
    )
    item.__dict__.update(locked.__dict__)
    return item


def _previous_status(item: ChatItem) -> str:
    return item.status if isinstance(item, Listing) else item.moderation_status


def _record_auction_event(auction: Auction, *, actor, summary: str) -> None:
    auction_services._record_event(  # noqa: SLF001 - deliberate reuse of one helper
        auction, AuctionEvent.Type.REJECTED, actor=actor, summary=summary
    )


def _audit(item: ChatItem, *, actor, summary: str, request=None, metadata=None) -> None:
    kind = (
        AuditEvent.Action.ADMIN_ACTION if isinstance(item, Listing)
        else AuditEvent.Action.AUCTION_REJECTED
    )
    record_audit(
        action=kind,
        actor=actor,
        entity=item,
        emirate=item.emirate,
        summary=summary,
        metadata=metadata,
        request=request,
    )


# ---------------------------------------------------------------------------
# Moderation decisions (public API used by admin actions)
# ---------------------------------------------------------------------------


def approve_listing(listing: Listing, *, reviewer, request=None) -> Listing:
    previous = listing.status
    listing = catalog_services.transition_listing(
        listing, Listing.Status.APPROVED, actor=reviewer, request=request
    )
    listing.reviewed_by = reviewer
    listing.reviewed_at = timezone.now()
    listing.save(update_fields=["reviewed_by", "reviewed_at"])
    ListingReview.objects.create(
        listing=listing,
        reviewer=reviewer,
        decision=ListingReview.Decision.APPROVE,
        reason="",
        previous_status=previous,
        new_status=Listing.Status.APPROVED,
    )
    thread = get_thread(listing)
    create_message(
        thread,
        sender=reviewer,
        system=True,
        message="Your listing has been approved and is now live.",
    )
    set_thread_status(thread, ListingChatThread.Status.RESOLVED)
    _notify_customer(
        thread,
        subject=f"Listing approved — {listing.title}",
        body=(
            f"Your listing “{listing.title}” has been approved and published by "
            "the marketplace team."
        ),
        key=f"gov:{thread.pk}:listing:approved",
    )
    return listing


def request_changes(item: ChatItem, *, reviewer, note: str, request=None) -> ChatItem:
    """CHANGES_REQUESTED + a system chat message and notification for the seller."""
    note = (note or "").strip()
    if not note:
        raise ValidationError({"note": "Tell the seller what to change."})
    thread = get_thread(item)
    previous = _previous_status(item)
    _apply_status(item, status_key="changes_requested", reviewer=reviewer, note=note)

    if isinstance(item, Listing):
        ListingReview.objects.create(
            listing=item,
            reviewer=reviewer,
            decision=ListingReview.Decision.CHANGES_REQUESTED,
            reason=note,
            previous_status=previous,
            new_status=Listing.Status.CHANGES_REQUESTED,
        )
        _audit(
            item,
            actor=reviewer,
            summary=f"Listing {item.slug} changes requested",
            request=request,
            metadata={"note": note},
        )
    else:
        AuctionReview.objects.create(
            auction=item,
            reviewer=reviewer,
            decision=AuctionReview.Decision.REJECT,
            reason=note,
            previous_status=previous,
            new_status=Auction.ModerationStatus.CHANGES_REQUESTED,
        )
        _record_auction_event(item, actor=reviewer, summary=f"Changes requested: {note}")
        _audit(
            item,
            actor=reviewer,
            summary=f"Auction {item.reference} changes requested",
            request=request,
            metadata={"note": note},
        )

    create_message(
        thread,
        sender=reviewer,
        system=True,
        message=(
            "The marketplace team requested changes to your item.\n"
            f"Reason from the reviewer: {note}\n"
            "Please reply here, or edit the item and resubmit it."
        ),
    )
    set_thread_status(thread, ListingChatThread.Status.NEEDS_SELLER_REPLY)
    _notify_customer(
        thread,
        subject="Changes requested on your item",
        body=(
            f"Changes were requested on “{item.title}”.\n"
            f"Reason: {note}\nOpen My account → Marketplace messages to reply."
        ),
        key=f"gov:{thread.pk}:changes_requested:{timezone.now():%Y%m%d%H%M}",
    )
    return item


def reject_item(item: ChatItem, *, reviewer, reason: str, request=None) -> ChatItem:
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError({"reason": "A rejection reason is required."})
    thread = get_thread(item)
    previous = _previous_status(item)
    _apply_status(item, status_key="rejected", reviewer=reviewer, note=reason)

    if isinstance(item, Listing):
        ListingReview.objects.create(
            listing=item,
            reviewer=reviewer,
            decision=ListingReview.Decision.REJECT,
            reason=reason,
            previous_status=previous,
            new_status=Listing.Status.REJECTED,
        )
        _audit(
            item,
            actor=reviewer,
            summary=f"Listing {item.slug} rejected",
            request=request,
            metadata={"reason": reason},
        )
    else:
        auction_services.reject_auction(
            item, reviewer=reviewer, reason=reason, request=request
        )

    create_message(
        thread,
        sender=reviewer,
        system=True,
        message=f"Your item was not approved by the marketplace. Reason: {reason}",
    )
    _notify_customer(
        thread,
        subject="Your item was not approved",
        body=f"“{item.title}” was not approved by the marketplace team.\nReason: {reason}",
        key=f"gov:{thread.pk}:rejected",
    )
    return item


def suspend_item(item: ChatItem, *, reviewer, reason: str, request=None) -> ChatItem:
    """SUSPENDED — an already-public item is delisted immediately."""
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError({"reason": "A suspension reason is required."})
    thread = get_thread(item)
    previous = _previous_status(item)
    _apply_status(item, status_key="suspended", reviewer=reviewer, note=reason)

    if isinstance(item, Listing):
        _audit(
            item,
            actor=reviewer,
            summary=f"Listing {item.slug} suspended",
            request=request,
            metadata={"reason": reason},
        )
    else:
        _record_auction_event(item, actor=reviewer, summary=f"Auction suspended: {reason}")
        _audit(
            item,
            actor=reviewer,
            summary=f"Auction {item.reference} suspended",
            request=request,
            metadata={"reason": reason},
        )
    create_message(
        thread,
        sender=reviewer,
        system=True,
        message=f"Your item has been suspended by the marketplace.\nReason: {reason}",
    )
    set_thread_status(thread, ListingChatThread.Status.NEEDS_SELLER_REPLY)
    _notify_customer(
        thread,
        subject="Your item was suspended",
        body=(
            f"“{item.title}” was suspended by the marketplace team.\nReason: {reason}\n"
            "Open My account → Marketplace messages if you need to reply."
        ),
        key=f"gov:{thread.pk}:suspended:{timezone.now():%Y%m%d%H%M}",
    )
    return item


# ---------------------------------------------------------------------------
# Vendor governance
# ---------------------------------------------------------------------------


@transaction.atomic
def suspend_vendor(vendor, *, actor, request=None) -> None:
    """Set the vendor to suspended and delist all their public items."""
    if not vendor.is_active:
        raise StateTransitionError("This account is already deactivated.")
    vendor.seller_status = "suspended"
    vendor.save(update_fields=["seller_status"])
    for listing in Listing.objects.filter(seller=vendor, status=Listing.Status.APPROVED):
        suspend_item(
            listing,
            reviewer=actor,
            reason="Vendor account suspended by the marketplace.",
            request=request,
        )
    for auction in Auction.objects.filter(
        seller=vendor, moderation_status=Auction.ModerationStatus.APPROVED
    ):
        suspend_item(
            auction,
            reviewer=actor,
            reason="Vendor account suspended by the marketplace.",
            request=request,
        )
    record_audit(
        action=AuditEvent.Action.ADMIN_ACTION,
        actor=actor,
        entity=vendor,
        summary=f"Vendor {vendor} suspended; active items delisted",
        request=request,
    )


@transaction.atomic
def restore_vendor(vendor, *, actor, request=None) -> None:
    """Return the vendor to verified. Suspended items stay suspended; each
    appeal is a separate staff decision on the moderation inbox."""
    vendor.seller_status = "verified"
    vendor.save(update_fields=["seller_status"])
    record_audit(
        action=AuditEvent.Action.ADMIN_ACTION,
        actor=actor,
        entity=vendor,
        summary=f"Vendor {vendor} restored to verified",
        request=request,
    )
