"""
Deal negotiation service layer.

All state changes live here so views stay thin and the rules are testable without HTTP.
Every operation that can race (offer creation, acceptance, cancellation) runs in a
transaction and locks the deal row; acceptance also locks the listing row before it
checks availability.
"""

from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from catalog.models import Listing
from catalog.services import mark_reserved, reopen_listing
from core.audit import record_audit
from core.exceptions import PermissionDeniedError, StateTransitionError
from core.models import AuditEvent
from deals.models import (
    Deal,
    DealCancellation,
    DealEvent,
    DealOffer,
    DealServiceSelection,
)
from locations.models import ServiceOption
from notifications.services import queue_notification
from notifications.models import Notification

logger = logging.getLogger("manzilcart.deals")

ACTIVE_DEAL_STATUSES = (Deal.Status.ACCEPTED, Deal.Status.CANCELLATION_REQUESTED)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _money(value) -> Decimal:
    if isinstance(value, Decimal):
        amount = value
    else:
        try:
            amount = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise ValidationError({"amount": "Enter a valid amount."}) from exc
    if amount < Decimal("1.00"):
        raise ValidationError({"amount": "The offer must be at least AED 1.00."})
    return amount.quantize(Decimal("0.01"))


def ensure_participant(deal: Deal, actor) -> None:
    if not deal.is_participant(actor):
        raise PermissionDeniedError("You are not part of this deal.")


def ensure_staff(actor) -> None:
    if actor is None or not getattr(actor, "is_staff", False):
        raise PermissionDeniedError("Only operations staff can do that.")


def _record_event(deal, event_type, *, actor=None, summary="", metadata=None) -> DealEvent:
    return DealEvent.objects.create(
        deal=deal,
        actor=actor,
        event_type=event_type,
        summary=summary[:255],
        metadata=metadata or {},
    )


def _notify_user(*, user, kind, subject, body, deal, key):
    """Best-effort transactional email/SMS. Never breaks the business transaction."""
    if user is None:
        return
    channel = Notification.Channel.EMAIL if user.email else Notification.Channel.SMS
    recipient = user.email or user.phone
    if not recipient:
        return
    try:
        queue_notification(
            channel=channel,
            kind=kind,
            recipient=recipient,
            subject=subject if channel == Notification.Channel.EMAIL else "",
            body=body,
            user=user,
            emirate=deal.emirate,
            entity=deal,
            idempotency_key=key,
        )
    except Exception:  # noqa: BLE001 - messaging must not undo a valid deal
        logger.exception("Could not queue %s notification for deal %s", kind, deal.pk)


def _notify_ops(deal, *, event: str) -> None:
    selections = list(
        deal.service_selections.select_related("service_option").values_list(
            "service_option__name", flat=True
        )
    )
    services = ", ".join(selections) if selections else "None selected"
    area = deal.service_area
    area_name = area.name if area is not None else "Unknown area"
    body = (
        f"Deal {deal.reference} ({event}).\n"
        f"Item: {deal.item_title} (AED {deal.agreed_price})\n"
        f"Marketplace: {deal.emirate.name} / {area_name}\n"
        f"Source: {deal.item_kind} {deal.item_reference}\n"
        f"Buyer: {deal.buyer.display_name or 'Member'} — {deal.buyer.phone}\n"
        f"Seller: {deal.seller.display_name or 'Member'} — {deal.seller.phone}\n"
        f"Optional services: {services}\n"
        "Coordinate delivery/assembly directly with both parties. "
        "ManzilCart does not collect the furniture payment."
    )
    try:
        queue_notification(
            channel=Notification.Channel.EMAIL,
            kind=Notification.Kind.OPS_SERVICE_REQUEST,
            recipient=settings.OPS_TEAM_EMAIL,
            subject=f"[ManzilCart] Deal {deal.reference} — {event}",
            body=body,
            emirate=deal.emirate,
            entity=deal,
            idempotency_key=f"deal:{deal.pk}:ops:{event}",
        )
    except Exception:  # noqa: BLE001
        logger.exception("Could not queue operations notification for deal %s", deal.pk)


