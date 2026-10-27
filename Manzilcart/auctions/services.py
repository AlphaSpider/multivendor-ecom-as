"""
Auction service layer: moderation, creation, bidding, seller lock and fallback.

All deadline processing is idempotent and row-locked, so two workers (or a worker and
the bidder's browser) cannot award an auction twice. The auction row is locked first.
An auction award creates a deal linked to the auction (never to a catalog listing).
"""

from __future__ import annotations

import logging
import uuid
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from io import BytesIO

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.core import signing
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from auctions.models import Auction, AuctionEvent, AuctionImage, AuctionSelection, Bid
from catalog.models import Category
from core.audit import record_audit
from core.exceptions import PermissionDeniedError, StateTransitionError
from core.images import sanitise_image, validate_image_upload
from core.models import AuditEvent
from deals.services import create_awarded_deal
from locations.validation import validate_emirate_service_area
from notifications.models import Notification
from notifications.services import queue_notification

logger = logging.getLogger("manzilcart.auctions")

SELECTION_SALT = "manzilcart.auction-selection"
ACTIVE_STATUSES = (
    Auction.Status.SCHEDULED,
    Auction.Status.LIVE,
    Auction.Status.LOCKED,
)
EDITABLE_MODERATION_STATUSES = (
    Auction.ModerationStatus.DRAFT,
    Auction.ModerationStatus.REJECTED,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _money(value) -> Decimal:
    try:
        amount = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValidationError({"amount": "Enter a valid bid amount."}) from exc
    if amount < Decimal("1.00"):
        raise ValidationError({"amount": "Bids must be at least AED 1.00."})
    return amount.quantize(Decimal("0.01"))


def _minimum_amount(value) -> Decimal:
    """The minimum amount is mandatory and doubles as the auction's starting price."""
    if value in (None, ""):
        raise ValidationError(
            {"reserve_price": "Enter the minimum amount (the auction's starting price)."}
        )
    try:
        amount = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValidationError(
            {"reserve_price": "Enter a valid minimum amount."}
        ) from exc
    if amount < Decimal("1.00"):
        raise ValidationError(
            {"reserve_price": "The minimum amount must be at least AED 1.00."}
        )
    return amount.quantize(Decimal("0.01"))


def _record_event(auction, event_type, *, actor=None, summary="", metadata=None):
    return AuctionEvent.objects.create(
        auction=auction,
        actor=actor,
        event_type=event_type,
        summary=summary[:255],
        metadata=metadata or {},
    )


def _notify_user(*, user, kind, subject, body, auction, key):
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
            emirate=auction.emirate,
            entity=auction,
            idempotency_key=key,
        )
    except Exception:  # noqa: BLE001 - notifications must never undo an auction state
        logger.exception("Could not queue %s for auction %s", kind, auction.pk)


def _ensure_owner(auction: Auction, actor) -> None:
    if actor is None or not getattr(actor, "is_authenticated", False):
        raise PermissionDeniedError("Please sign in to manage this auction.")
    if auction.seller_id != actor.pk:
        raise PermissionDeniedError("You cannot change this auction.")


def _validate_schedule(start_at, end_at, *, now=None) -> tuple:
    """Validate and normalise an auction schedule. Shared by create and edit."""
    now = now or timezone.now()
    if start_at is None or end_at is None:
        raise ValidationError({"end_at": "Choose a start and end time."})
    if end_at <= start_at:
        raise ValidationError({"end_at": "The end time must be after the start time."})
    if end_at <= now:
        raise ValidationError({"end_at": "The auction must end in the future."})
    if start_at < now - timedelta(minutes=1):
        start_at = now
    duration = end_at - start_at
    if duration < timedelta(hours=settings.AUCTION_MIN_DURATION_HOURS):
        raise ValidationError(
            {
                "end_at": (
                    "Auctions must run for at least "
                    f"{settings.AUCTION_MIN_DURATION_HOURS} hour(s)."
                )
            }
        )
    if duration > timedelta(days=settings.AUCTION_MAX_DURATION_DAYS):
        raise ValidationError(
            {
                "end_at": (
                    "Auctions can run for at most "
                    f"{settings.AUCTION_MAX_DURATION_DAYS} days."
                )
            }
        )
    return start_at, end_at


def _validate_content(*, title, description, category, condition) -> None:
    if not (title or "").strip():
        raise ValidationError({"title": "Give the item a title."})
    if len((description or "").strip()) < 20:
        raise ValidationError(
            {"description": "Please write at least 20 characters about the item."}
        )
    if category is None or not category.is_active:
        raise ValidationError({"category": "Please choose an available category."})
    if category.parent_id is None and category.children.filter(is_active=True).exists():
        raise ValidationError({"category": "Please choose a more specific category."})
    valid_conditions = {choice[0] for choice in Auction.Condition.choices}
    if condition not in valid_conditions:
        raise ValidationError({"condition": "Choose a valid condition."})


def eligible_bids(auction: Auction) -> list[Bid]:
    """
    One row per bidder: their highest bid, earliest first on a tie.

    Bids below the starting price are excluded. Ordering by ``-amount, created_at, id``
    before de-duplicating is what makes the tie-break rule explicit and deterministic.
    """
    starting_price = auction.reserve_price
    seen: set[int] = set()
    rows: list[Bid] = []
    bids = (
        auction.bids.select_related("bidder")
        .filter(voided_at__isnull=True)
        .order_by("-amount", "created_at", "id")
    )
    for bid in bids:
        if bid.bidder_id in seen:
            continue
        if starting_price is not None and bid.amount < starting_price:
            continue
        seen.add(bid.bidder_id)
        rows.append(bid)
    return rows