def _selected_options(service_area, option_ids) -> list[ServiceOption]:
    """
    Resolve buyer-supplied ids against *available coverage*.

    Unknown, inactive, or uncovered options are rejected as a set rather than silently
    dropped: the buyer must know that a service is not offered in their area.
    """
    try:
        ids = [int(value) for value in option_ids or []]
    except (TypeError, ValueError) as exc:
        raise ValidationError({"services": "Invalid service selection."}) from exc
    if not ids:
        return []
    available = list(
        ServiceOption.objects.filter(
            pk__in=ids,
            is_active=True,
            coverage__service_area=service_area,
            coverage__is_available=True,
        )
        .distinct()
        .order_by("display_order", "name")
    )
    if len(available) != len(set(ids)):
        raise ValidationError(
            {"services": "One or more of those services are not available in this area."}
        )
    return available


def _write_service_selections(deal, options) -> None:
    DealServiceSelection.objects.filter(deal=deal).delete()
    DealServiceSelection.objects.bulk_create(
        [
            DealServiceSelection(deal=deal, service_option=option)
            for option in options
        ],
        ignore_conflicts=True,
    )


def _create_offer(deal, sender, amount, note="", in_response_to=None) -> DealOffer:
    offer = DealOffer(
        deal=deal,
        sender=sender,
        amount=_money(amount),
        note=(note or "").strip()[:280],
        in_response_to=in_response_to,
    )
    offer.full_clean()
    offer.save()
    return offer


def _pending_offer(deal) -> DealOffer:
    offer = deal.offers.select_for_update().filter(status=DealOffer.Status.PENDING).first()
    if offer is None:
        raise StateTransitionError("There is no offer waiting for a response.")
    return offer


# ---------------------------------------------------------------------------
# Negotiation
# ---------------------------------------------------------------------------


@transaction.atomic
def create_deal(
    *,
    listing: Listing,
    buyer,
    amount,
    service_option_ids=None,
    note="",
    request=None,
) -> Deal:
    """Start a negotiation and record the buyer's first offer."""
    if buyer is None or not getattr(buyer, "is_authenticated", False):
        raise PermissionDeniedError("Please sign in to make an offer.")
    if listing.seller_id == buyer.pk:
        raise PermissionDeniedError("You cannot make an offer on your own listing.")

    locked_listing = (
        Listing.objects.select_for_update()
        .select_related("seller", "emirate", "service_area")
        .get(pk=listing.pk)
    )
    if not locked_listing.is_public:
        raise StateTransitionError("This listing is not available for offers.")
    if Deal.objects.filter(
        listing=locked_listing, status__in=ACTIVE_DEAL_STATUSES
    ).exists():
        raise StateTransitionError("Another buyer already has an accepted deal for this item.")
    if Deal.objects.filter(
        listing=locked_listing, buyer=buyer, status=Deal.Status.NEGOTIATING
    ).exists():
        raise StateTransitionError(
            "You already have an open negotiation for this item. Continue it from your deals page."
        )

    deal = Deal.objects.create(
        listing=locked_listing,
        buyer=buyer,
        seller=locked_listing.seller,
        emirate=locked_listing.emirate,
    )
    offer = _create_offer(deal, buyer, amount, note=note)

    options = _selected_options(locked_listing.service_area, service_option_ids)
    if options:
        _write_service_selections(deal, options)

    _record_event(
        deal,
        DealEvent.Type.OFFER_MADE,
        actor=buyer,
        summary=f"Offer of AED {offer.amount}",
        metadata={"offer_id": offer.pk, "amount": str(offer.amount)},
    )
    _notify_user(
        user=locked_listing.seller,
        kind=Notification.Kind.OFFER_RECEIVED,
        subject=f"New offer on {locked_listing.title}",
        body=(
            f"{buyer.display_name or 'A buyer'} offered AED {offer.amount} for "
            f"{locked_listing.title}. Open your ManzilCart deals page to accept, "
            "reject or counter the offer."
        ),
        deal=deal,
        key=f"deal:{deal.pk}:offer:{offer.pk}:received",
    )
    return deal


@transaction.atomic
def make_offer(deal: Deal, *, actor, amount, note="") -> DealOffer:
    """Put a new offer on the table when nothing is pending."""
    locked = Deal.objects.select_for_update().get(pk=deal.pk)
    ensure_participant(locked, actor)
    if locked.status != Deal.Status.NEGOTIATING:
        raise StateTransitionError("This deal is no longer open for offers.")
    if locked.offers.filter(status=DealOffer.Status.PENDING).exists():
        raise StateTransitionError("Respond to the current offer before making a new one.")

    offer = _create_offer(locked, actor, amount, note=note)
    _record_event(
        locked,
        DealEvent.Type.OFFER_MADE,
        actor=actor,
        summary=f"Offer of AED {offer.amount}",
        metadata={"offer_id": offer.pk, "amount": str(offer.amount)},
    )
    other = locked.other_participant(actor)
    _notify_user(
        user=other,
        kind=Notification.Kind.OFFER_RECEIVED,
        subject=f"New offer on {locked.item_title}",
        body=f"{actor.display_name or 'The other party'} offered AED {offer.amount}.",
        deal=locked,
        key=f"deal:{locked.pk}:offer:{offer.pk}:received",
    )
    return offer


@transaction.atomic
def counter_offer(deal: Deal, *, actor, amount, note="") -> DealOffer:
    """Counter the pending offer; the previous offer is kept in history."""
    locked = Deal.objects.select_for_update().get(pk=deal.pk)
    ensure_participant(locked, actor)
    if locked.status != Deal.Status.NEGOTIATING:
        raise StateTransitionError("This deal is no longer open for offers.")

    previous = _pending_offer(locked)
    if previous.sender_id == actor.pk:
        raise PermissionDeniedError("Wait for the other party to respond to your offer.")

    previous.status = DealOffer.Status.COUNTERED
    previous.responded_at = timezone.now()
    previous.save(update_fields=["status", "responded_at"])

    offer = _create_offer(
        locked, actor, amount, note=note, in_response_to=previous
    )
    _record_event(
        locked,
        DealEvent.Type.OFFER_COUNTERED,
        actor=actor,
        summary=f"Counteroffer of AED {offer.amount}",
        metadata={"offer_id": offer.pk, "previous_offer_id": previous.pk},
    )
    _notify_user(
        user=previous.sender,
        kind=Notification.Kind.OFFER_RECEIVED,
        subject=f"Counteroffer on {locked.item_title}",
        body=f"{actor.display_name or 'The other party'} countered with AED {offer.amount}.",
        deal=locked,
        key=f"deal:{locked.pk}:offer:{offer.pk}:received",
    )
    return offer


@transaction.atomic
def reject_offer(deal: Deal, *, actor, note="") -> DealOffer:
    locked = Deal.objects.select_for_update().get(pk=deal.pk)
    ensure_participant(locked, actor)
    if locked.status != Deal.Status.NEGOTIATING:
        raise StateTransitionError("This deal is no longer open for offers.")

    offer = _pending_offer(locked)
    if offer.sender_id == actor.pk:
        raise PermissionDeniedError("You cannot reject your own offer.")

    offer.status = DealOffer.Status.REJECTED
    offer.responded_at = timezone.now()
    offer.save(update_fields=["status", "responded_at"])
    _record_event(
        locked,
        DealEvent.Type.OFFER_REJECTED,
        actor=actor,
        summary=f"Offer of AED {offer.amount} rejected",
        metadata={"offer_id": offer.pk, "note": (note or "")[:280]},
    )
    _notify_user(
        user=offer.sender,
        kind=Notification.Kind.OFFER_RECEIVED,
        subject=f"Offer declined for {locked.item_title}",
        body="Your offer was declined. You can make a new offer from the deal page.",
        deal=locked,
        key=f"deal:{locked.pk}:offer:{offer.pk}:rejected",
    )
    return offer