def highest_eligible_bid(auction: Auction) -> Bid | None:
    rows = eligible_bids(auction)
    return rows[0] if rows else None


def selection_token(selection: AuctionSelection) -> str:
    return signing.dumps({"selection": selection.pk}, salt=SELECTION_SALT)


def selection_from_token(token: str) -> AuctionSelection | None:
    try:
        payload = signing.loads(token, salt=SELECTION_SALT)
    except (signing.BadSignature, signing.SignatureExpired):
        return None
    selection_id = payload.get("selection") if isinstance(payload, dict) else None
    if not selection_id:
        return None
    return (
        AuctionSelection.objects.select_related(
            "auction", "auction__emirate", "bidder", "bid"
        )
        .filter(pk=selection_id)
        .first()
    )


def _selection_link(selection: AuctionSelection) -> str:
    path = reverse("auctions:selection", kwargs={"token": selection_token(selection)})
    return f"{settings.SITE_URL}{path}"


def _send_selection_invite(selection: AuctionSelection) -> None:
    auction = selection.auction
    deadline = selection.confirmation_deadline
    minutes = max(1, int((deadline - timezone.now()).total_seconds() // 60))
    kind = (
        Notification.Kind.AUCTION_FALLBACK
        if selection.sequence > 1
        else Notification.Kind.AUCTION_SELECTION
    )
    body = (
        f"You have been selected for {auction.reference} — {auction.title} "
        f"at AED {selection.bid.amount}. Confirm or decline within {minutes} minute(s): "
        f"{_selection_link(selection)}\n"
        "If you do not respond by the deadline, the auction is offered to the next "
        "highest bidder. This is not a payment request; payment is arranged directly "
        "with the seller."
    )
    _notify_user(
        user=selection.bidder,
        kind=kind,
        subject=f"Confirm your winning bid — {auction.reference}",
        body=body,
        auction=auction,
        key=f"auction:{auction.pk}:selection:{selection.pk}:invited",
    )


# ---------------------------------------------------------------------------
# Creation and seller edits
# ---------------------------------------------------------------------------


@transaction.atomic
def create_auction(
    *,
    seller,
    emirate,
    title: str,
    description: str,
    category: Category,
    condition: str,
    service_area,
    start_at,
    end_at,
    reserve_price=None,
    agree_fallback: bool = False,
    submit_for_review: bool = True,
    request=None,
) -> Auction:
    """Create a standalone auction item. No catalog listing is created."""
    if not agree_fallback:
        raise ValidationError(
            {"agree_fallback": "You must accept the fallback rules before starting."}
        )
    emirate, service_area = validate_emirate_service_area(emirate, service_area)
    _validate_content(
        title=title, description=description, category=category, condition=condition
    )
    start_at, end_at = _validate_schedule(start_at, end_at)
    minimum_amount = _minimum_amount(reserve_price)

    now = timezone.now()
    auction = Auction(
        seller=seller,
        title=title.strip(),
        description=description.strip(),
        category=category,
        condition=condition,
        emirate=emirate,
        service_area=service_area,
        start_at=start_at,
        end_at=end_at,
        reserve_price=minimum_amount,
        # Approval is required before the worker may open this auction.
        status=Auction.Status.SCHEDULED,
        moderation_status=(
            Auction.ModerationStatus.PENDING
            if submit_for_review
            else Auction.ModerationStatus.DRAFT
        ),
        submitted_at=now if submit_for_review else None,
        seller_agreed_fallback=True,
    )
    auction.full_clean(exclude=["approved_by"])
    auction.save()

    _record_event(
        auction,
        AuctionEvent.Type.CREATED,
        actor=seller,
        summary=f"Auction {auction.reference} created",
        metadata={
            "start_at": start_at.isoformat(),
            "end_at": end_at.isoformat(),
            "minimum_amount": str(minimum_amount),
        },
    )
    if submit_for_review:
        _record_event(
            auction,
            AuctionEvent.Type.SUBMITTED,
            actor=seller,
            summary="Submitted for moderation",
        )
    record_audit(
        action=(
            AuditEvent.Action.AUCTION_SUBMITTED
            if submit_for_review
            else AuditEvent.Action.ADMIN_ACTION
        ),
        actor=seller,
        entity=auction,
        emirate=auction.emirate,
        summary=(
            f"Auction {auction.reference} submitted for review"
            if submit_for_review
            else f"Auction {auction.reference} saved as draft"
        ),
        request=request,
    )
    return auction


@transaction.atomic
def update_auction(
    auction: Auction,
    *,
    actor,
    title: str,
    description: str,
    category: Category,
    condition: str,
    service_area,
    start_at,
    end_at,
    reserve_price=None,
    agree_fallback: bool = False,
    submit_for_review: bool = True,
    request=None,
) -> Auction:
    """
    Edit an auction.

    Drafts and rejected auctions can be edited freely. An approved auction may only be
    edited before its first bid; doing so puts it back into moderation. Once bidding has
    started, content is frozen.
    """
    _ensure_owner(auction, actor)
    locked = Auction.objects.select_for_update().select_related("emirate").get(pk=auction.pk)
    if not locked.is_editable_by_owner:
        raise StateTransitionError(
            "This auction can no longer be edited. Bidding has already started."
        )
    if not agree_fallback:
        raise ValidationError(
            {"agree_fallback": "You must accept the fallback rules before saving."}
        )

    emirate, service_area = validate_emirate_service_area(locked.emirate, service_area)
    _validate_content(
        title=title, description=description, category=category, condition=condition
    )
    start_at, end_at = _validate_schedule(start_at, end_at)
    minimum_amount = _minimum_amount(reserve_price)

    locked.title = title.strip()
    locked.description = description.strip()
    locked.category = category
    locked.condition = condition
    locked.service_area = service_area
    locked.start_at = start_at
    locked.end_at = end_at
    locked.reserve_price = minimum_amount
    locked.seller_agreed_fallback = True

    was_approved = locked.moderation_status == Auction.ModerationStatus.APPROVED
    if was_approved or submit_for_review:
        locked.moderation_status = Auction.ModerationStatus.PENDING
        locked.submitted_at = timezone.now()
        locked.moderation_note = ""
        # Content is no longer public until a moderator approves it again.
        locked.status = Auction.Status.SCHEDULED
    elif locked.moderation_status == Auction.ModerationStatus.DRAFT:
        locked.status = Auction.Status.SCHEDULED

    locked.full_clean(exclude=["approved_by"])
    locked.save()

    if locked.moderation_status == Auction.ModerationStatus.PENDING:
        _record_event(
            locked,
            AuctionEvent.Type.SUBMITTED,
            actor=actor,
            summary="Edited and resubmitted for moderation",
        )
        record_audit(
            action=AuditEvent.Action.AUCTION_SUBMITTED,
            actor=actor,
            entity=locked,
            emirate=emirate,
            summary=f"Auction {locked.reference} edited and resubmitted",
            request=request,
        )
    else:
        record_audit(
            action=AuditEvent.Action.ADMIN_ACTION,
            actor=actor,
            entity=locked,
            emirate=emirate,
            summary=f"Auction {locked.reference} saved as draft",
            request=request,
        )
    return locked


@transaction.atomic
def submit_auction(auction: Auction, *, actor, request=None) -> Auction:
    _ensure_owner(auction, actor)
    locked = Auction.objects.select_for_update().select_related("emirate").get(pk=auction.pk)
    if locked.moderation_status not in EDITABLE_MODERATION_STATUSES:
        raise StateTransitionError("This auction is not waiting for a resubmission.")
    # Photos are how bidders judge condition: never let an under-photographed
    # auction reach moderation through a path that skipped the form-level check.
    if locked.images.count() < settings.MIN_IMAGES_PER_LISTING:
        raise StateTransitionError(
            f"Add at least {settings.MIN_IMAGES_PER_LISTING} photos before submitting "
            "for review."
        )
    locked.moderation_status = Auction.ModerationStatus.PENDING
    locked.moderation_note = ""
    locked.submitted_at = timezone.now()
    locked.save(
        update_fields=["moderation_status", "moderation_note", "submitted_at", "updated_at"]
    )
    _record_event(
        locked,
        AuctionEvent.Type.SUBMITTED,
        actor=actor,
        summary="Submitted for moderation",
    )
    record_audit(
        action=AuditEvent.Action.AUCTION_SUBMITTED,
        actor=actor,
        entity=locked,
        emirate=locked.emirate,
        summary=f"Auction {locked.reference} submitted for review",
        request=request,
    )
    return locked


# ---------------------------------------------------------------------------
# Moderation
# ---------------------------------------------------------------------------


@transaction.atomic
def approve_auction(auction: Auction, *, reviewer, request=None) -> Auction:
    """Approve a pending auction. Lifecycle status is deliberately unchanged."""
    locked = Auction.objects.select_for_update().select_related("emirate").get(pk=auction.pk)
    if locked.moderation_status != Auction.ModerationStatus.PENDING:
        raise StateTransitionError("Only a pending auction can be approved.")
    previous = locked.moderation_status
    now = timezone.now()
    locked.moderation_status = Auction.ModerationStatus.APPROVED
    locked.moderation_note = ""
    locked.approved_at = now
    locked.approved_by = reviewer
    locked.save(
        update_fields=[
            "moderation_status",
            "moderation_note",
            "approved_at",
            "approved_by",
            "updated_at",
        ]
    )
    _record_event(
        locked,
        AuctionEvent.Type.APPROVED,
        actor=reviewer,
        summary="Auction approved",
    )
    from moderation.models import AuctionReview

    AuctionReview.objects.create(
        auction=locked,
        reviewer=reviewer,
        decision=AuctionReview.Decision.APPROVE,
        previous_status=previous,
        new_status=Auction.ModerationStatus.APPROVED,
    )
    record_audit(
        action=AuditEvent.Action.AUCTION_APPROVED,
        actor=reviewer,
        entity=locked,
        emirate=locked.emirate,
        summary=f"Auction {locked.reference} approved",
        metadata={"previous_status": previous},
        request=request,
    )
    _notify_user(
        user=locked.seller,
        kind=Notification.Kind.AUCTION_OUTCOME,
        subject=f"Auction approved — {locked.reference}",
        body=(
            f"Your auction “{locked.title}” ({locked.reference}) has been approved. "
            f"It opens on {locked.start_at:%d %b %Y %H:%M} and closes on "
            f"{locked.end_at:%d %b %Y %H:%M}."
        ),
        auction=locked,
        key=f"auction:{locked.pk}:moderation:approved",
    )
    return locked


@transaction.atomic
def reject_auction(auction: Auction, *, reviewer, reason: str, request=None) -> Auction:
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError({"reason": "A rejection reason is required."})
    locked = Auction.objects.select_for_update().select_related("emirate").get(pk=auction.pk)
    if locked.moderation_status != Auction.ModerationStatus.PENDING:
        raise StateTransitionError("Only a pending auction can be rejected.")
    previous = locked.moderation_status
    locked.moderation_status = Auction.ModerationStatus.REJECTED
    locked.moderation_note = reason
    locked.save(
        update_fields=["moderation_status", "moderation_note", "updated_at"]
    )
    _record_event(
        locked,
        AuctionEvent.Type.REJECTED,
        actor=reviewer,
        summary=reason,
    )
    from moderation.models import AuctionReview

    AuctionReview.objects.create(
        auction=locked,
        reviewer=reviewer,
        decision=AuctionReview.Decision.REJECT,
        reason=reason,
        previous_status=previous,
        new_status=Auction.ModerationStatus.REJECTED,
    )
    record_audit(
        action=AuditEvent.Action.AUCTION_REJECTED,
        actor=reviewer,
        entity=locked,
        emirate=locked.emirate,
        summary=f"Auction {locked.reference} rejected",
        metadata={"reason": reason, "previous_status": previous},
        request=request,
    )
    _notify_user(
        user=locked.seller,
        kind=Notification.Kind.AUCTION_OUTCOME,
        subject=f"Auction needs changes — {locked.reference}",
        body=(
            f"Your auction “{locked.title}” ({locked.reference}) was not approved.\n"
            f"Reason: {reason}\n"
            "Edit the auction from My auctions and submit it again."
        ),
        auction=locked,
        key=f"auction:{locked.pk}:moderation:rejected",
    )
    return locked


# ---------------------------------------------------------------------------
# Admin auction controls (bid monitor)
# ---------------------------------------------------------------------------


def _ensure_staff(actor) -> None:
    if actor is None or not getattr(actor, "is_staff", False):
        raise PermissionDeniedError("Marketplace staff only.")


@transaction.atomic
def pause_auction(auction: Auction, *, actor, request=None) -> Auction:
    """Emergency pause: the page stays up but bids are refused until resumed."""
    _ensure_staff(actor)
    locked = Auction.objects.select_for_update().get(pk=auction.pk)
    if locked.status != Auction.Status.LIVE:
        raise StateTransitionError("Only a live auction can be paused.")
    locked.status = Auction.Status.PAUSED
    locked.save(update_fields=["status", "updated_at"])
    _record_event(
        locked,
        AuctionEvent.Type.PAUSED,
        actor=actor,
        summary="Auction paused by admin",
    )
    record_audit(
        action=AuditEvent.Action.ADMIN_ACTION,
        actor=actor,
        entity=locked,
        emirate=locked.emirate,
        summary=f"Auction {locked.reference} paused by admin",
        request=request,
    )
    return locked


@transaction.atomic
def resume_auction(auction: Auction, *, actor, request=None) -> Auction:
    _ensure_staff(actor)
    locked = Auction.objects.select_for_update().get(pk=auction.pk)
    if locked.status != Auction.Status.PAUSED:
        raise StateTransitionError("Only a paused auction can be resumed.")
    now = timezone.now()
    if locked.end_at <= now:
        raise StateTransitionError("This auction already ended; extend it first.")
    locked.status = Auction.Status.LIVE
    locked.save(update_fields=["status", "updated_at"])
    record_audit(
        action=AuditEvent.Action.ADMIN_ACTION,
        actor=actor,
        entity=locked,
        emirate=locked.emirate,
        summary=f"Auction {locked.reference} resumed by admin",
        request=request,
    )
    return locked


@transaction.atomic
def extend_auction(auction: Auction, *, hours: int, actor, request=None) -> Auction:
    """Push the end time out; also revives a paused auction inside its window."""
    _ensure_staff(actor)
    if hours <= 0 or hours > 24 * 14:
        raise ValidationError({"hours": "Extend between 1 hour and 14 days."})
    locked = Auction.objects.select_for_update().get(pk=auction.pk)
    if locked.status not in {Auction.Status.LIVE, Auction.Status.PAUSED}:
        raise StateTransitionError("Only a live or paused auction can be extended.")
    now = timezone.now()
    new_end = max(locked.end_at, now) + timedelta(hours=hours)
    locked.end_at = new_end
    if locked.status == Auction.Status.PAUSED:
        locked.status = Auction.Status.LIVE
    locked.save(update_fields=["end_at", "status", "updated_at"])
    _record_event(
        locked,
        AuctionEvent.Type.EXTENDED,
        actor=actor,
        summary=f"End time extended to {new_end:%d %b %Y %H:%M} by admin",
    )
    record_audit(
        action=AuditEvent.Action.ADMIN_ACTION,
        actor=actor,
        entity=locked,
        emirate=locked.emirate,
        summary=f"Auction {locked.reference} end time extended to {new_end:%Y-%m-%d %H:%M}",
        request=request,
    )
    return locked


@transaction.atomic
def void_bid(auction: Auction, bid: Bid, *, reviewer, request=None) -> Bid:
    """
    Void a fraudulent bid atomically.

    Ranking, top-bid displays and the bid-placement floor all exclude voided
    bids, so the highest bid recalculates automatically from the remaining rows.
    Voiding the bid behind the rewarded/locked selection is refused: that
    decision should go through the selection fallback instead.
    """
    _ensure_staff(reviewer)
    locked = Auction.objects.select_for_update().get(pk=auction.pk)
    bid = locked.bids.select_for_update().get(pk=bid.pk)
    if bid.voided_at is not None:
        raise StateTransitionError("This bid is already voided.")
    if locked.selected_bid_id == bid.pk:
        raise StateTransitionError(
            "This bid holds the current selection. Use the selection fallback to invalidate it."
        )
    bid.voided_at = timezone.now()
    bid.voided_by = reviewer
    bid.save(update_fields=["voided_at", "voided_by"])
    _record_event(
        locked,
        AuctionEvent.Type.BID_VOIDED,
        actor=reviewer,
        summary=f"Bid #{bid.pk} of AED {bid.amount} voided",
        metadata={"bid_id": bid.pk, "bidder_id": bid.bidder_id, "amount": str(bid.amount)},
    )
    record_audit(
        action=AuditEvent.Action.ADMIN_ACTION,
        actor=reviewer,
        entity=locked,
        emirate=locked.emirate,
        summary=f"Bid #{bid.pk} voided on auction {locked.reference}",
        metadata={"bid_id": bid.pk},
        request=request,
    )
    return bid


# ---------------------------------------------------------------------------
# Images
# ---------------------------------------------------------------------------


@transaction.atomic
def add_auction_image(auction: Auction, uploaded_file, *, actor, caption: str = "") -> AuctionImage:
    """Validate, sanitise and store one auction image using ``core.images``."""
    _ensure_owner(auction, actor)
    locked = Auction.objects.select_for_update().get(pk=auction.pk)
    if locked.moderation_status == Auction.ModerationStatus.APPROVED and locked.bids.exists():
        raise StateTransitionError("Photos cannot be changed once bidding has started.")

    existing_count = locked.images.count()
    if existing_count >= settings.MAX_IMAGES_PER_LISTING:
        raise ValidationError(
            f"An auction can have at most {settings.MAX_IMAGES_PER_LISTING} images."
        )

    validate_image_upload(uploaded_file)

    uploaded_file.seek(0)
    payload = uploaded_file.read()
    data, extension = sanitise_image(payload)

    image = AuctionImage(
        auction=locked,
        caption=caption[:140],
        is_cover=(existing_count == 0),
        display_order=existing_count,
    )
    image.image.save(f"{uuid.uuid4().hex}.{extension}", ContentFile(data), save=False)

    with Image.open(BytesIO(data)) as probe:
        image.width, image.height = probe.size
    image.byte_size = len(data)

    try:
        # A savepoint keeps the outer transaction usable if a concurrent upload won
        # the single-cover constraint first.
        with transaction.atomic():
            image.save()
    except IntegrityError:
        image.is_cover = False
        image.save()

    return image


# ---------------------------------------------------------------------------
# Bidding
# ---------------------------------------------------------------------------


def _normalise_client_id(value):
    if value in (None, ""):
        return None
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError) as exc:
        raise ValidationError({"client_bid_id": "Invalid bid identifier."}) from exc


@transaction.atomic
def cancel_auction(auction: Auction, *, actor, reason="", request=None) -> Auction:
    locked = Auction.objects.select_for_update().select_related("emirate").get(pk=auction.pk)
    is_seller = actor is not None and actor.pk == locked.seller_id
    is_staff = actor is not None and getattr(actor, "is_staff", False)
    if not (is_seller or is_staff):
        raise PermissionDeniedError("Only the seller or operations can cancel this auction.")
    if locked.status not in {Auction.Status.SCHEDULED, Auction.Status.LIVE, Auction.Status.PAUSED}:
        raise StateTransitionError("This auction can no longer be cancelled.")
    if is_seller and locked.bids.exists():
        raise StateTransitionError("Bids already exist; ask operations to resolve this auction.")

    locked.status = Auction.Status.CANCELLED
    locked.outcome_note = (reason or "Cancelled")[:255]
    locked.save(update_fields=["status", "outcome_note", "updated_at"])
    _record_event(
        locked,
        AuctionEvent.Type.CANCELLED,
        actor=actor,
        summary=reason or "Auction cancelled",
    )
    record_audit(
        action=AuditEvent.Action.AUCTION_CANCELLED,
        actor=actor,
        entity=locked,
        emirate=locked.emirate,
        summary=f"Auction {locked.reference} cancelled",
        request=request,
    )
    return locked


@transaction.atomic
def place_bid(
    auction: Auction,
    *,
    bidder,
    amount,
    client_bid_id=None,
    request=None,
) -> tuple[Bid, bool]:
    """
    Place a bid. Returns ``(bid, created)``; retries are idempotent per client id.
    """
    if bidder is None or not getattr(bidder, "is_authenticated", False):
        raise PermissionDeniedError("Please sign in to bid.")
    if not getattr(bidder, "is_mobile_verified", False):
        raise PermissionDeniedError("Verify your mobile number before bidding.")

    lock = Auction.objects.select_for_update().select_related("emirate").get(pk=auction.pk)
    if not lock.is_public:
        raise StateTransitionError("This auction is not available.")
    if lock.seller_id == bidder.pk:
        raise PermissionDeniedError("You cannot bid on your own auction.")

    client_id = _normalise_client_id(client_bid_id)
    if client_id is not None:
        existing = lock.bids.filter(client_bid_id=client_id).first()
        if existing is not None:
            return existing, False

    now = timezone.now()
    if lock.status != Auction.Status.LIVE:
        raise StateTransitionError("This auction is not accepting bids.")
    if now < lock.start_at:
        raise StateTransitionError("Bidding has not started yet.")
    if now >= lock.end_at:
        raise StateTransitionError("This auction has closed.")

    limit = settings.AUCTION_MAX_BIDS_PER_USER
    if lock.bids.filter(bidder=bidder).count() >= limit:
        raise StateTransitionError("You have reached the bid limit for this auction.")

    amount = _money(amount)
    highest = (
        lock.bids.filter(voided_at__isnull=True).order_by("-amount", "created_at", "id").first()
    )
    increment = Decimal(settings.AUCTION_MIN_BID_INCREMENT)
    starting_price = lock.starting_price
    minimum_bid = (
        max(highest.amount + increment, starting_price)
        if highest is not None
        else starting_price
    )
    if amount < minimum_bid:
        raise ValidationError({"amount": f"Bid at least AED {minimum_bid}."})

    bid = Bid.objects.create(
        auction=lock,
        bidder=bidder,
        amount=amount,
        client_bid_id=client_id,
    )
    _record_event(
        lock,
        AuctionEvent.Type.BID_PLACED,
        actor=bidder,
        summary=f"Bid of AED {amount}",
        metadata={"bid_id": bid.pk, "amount": str(amount)},
    )

    if highest is not None and highest.bidder_id != bidder.pk:
        _notify_user(
            user=highest.bidder,
            kind=Notification.Kind.AUCTION_OUTBID,
            subject=f"You have been outbid — {lock.reference}",
            body=(
                f"A new bid of AED {amount} was placed on {lock.title}. "
                f"Place a higher bid before {lock.end_at:%d %b %Y %H:%M}."
            ),
            auction=lock,
            key=f"auction:{lock.pk}:bid:{bid.pk}:outbid:{highest.bidder_id}",
        )
    return bid, True


@transaction.atomic
def lock_bid(auction: Auction, *, seller, bid_id: int, request=None) -> AuctionSelection:
    """
    Seller action: freeze bidding and ask the highest eligible bidder to confirm.

    Only the current highest eligible bid can be locked, and only before ``end_at``.
    """
    locked = Auction.objects.select_for_update().select_related("emirate").get(pk=auction.pk)
    _ensure_owner(locked, seller)
    if not locked.is_public:
        raise StateTransitionError("This auction is not available for bidding.")
    if locked.status != Auction.Status.LIVE:
        raise StateTransitionError("Only a live auction can lock a bid.")
    if timezone.now() >= locked.end_at:
        raise StateTransitionError("The auction has ended; bids can no longer be locked.")

    highest = highest_eligible_bid(locked)
    if highest is None:
        raise StateTransitionError(
            "There are no eligible bids at or above the starting price."
        )
    if int(bid_id) != highest.pk:
        raise StateTransitionError("You can only lock the current highest eligible bid.")

    now = timezone.now()
    deadline = now + timedelta(hours=settings.AUCTION_CONFIRMATION_WINDOW_HOURS)
    selection = AuctionSelection.objects.create(
        auction=locked,
        bidder=highest.bidder,
        bid=highest,
        sequence=(
            locked.selections.order_by("-sequence").values_list("sequence", flat=True).first()
            or 0
        )
        + 1,
        confirmation_deadline=deadline,
    )
    locked.status = Auction.Status.LOCKED
    locked.selected_bid = highest
    locked.selected_bidder = highest.bidder
    locked.confirmation_deadline = deadline
    locked.save(
        update_fields=[
            "status",
            "selected_bid",
            "selected_bidder",
            "confirmation_deadline",
            "updated_at",
        ]
    )
    _record_event(
        locked,
        AuctionEvent.Type.BID_LOCKED,
        actor=seller,
        summary=f"Bid of AED {highest.amount} locked for confirmation",
        metadata={"bid_id": highest.pk, "selection_id": selection.pk},
    )
    record_audit(
        action=AuditEvent.Action.AUCTION_BID_LOCKED,
        actor=seller,
        entity=locked,
        emirate=locked.emirate,
        summary=f"Bid locked on auction {locked.reference}",
        metadata={"bid_id": highest.pk, "bidder_id": highest.bidder_id},
        request=request,
    )
    _send_selection_invite(selection)
    return selection


# ---------------------------------------------------------------------------
# Confirmation, decline and fallback
# ---------------------------------------------------------------------------


def _award(
    auction: Auction,
    selection: AuctionSelection,
    *,
    actor,
    request=None,
) -> "Deal":  # noqa: F821 - avoids importing Deal at module import time
    # Idempotency: the unique awarded_deal relationship is the final backstop.
    if auction.awarded_deal_id:
        if (
            auction.selected_bid_id != selection.bid_id
            or auction.selected_bidder_id != selection.bidder_id
        ):
            raise StateTransitionError(
                "This auction has already been awarded to another bidder."
            )
        if selection.status != AuctionSelection.Status.CONFIRMED:
            selection.status = AuctionSelection.Status.CONFIRMED
            selection.responded_at = selection.responded_at or timezone.now()
            selection.save(update_fields=["status", "responded_at", "updated_at"])
        return auction.awarded_deal

    if auction.status != Auction.Status.LOCKED:
        raise StateTransitionError("This auction is no longer awaiting confirmation.")
    if auction.selected_bid_id != selection.bid_id:
        raise StateTransitionError("This bid is no longer the selected bid.")
    if not auction.is_public:
        raise StateTransitionError("This auction is no longer available.")

    deal = create_awarded_deal(
        auction=auction,
        buyer=selection.bidder,
        amount=selection.bid.amount,
        source="auction",
        request=request,
    )

    now = timezone.now()
    selection.status = AuctionSelection.Status.CONFIRMED
    selection.responded_at = now
    selection.save(update_fields=["status", "responded_at", "updated_at"])

    auction.status = Auction.Status.AWARDED
    auction.awarded_deal = deal
    auction.awarded_at = now
    auction.confirmation_deadline = None
    auction.outcome_note = f"Awarded to the highest confirmed bidder at AED {selection.bid.amount}"
    auction.save(
        update_fields=[
            "status",
            "awarded_deal",
            "awarded_at",
            "confirmation_deadline",
            "outcome_note",
            "updated_at",
        ]
    )
    _record_event(
        auction,
        AuctionEvent.Type.AWARDED,
        actor=actor,
        summary=f"Awarded to bidder {selection.bidder_id} at AED {selection.bid.amount}",
        metadata={"deal_id": deal.pk, "bid_id": selection.bid_id},
    )
    record_audit(
        action=AuditEvent.Action.AUCTION_AWARDED,
        actor=actor,
        entity=auction,
        emirate=auction.emirate,
        summary=f"Auction {auction.reference} awarded",
        metadata={"deal_id": deal.pk, "bidder_id": selection.bidder_id},
        request=request,
    )
    _notify_user(
        user=auction.seller,
        kind=Notification.Kind.AUCTION_OUTCOME,
        subject=f"Auction awarded — {auction.reference}",
        body=(
            f"{auction.reference} was awarded at AED {selection.bid.amount}. "
            f"Deal {deal.reference} is now accepted; coordinate handover directly."
        ),
        auction=auction,
        key=f"auction:{auction.pk}:awarded:seller",
    )
    return deal


def confirm_selection(
    selection: AuctionSelection, *, actor, request=None
) -> "Deal":  # noqa: F821
    expired = False
    with transaction.atomic():
        locked_selection = (
            AuctionSelection.objects.select_for_update()
            .select_related("auction", "bid")
            .get(pk=selection.pk)
        )
        auction = Auction.objects.select_for_update().select_related("emirate").get(
            pk=locked_selection.auction_id
        )
        if actor is None or actor.pk != locked_selection.bidder_id:
            raise PermissionDeniedError("This confirmation link is not yours.")

        if locked_selection.status == AuctionSelection.Status.CONFIRMED and auction.awarded_deal_id:
            return auction.awarded_deal
        if locked_selection.status != AuctionSelection.Status.PENDING:
            raise StateTransitionError("This selection has already been answered.")
        if (
            auction.status != Auction.Status.LOCKED
            or auction.selected_bid_id != locked_selection.bid_id
        ):
            raise StateTransitionError("This selection is no longer active.")

        now = timezone.now()
        if now > locked_selection.confirmation_deadline:
            locked_selection.status = AuctionSelection.Status.TIMED_OUT
            locked_selection.responded_at = now
            locked_selection.save(update_fields=["status", "responded_at", "updated_at"])
            _record_event(
                auction,
                AuctionEvent.Type.SELECTION_TIMED_OUT,
                actor=None,
                summary="Confirmation deadline passed before the bidder responded",
                metadata={"selection_id": locked_selection.pk},
            )
            _offer_next(auction, actor=None)
            expired = True
        else:
            return _award(auction, locked_selection, actor=actor, request=request)

    # Committed the timeout + fallback invite before telling the bidder it was late.
    if expired:
        raise StateTransitionError("The confirmation deadline has passed.")
    raise StateTransitionError("This selection could not be confirmed.")


@transaction.atomic
def decline_selection(
    selection: AuctionSelection, *, actor, reason="", request=None
) -> Auction:
    locked_selection = (
        AuctionSelection.objects.select_for_update()
        .select_related("auction", "bid")
        .get(pk=selection.pk)
    )
    auction = Auction.objects.select_for_update().select_related("emirate").get(
        pk=locked_selection.auction_id
    )
    if actor is None or actor.pk != locked_selection.bidder_id:
        raise PermissionDeniedError("This selection is not yours.")
    if locked_selection.status != AuctionSelection.Status.PENDING:
        raise StateTransitionError("This selection has already been answered.")
    if (
        auction.status != Auction.Status.LOCKED
        or auction.selected_bid_id != locked_selection.bid_id
    ):
        raise StateTransitionError("This selection is no longer active.")

    now = timezone.now()
    locked_selection.status = AuctionSelection.Status.DECLINED
    locked_selection.responded_at = now
    locked_selection.save(update_fields=["status", "responded_at", "updated_at"])
    _record_event(
        auction,
        AuctionEvent.Type.SELECTION_DECLINED,
        actor=actor,
        summary=(reason or "Selected bidder declined")[:255],
        metadata={"selection_id": locked_selection.pk},
    )
    _offer_next(auction, actor=actor)
    return auction


def _offer_next(auction: Auction, *, actor=None) -> AuctionSelection | None:
    """
    Offer the auction to the next eligible bidder, or mark it unsold.

    Caller holds the auction row lock. Bidders who were already offered the auction are
    excluded, so a decline or timeout can never loop back to the same person.
    """
    if auction.status == Auction.Status.AWARDED:
        return None
    if auction.status not in {Auction.Status.LOCKED, Auction.Status.LIVE}:
        return None

    offered = set(auction.selections.values_list("bidder_id", flat=True))
    remaining = [bid for bid in eligible_bids(auction) if bid.bidder_id not in offered]

    if not remaining:
        auction.status = Auction.Status.UNSOLD
        auction.confirmation_deadline = None
        auction.outcome_note = "No eligible bidder confirmed the auction."
        auction.save(
            update_fields=["status", "confirmation_deadline", "outcome_note", "updated_at"]
        )
        _record_event(
            auction,
            AuctionEvent.Type.UNSOLD,
            actor=actor,
            summary="No eligible bidders remained",
        )
        record_audit(
            action=AuditEvent.Action.AUCTION_UNSOLD,
            actor=actor,
            entity=auction,
            emirate=auction.emirate,
            summary=f"Auction {auction.reference} closed unsold",
        )
        _notify_user(
            user=auction.seller,
            kind=Notification.Kind.AUCTION_OUTCOME,
            subject=f"Auction unsold — {auction.reference}",
            body=(
                f"{auction.reference} closed without a confirmed bidder. "
                "You can create a new auction for the item."
            ),
            auction=auction,
            key=f"auction:{auction.pk}:unsold:seller",
        )
        return None

    next_bid = remaining[0]
    now = timezone.now()
    deadline = now + timedelta(hours=settings.AUCTION_CONFIRMATION_WINDOW_HOURS)
    sequence = (
        auction.selections.order_by("-sequence").values_list("sequence", flat=True).first()
        or 0
    ) + 1
    selection = AuctionSelection.objects.create(
        auction=auction,
        bidder=next_bid.bidder,
        bid=next_bid,
        sequence=sequence,
        confirmation_deadline=deadline,
    )
    auction.status = Auction.Status.LOCKED
    auction.selected_bid = next_bid
    auction.selected_bidder = next_bid.bidder
    auction.confirmation_deadline = deadline
    auction.save(
        update_fields=[
            "status",
            "selected_bid",
            "selected_bidder",
            "confirmation_deadline",
            "updated_at",
        ]
    )
    _record_event(
        auction,
        AuctionEvent.Type.SELECTION_OFFERED,
        actor=actor,
        summary=f"Fallback invitation to bidder {next_bid.bidder_id} at AED {next_bid.amount}",
        metadata={"selection_id": selection.pk, "sequence": sequence},
    )
    _send_selection_invite(selection)
    return selection


@transaction.atomic
def expire_selection(selection_id: int, *, now=None) -> AuctionSelection | None:
    """
    Mark an overdue confirmation as timed out and move to the next bidder.

    Returns the (possibly new) pending selection, or ``None`` when the auction ended
    unsold. Safe to call repeatedly from the scheduler.
    """
    now = now or timezone.now()
    selection = AuctionSelection.objects.select_for_update().filter(pk=selection_id).first()
    if selection is None or selection.status != AuctionSelection.Status.PENDING:
        return None
    if selection.confirmation_deadline > now:
        return selection

    auction = Auction.objects.select_for_update().select_related("emirate").get(
        pk=selection.auction_id
    )
    if auction.status != Auction.Status.LOCKED:
        return None
    if auction.selected_bid_id != selection.bid_id:
        return None

    selection.status = AuctionSelection.Status.TIMED_OUT
    selection.responded_at = now
    selection.save(update_fields=["status", "responded_at", "updated_at"])
    _record_event(
        auction,
        AuctionEvent.Type.SELECTION_TIMED_OUT,
        actor=None,
        summary="Confirmation deadline expired",
        metadata={"selection_id": selection.pk},
    )
    return _offer_next(auction, actor=None)


# ---------------------------------------------------------------------------
# Scheduled processing (management command)
# ---------------------------------------------------------------------------


@transaction.atomic
def open_scheduled_auction(auction_id: int, *, now=None) -> Auction | None:
    """Open an approved scheduled auction. Unapproved items are never opened."""
    now = now or timezone.now()
    auction = (
        Auction.objects.select_for_update()
        .select_related("emirate")
        .filter(
            pk=auction_id,
            status=Auction.Status.SCHEDULED,
            moderation_status=Auction.ModerationStatus.APPROVED,
        )
        .first()
    )
    if auction is None:
        return None
    if auction.end_at <= now:
        auction.status = Auction.Status.UNSOLD
        auction.outcome_note = "Auction ended before it was opened."
        auction.save(update_fields=["status", "outcome_note", "updated_at"])
        _record_event(auction, AuctionEvent.Type.UNSOLD, summary="Auction ended before opening")
        return auction
    if auction.start_at > now:
        return auction
    auction.status = Auction.Status.LIVE
    auction.save(update_fields=["status", "updated_at"])
    _record_event(auction, AuctionEvent.Type.STARTED, summary="Auction opened for bids")
    return auction


@transaction.atomic
def close_expired_auction(auction_id: int, *, now=None) -> Auction | None:
    """
    Close a live auction whose scheduled end passed without a seller lock.

    The specification deliberately has no automatic winner: if the seller did not lock
    a bid before the end, the auction closes unsold.
    """
    now = now or timezone.now()
    auction = (
        Auction.objects.select_for_update()
        .select_related("emirate")
        .filter(
            pk=auction_id,
            status=Auction.Status.LIVE,
            moderation_status=Auction.ModerationStatus.APPROVED,
        )
        .first()
    )
    if auction is None or auction.end_at > now:
        return auction

    auction.status = Auction.Status.UNSOLD
    auction.outcome_note = "The seller did not lock a bid before the auction ended."
    auction.save(update_fields=["status", "outcome_note", "updated_at"])
    _record_event(
        auction,
        AuctionEvent.Type.UNSOLD,
        summary="Auction ended without a locked bid",
    )
    record_audit(
        action=AuditEvent.Action.AUCTION_UNSOLD,
        actor=None,
        entity=auction,
        emirate=auction.emirate,
        summary=f"Auction {auction.reference} closed unsold",
    )
    _notify_user(
        user=auction.seller,
        kind=Notification.Kind.AUCTION_OUTCOME,
        subject=f"Auction ended unsold — {auction.reference}",
        body=(
            f"{auction.reference} ended without a locked bid. You can create a new "
            "auction for the item."
        ),
        auction=auction,
        key=f"auction:{auction.pk}:unsold:seller",
    )
    return auction


def refresh_auction_state(auction: Auction, *, now=None) -> Auction:
    """
    Apply a due open/close transition while rendering a page.

    The deadline worker remains the primary mechanism. This fallback keeps detail and
    bid pages accurate when the worker has not run yet, for example during local
    development, without replacing the worker's scheduled processing.
    """
    now = now or timezone.now()
    if auction.status == Auction.Status.SCHEDULED and auction.start_at <= now:
        return open_scheduled_auction(auction.pk, now=now) or auction
    if auction.status == Auction.Status.LIVE and auction.end_at <= now:
        return close_expired_auction(auction.pk, now=now) or auction
    return auction