@transaction.atomic
def accept_offer(deal: Deal, *, actor, request=None) -> Deal:
    """
    Accept the pending offer and reserve the listing.

    The deal row is locked first, then the listing row. That ordering is shared by every
    acceptance, so two simultaneous acceptances cannot deadlock or both succeed:
    the second one re-reads the listing after the first transaction commits and finds
    it reserved.
    """
    locked = Deal.objects.select_for_update().get(pk=deal.pk)
    ensure_participant(locked, actor)
    if locked.status != Deal.Status.NEGOTIATING:
        raise StateTransitionError("This deal has already been decided.")

    offer = _pending_offer(locked)
    if offer.sender_id == actor.pk:
        raise PermissionDeniedError(
            "Wait for the other party to accept your offer, or withdraw it."
        )

    listing = (
        Listing.objects.select_for_update()
        .select_related("seller", "emirate", "service_area")
        .get(pk=locked.listing_id)
    )
    if not listing.is_public:
        raise StateTransitionError("This listing is no longer available.")

    competing = (
        Deal.objects.select_for_update()
        .filter(listing=listing, status__in=ACTIVE_DEAL_STATUSES)
        .exclude(pk=locked.pk)
        .exists()
    )
    if competing:
        raise StateTransitionError("Another buyer already has an accepted deal for this item.")

    now = timezone.now()
    offer.status = DealOffer.Status.ACCEPTED
    offer.responded_at = now
    offer.save(update_fields=["status", "responded_at"])

    locked.status = Deal.Status.ACCEPTED
    locked.agreed_price = offer.amount
    locked.accepted_offer = offer
    locked.accepted_at = now
    locked.save(
        update_fields=["status", "agreed_price", "accepted_offer", "accepted_at", "updated_at"]
    )

    mark_reserved(listing, actor=actor, request=request)

    _record_event(
        locked,
        DealEvent.Type.DEAL_ACCEPTED,
        actor=actor,
        summary=f"Deal accepted at AED {offer.amount}",
        metadata={"offer_id": offer.pk, "agreed_price": str(offer.amount)},
    )
    record_audit(
        action=AuditEvent.Action.DEAL_ACCEPTED,
        actor=actor,
        entity=locked,
        emirate=locked.emirate,
        summary=f"Deal {locked.reference} accepted at AED {offer.amount}",
        metadata={"listing_id": listing.pk, "offer_id": offer.pk},
        request=request,
    )

    body = (
        f"Deal {locked.reference} is accepted at AED {offer.amount} for "
        f"{listing.title}. Arrange payment and handover directly with the other party."
    )
    for participant in (locked.buyer, locked.seller):
        _notify_user(
            user=participant,
            kind=Notification.Kind.DEAL_ACCEPTED,
            subject=f"Deal accepted — {listing.title}",
            body=body,
            deal=locked,
            key=f"deal:{locked.pk}:accepted:{participant.pk}",
        )
    _notify_ops(locked, event="accepted")
    return locked


@transaction.atomic
def create_awarded_deal(
    *,
    listing=None,
    auction=None,
    buyer,
    amount,
    source: str = "auction",
    request=None,
) -> Deal:
    """
    Create an *accepted* deal without a negotiation round.

    Exactly one source is required. For a listing deal the listing row is locked and
    reserved in the same transaction. For an auction award the auction is already
    approved and the deal is linked to it; no catalog listing exists or is touched.
    The offer history still gets an accepted ``DealOffer`` row at the winning amount,
    so the deal page is consistent with negotiated deals.
    """
    if (listing is None) == (auction is None):
        raise ValidationError("A deal must have exactly one source item.")

    now = timezone.now()

    if listing is not None:
        locked_listing = (
            Listing.objects.select_for_update()
            .select_related("seller", "emirate", "service_area")
            .get(pk=listing.pk)
        )
        if not locked_listing.is_public:
            raise StateTransitionError("This listing is no longer available.")
        seller = locked_listing.seller
        emirate = locked_listing.emirate
        title = locked_listing.title
        if seller.pk == buyer.pk:
            raise PermissionDeniedError("A seller cannot buy their own listing.")
        if Deal.objects.filter(
            listing=locked_listing, status__in=ACTIVE_DEAL_STATUSES
        ).exists():
            raise StateTransitionError("Another accepted deal already exists for this item.")

        deal = Deal.objects.create(
            listing=locked_listing,
            buyer=buyer,
            seller=seller,
            emirate=emirate,
            status=Deal.Status.ACCEPTED,
            agreed_price=_money(amount),
            accepted_at=now,
        )
        mark_reserved(locked_listing, actor=buyer, request=request)
        source_metadata = {"listing_id": locked_listing.pk}
    else:
        if not auction.is_public:
            raise StateTransitionError("This auction is no longer available.")
        if auction.seller_id == buyer.pk:
            raise PermissionDeniedError("A seller cannot buy their own auction.")
        if Deal.objects.filter(
            auction=auction, status__in=ACTIVE_DEAL_STATUSES
        ).exists():
            raise StateTransitionError("This auction has already been awarded.")
        seller = auction.seller
        emirate = auction.emirate
        title = auction.title

        deal = Deal.objects.create(
            auction=auction,
            listing=None,
            buyer=buyer,
            seller=seller,
            emirate=emirate,
            status=Deal.Status.ACCEPTED,
            agreed_price=_money(amount),
            accepted_at=now,
        )
        source_metadata = {"auction_id": auction.pk}

    offer = DealOffer.objects.create(
        deal=deal,
        sender=buyer,
        amount=deal.agreed_price,
        status=DealOffer.Status.ACCEPTED,
        note=f"Winning bid ({source})",
        responded_at=now,
    )
    deal.accepted_offer = offer
    deal.save(update_fields=["accepted_offer", "updated_at"])

    _record_event(
        deal,
        DealEvent.Type.DEAL_ACCEPTED,
        actor=buyer,
        summary=f"Deal accepted at AED {deal.agreed_price} ({source})",
        metadata={"offer_id": offer.pk, "source": source, **source_metadata},
    )
    record_audit(
        action=AuditEvent.Action.DEAL_ACCEPTED,
        actor=buyer,
        entity=deal,
        emirate=deal.emirate,
        summary=f"Deal {deal.reference} accepted at AED {deal.agreed_price} ({source})",
        metadata={"offer_id": offer.pk, "source": source, **source_metadata},
        request=request,
    )

    body = (
        f"Deal {deal.reference} is accepted at AED {deal.agreed_price} for "
        f"{title}. Arrange payment and handover directly with the other party."
    )
    for participant in (deal.buyer, deal.seller):
        _notify_user(
            user=participant,
            kind=Notification.Kind.DEAL_ACCEPTED,
            subject=f"Deal accepted — {title}",
            body=body,
            deal=deal,
            key=f"deal:{deal.pk}:accepted:{participant.pk}",
        )
    _notify_ops(deal, event=f"accepted-{source}")
    return deal


# ---------------------------------------------------------------------------
# Optional services
# ---------------------------------------------------------------------------

@transaction.atomic
def set_service_selections(deal: Deal, *, actor, option_ids) -> Deal:
    """Replace the buyer's optional service selection while negotiating."""
    locked = Deal.objects.select_for_update().get(pk=deal.pk)
    if actor is None or actor.pk != locked.buyer_id:
        raise PermissionDeniedError("Only the buyer can choose optional services.")
    if locked.status != Deal.Status.NEGOTIATING:
        raise StateTransitionError(
            "Services are locked once the deal is accepted; contact operations to change them."
        )

    options = _selected_options(locked.service_area, option_ids)
    _write_service_selections(locked, options)
    _record_event(
        locked,
        DealEvent.Type.SERVICES_UPDATED,
        actor=actor,
        summary=", ".join(option.name for option in options) or "No optional services",
        metadata={"service_option_ids": [option.pk for option in options]},
    )
    return locked


# ---------------------------------------------------------------------------
# Cancellation
# ---------------------------------------------------------------------------


@transaction.atomic
def request_cancellation(deal: Deal, *, actor, reason: str, request=None) -> DealCancellation:
    locked = Deal.objects.select_for_update().get(pk=deal.pk)
    ensure_participant(locked, actor)
    if locked.status != Deal.Status.ACCEPTED:
        raise StateTransitionError("Only an accepted deal can be cancelled.")
    reason = (reason or "").strip()
    if len(reason) < 5:
        raise ValidationError({"reason": "Please explain why the deal should be cancelled."})
    if locked.cancellations.filter(
        status__in=[DealCancellation.Status.PENDING, DealCancellation.Status.ESCALATED]
    ).exists():
        raise StateTransitionError("A cancellation request is already open for this deal.")

    locked.status = Deal.Status.CANCELLATION_REQUESTED
    locked.save(update_fields=["status", "updated_at"])

    cancellation = DealCancellation.objects.create(
        deal=locked, requested_by=actor, reason=reason
    )
    _record_event(
        locked,
        DealEvent.Type.CANCELLATION_REQUESTED,
        actor=actor,
        summary=f"Cancellation requested by {actor.display_name or 'a participant'}",
        metadata={"cancellation_id": cancellation.pk, "reason": reason[:500]},
    )
    record_audit(
        action=AuditEvent.Action.DEAL_CANCELLATION_REQUESTED,
        actor=actor,
        entity=locked,
        emirate=locked.emirate,
        summary=f"Cancellation requested for {locked.reference}",
        metadata={"cancellation_id": cancellation.pk},
        request=request,
    )
    other = locked.other_participant(actor)
    _notify_user(
        user=other,
        kind=Notification.Kind.DEAL_CANCELLATION,
        subject=f"Cancellation requested — {locked.reference}",
        body=(
            f"{actor.display_name or 'The other party'} asked to cancel deal "
            f"{locked.reference}. Reason: {reason}. "
            "You can accept or reject the request from the deal page."
        ),
        deal=locked,
        key=f"deal:{locked.pk}:cancel:{cancellation.pk}:requested",
    )
    return cancellation


def _finalize_cancellation(
    deal: Deal,
    *,
    actor,
    cancellation: DealCancellation | None,
    note: str,
    request=None,
    by_staff: bool,
) -> Deal:
    now = timezone.now()
    if cancellation is not None:
        cancellation.status = (
            DealCancellation.Status.FINALIZED
            if by_staff
            else DealCancellation.Status.ACCEPTED
        )
        cancellation.responded_by = actor
        cancellation.response_note = (note or "")[:1000]
        cancellation.responded_at = now
        cancellation.resolved_at = now
        cancellation.save(
            update_fields=[
                "status",
                "responded_by",
                "response_note",
                "responded_at",
                "resolved_at",
                "updated_at",
            ]
        )

    deal.status = Deal.Status.CANCELLED
    deal.cancelled_at = now
    deal.save(update_fields=["status", "cancelled_at", "updated_at"])

    listing = None
    reopened = False
    if deal.listing_id:
        listing = Listing.objects.select_for_update().get(pk=deal.listing_id)
        if listing.status == Listing.Status.RESERVED:
            reopen_listing(listing, actor=actor, request=request)
            reopened = True
            _record_event(
                deal,
                DealEvent.Type.LISTING_REOPENED,
                actor=actor,
                summary="Listing returned to the marketplace",
            )

    _record_event(
        deal,
        DealEvent.Type.CANCELLATION_FINALIZED,
        actor=actor,
        summary="Cancellation finalised" + (" by operations" if by_staff else ""),
        metadata={"listing_reopened": reopened},
    )
    record_audit(
        action=(
            AuditEvent.Action.DEAL_CANCELLED
            if by_staff
            else AuditEvent.Action.DEAL_CANCELLATION_APPROVED
        ),
        actor=actor,
        entity=deal,
        emirate=deal.emirate,
        summary=f"Deal {deal.reference} cancelled",
        metadata={
            "listing_id": listing.pk if listing is not None else None,
            "auction_id": deal.auction_id,
            "reopened": reopened,
        },
        request=request,
    )

    availability = (
        "available again."
        if reopened
        else ("no longer available." if listing is not None else "closed.")
    )
    for participant in (deal.buyer, deal.seller):
        _notify_user(
            user=participant,
            kind=Notification.Kind.DEAL_CANCELLATION,
            subject=f"Deal cancelled — {deal.reference}",
            body=(
                f"Deal {deal.reference} has been cancelled. The item is {availability}"
            ),
            deal=deal,
            key=f"deal:{deal.pk}:cancel:final:{participant.pk}",
        )
    return deal


@transaction.atomic
def respond_to_cancellation(
    cancellation: DealCancellation, *, actor, accept: bool, note="", request=None
) -> Deal:
    locked_cancellation = (
        DealCancellation.objects.select_for_update()
        .select_related("deal")
        .get(pk=cancellation.pk)
    )
    deal = Deal.objects.select_for_update().get(
        pk=locked_cancellation.deal_id
    )
    if locked_cancellation.status != DealCancellation.Status.PENDING:
        raise StateTransitionError("This cancellation request has already been answered.")
    ensure_participant(deal, actor)
    if locked_cancellation.requested_by_id == actor.pk:
        raise PermissionDeniedError("The other party must respond to your request.")

    if accept:
        return _finalize_cancellation(
            deal,
            actor=actor,
            cancellation=locked_cancellation,
            note=note,
            request=request,
            by_staff=False,
        )

    locked_cancellation.status = DealCancellation.Status.REJECTED
    locked_cancellation.responded_by = actor
    locked_cancellation.response_note = (note or "")[:1000]
    locked_cancellation.responded_at = timezone.now()
    locked_cancellation.save(
        update_fields=[
            "status",
            "responded_by",
            "response_note",
            "responded_at",
            "updated_at",
        ]
    )
    deal.status = Deal.Status.ACCEPTED
    deal.save(update_fields=["status", "updated_at"])

    _record_event(
        deal,
        DealEvent.Type.CANCELLATION_REJECTED,
        actor=actor,
        summary="Cancellation request rejected",
        metadata={"cancellation_id": locked_cancellation.pk},
    )
    record_audit(
        action=AuditEvent.Action.DEAL_CANCELLATION_REJECTED,
        actor=actor,
        entity=deal,
        emirate=deal.emirate,
        summary=f"Cancellation rejected for {deal.reference}",
        metadata={"cancellation_id": locked_cancellation.pk},
        request=request,
    )
    _notify_user(
        user=locked_cancellation.requested_by,
        kind=Notification.Kind.DEAL_CANCELLATION,
        subject=f"Cancellation declined — {deal.reference}",
        body=(
            "The other party declined the cancellation request. You can escalate the "
            "dispute to ManzilCart operations from the deal page."
        ),
        deal=deal,
        key=f"deal:{deal.pk}:cancel:{locked_cancellation.pk}:rejected",
    )
    return deal


@transaction.atomic
def staff_reject_cancellation(
    cancellation: DealCancellation, *, actor, note="", request=None
) -> Deal:
    """
    Operations declines a cancellation dispute: the deal stands.

    The participant flow (``respond_to_cancellation``) is seller/buyer-only;
    staff need the same outcome from the governance financials page, including
    for ESCALATED rows that a participant cannot answer.
    """
    ensure_staff(actor)
    locked_cancellation = (
        DealCancellation.objects.select_for_update()
        .select_related("deal")
        .get(pk=cancellation.pk)
    )
    deal = Deal.objects.select_for_update().get(pk=locked_cancellation.deal_id)
    if locked_cancellation.status not in {
        DealCancellation.Status.PENDING,
        DealCancellation.Status.ESCALATED,
    }:
        raise StateTransitionError("This cancellation request has already been answered.")

    locked_cancellation.status = DealCancellation.Status.REJECTED
    locked_cancellation.responded_by = actor
    locked_cancellation.response_note = (note or "")[:1000]
    locked_cancellation.responded_at = timezone.now()
    locked_cancellation.resolved_at = timezone.now()
    locked_cancellation.save(
        update_fields=[
            "status",
            "responded_by",
            "response_note",
            "responded_at",
            "resolved_at",
            "updated_at",
        ]
    )
    deal.status = Deal.Status.ACCEPTED
    deal.save(update_fields=["status", "updated_at"])

    _record_event(
        deal,
        DealEvent.Type.CANCELLATION_REJECTED,
        actor=actor,
        summary="Cancellation request rejected by operations",
        metadata={"cancellation_id": locked_cancellation.pk},
    )
    record_audit(
        action=AuditEvent.Action.DEAL_CANCELLATION_REJECTED,
        actor=actor,
        entity=deal,
        emirate=deal.emirate,
        summary=f"Cancellation rejected by operations for {deal.reference}",
        metadata={"cancellation_id": locked_cancellation.pk},
        request=request,
    )
    _notify_user(
        user=locked_cancellation.requested_by,
        kind=Notification.Kind.DEAL_CANCELLATION,
        subject=f"Cancellation declined — {deal.reference}",
        body=(
            "ManzilCart operations reviewed the dispute and the deal stands. "
            "Please coordinate with the other party to complete it."
        ),
        deal=deal,
        key=f"deal:{deal.pk}:cancel:{locked_cancellation.pk}:ops-rejected",
    )
    return deal


@transaction.atomic
def escalate_cancellation(
    cancellation: DealCancellation, *, actor, note="", request=None
) -> DealCancellation:
    locked = (
        DealCancellation.objects.select_for_update()
        .select_related("deal", "deal__emirate")
        .get(pk=cancellation.pk)
    )
    deal = Deal.objects.select_for_update().get(
        pk=locked.deal_id
    )
    if not (deal.is_participant(actor) or getattr(actor, "is_staff", False)):
        raise PermissionDeniedError("You cannot escalate this cancellation.")
    if locked.status not in {
        DealCancellation.Status.PENDING,
        DealCancellation.Status.REJECTED,
    }:
        raise StateTransitionError("This cancellation can no longer be escalated.")
    if deal.status not in {Deal.Status.ACCEPTED, Deal.Status.CANCELLATION_REQUESTED}:
        raise StateTransitionError("This deal is already closed.")

    locked.status = DealCancellation.Status.ESCALATED
    locked.response_note = (note or locked.response_note)[:1000]
    locked.save(update_fields=["status", "response_note", "updated_at"])
    if deal.status != Deal.Status.CANCELLATION_REQUESTED:
        deal.status = Deal.Status.CANCELLATION_REQUESTED
        deal.save(update_fields=["status", "updated_at"])

    _record_event(
        deal,
        DealEvent.Type.CANCELLATION_ESCALATED,
        actor=actor,
        summary="Cancellation escalated to operations",
        metadata={"cancellation_id": locked.pk},
    )
    record_audit(
        action=AuditEvent.Action.DEAL_CANCELLATION_ESCALATED,
        actor=actor,
        entity=deal,
        emirate=deal.emirate,
        summary=f"Cancellation escalated for {deal.reference}",
        metadata={"cancellation_id": locked.pk},
        request=request,
    )
    _notify_ops(deal, event=f"cancellation-escalated-{locked.pk}")
    return locked


@transaction.atomic
def finalize_cancellation(
    deal: Deal, *, actor, note="", request=None
) -> Deal:
    """Staff-only final decision on a disputed cancellation."""
    ensure_staff(actor)
    locked = Deal.objects.select_for_update().get(pk=deal.pk)
    if locked.status not in {Deal.Status.ACCEPTED, Deal.Status.CANCELLATION_REQUESTED}:
        raise StateTransitionError("This deal is not open for cancellation.")
    cancellation = (
        locked.cancellations.filter(
            status__in=[
                DealCancellation.Status.PENDING,
                DealCancellation.Status.ESCALATED,
                DealCancellation.Status.REJECTED,
            ]
        )
        .order_by("-created_at")
        .first()
    )
    return _finalize_cancellation(
        locked,
        actor=actor,
        cancellation=cancellation,
        note=note,
        request=request,
        by_staff=True,
    )
