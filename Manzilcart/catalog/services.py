"""
Listing write operations and the moderation state machine.

Every status change goes through :func:`transition_listing`, which:

* locks the listing row (``SELECT ... FOR UPDATE``),
* validates the transition against a single declared map,
* stamps the derived timestamps (``published_at``, ``submitted_at``, ``reserved_at``),
* writes an audit event.

Views never assign ``listing.status`` directly. That is what keeps "only approved
listings are public" true regardless of which screen triggered the change.
"""

from __future__ import annotations

import logging
import uuid
from io import BytesIO

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.db import IntegrityError, transaction
from django.utils import timezone
from PIL import Image

from catalog.models import Listing, ListingImage
from core.audit import record_audit
from core.exceptions import PermissionDeniedError, StateTransitionError
from core.images import sanitise_image, validate_image_upload
from core.models import AuditEvent
from core.slug import unique_slug
from locations.validation import validate_emirate_service_area

logger = logging.getLogger("manzilcart.catalog")

S = Listing.Status

#: The only permitted status transitions. Anything else raises StateTransitionError.
LISTING_TRANSITIONS: dict[str, set[str]] = {
    S.DRAFT: {S.PENDING, S.ARCHIVED},
    S.PENDING: {S.APPROVED, S.REJECTED, S.DRAFT},
    S.APPROVED: {S.PENDING, S.RESERVED, S.SOLD, S.ARCHIVED},
    S.REJECTED: {S.DRAFT, S.PENDING, S.ARCHIVED},
    S.RESERVED: {S.APPROVED, S.SOLD, S.ARCHIVED},
    S.SOLD: {S.ARCHIVED},
    S.ARCHIVED: {S.DRAFT},
}


def can_transition(current: str, target: str) -> bool:
    return target in LISTING_TRANSITIONS.get(current, set())


def ensure_owner(listing: Listing, actor) -> None:
    """Object-level authorisation for every seller action."""
    if actor is None or not getattr(actor, "is_authenticated", False):
        raise PermissionDeniedError("Please sign in to manage this listing.")
    if listing.seller_id != actor.pk:
        # Same message whether the listing is missing or belongs to someone else.
        raise PermissionDeniedError("You cannot change this listing.")


@transaction.atomic
def create_listing(
    *,
    seller,
    title: str,
    description: str,
    category,
    condition: str,
    asking_price,
    emirate,
    service_area,
    submit_for_review: bool = True,
    request=None,
) -> Listing:
    """
    Create a listing in the seller's own marketplace.

    ``emirate`` comes from the resolved URL/marketplace context, never from a hidden
    form field, and the pair is validated again here before the row is written.
    """
    emirate, service_area = validate_emirate_service_area(emirate, service_area)

    if category is None or not category.is_active:
        raise ValidationError({"category": "Please choose an available category."})
    if category.parent_id is None and category.children.filter(is_active=True).exists():
        raise ValidationError(
            {"category": "Please choose a more specific category."}
        )

    listing = Listing(
        seller=seller,
        title=title.strip(),
        description=description.strip(),
        category=category,
        condition=condition,
        asking_price=asking_price,
        emirate=emirate,
        service_area=service_area,
        status=S.PENDING if submit_for_review else S.DRAFT,
        submitted_at=timezone.now() if submit_for_review else None,
    )
    listing.slug = unique_slug(Listing, title, max_length=160, fallback="listing")
    listing.full_clean(exclude=["slug", "approved_by"])
    listing.save()

    record_audit(
        action=(
            AuditEvent.Action.LISTING_SUBMITTED
            if submit_for_review
            else AuditEvent.Action.ADMIN_ACTION
        ),
        actor=seller,
        entity=listing,
        emirate=emirate,
        summary=(
            "Listing submitted for review"
            if submit_for_review
            else "Listing saved as draft"
        ),
        request=request,
    )
    return listing


@transaction.atomic
def update_listing(
    listing: Listing,
    *,
    actor,
    title: str,
    description: str,
    category,
    condition: str,
    asking_price,
    service_area,
    request=None,
) -> Listing:
    """
    Edit a listing.

    An approved (public) listing is moved back to ``pending`` and unpublished until a
    moderator approves the change - editing must not be a way to slip unmoderated
    content live.
    """
    ensure_owner(listing, actor)

    locked = Listing.objects.select_for_update().get(pk=listing.pk)
    if not locked.is_editable_by_owner:
        raise StateTransitionError("This listing can no longer be edited.")

    emirate, service_area = validate_emirate_service_area(locked.emirate, service_area)

    if category is None or not category.is_active:
        raise ValidationError({"category": "Please choose an available category."})

    locked.title = title.strip()
    locked.description = description.strip()
    locked.category = category
    locked.condition = condition
    locked.asking_price = asking_price
    locked.service_area = service_area

    if locked.status in {S.APPROVED, S.REJECTED}:
        # Re-enter moderation instead of silently updating a public listing.
        locked.status = S.PENDING
        locked.submitted_at = timezone.now()
        locked.moderation_note = ""

    locked.full_clean(exclude=["slug", "approved_by"])
    locked.save()

    record_audit(
        action=AuditEvent.Action.LISTING_SUBMITTED,
        actor=actor,
        entity=locked,
        emirate=emirate,
        summary="Listing edited and submitted for review",
        request=request,
    )
    return locked


@transaction.atomic
def transition_listing(
    listing: Listing,
    target: str,
    *,
    actor=None,
    reason: str = "",
    request=None,
) -> Listing:
    """
    Move a listing to ``target`` if the transition is allowed.

    The row lock serialises competing transitions (for example a moderator approving at
    the same moment the seller archives).
    """
    with transaction.atomic():
        locked = Listing.objects.select_for_update().get(pk=listing.pk)
        previous = locked.status

        if previous == target:
            return locked
        if not can_transition(previous, target):
            raise StateTransitionError(
                f"A listing cannot move from '{locked.get_status_display()}' "
                f"to '{target}'."
            )

        locked.status = target

        if target == S.PENDING:
            locked.submitted_at = timezone.now()
        elif target == S.APPROVED:
            # Fresh publication date: re-approved listings sort as new, not stale.
            locked.published_at = timezone.now()
            locked.approved_at = timezone.now()
            locked.moderation_note = ""
            if actor is not None and getattr(actor, "is_staff", False):
                locked.approved_by = actor
        elif target == S.REJECTED:
            locked.moderation_note = reason.strip()
            if not locked.moderation_note:
                raise ValidationError("A rejection reason is required.")
        elif target == S.RESERVED:
            locked.reserved_at = timezone.now()
        elif target in {S.APPROVED, S.SOLD, S.ARCHIVED}:
            locked.reserved_at = None

        locked.save()

    action_map = {
        S.APPROVED: AuditEvent.Action.LISTING_APPROVED,
        S.REJECTED: AuditEvent.Action.LISTING_REJECTED,
        S.ARCHIVED: AuditEvent.Action.LISTING_ARCHIVED,
        S.SOLD: AuditEvent.Action.LISTING_SOLD,
    }
    record_audit(
        action=action_map.get(target, AuditEvent.Action.ADMIN_ACTION),
        actor=actor,
        entity=locked,
        emirate=locked.emirate,
        summary=f"Listing status: {previous} -> {target}",
        metadata={"reason": reason} if reason else {},
        request=request,
    )
    return locked


def submit_for_review(listing: Listing, *, actor, request=None) -> Listing:
    return transition_listing(
        listing, S.PENDING, actor=actor, request=request
    )


def archive_listing(listing: Listing, *, actor, request=None) -> Listing:
    ensure_owner(listing, actor)
    return transition_listing(listing, S.ARCHIVED, actor=actor, request=request)


def mark_sold(listing: Listing, *, actor, request=None) -> Listing:
    ensure_owner(listing, actor)
    return transition_listing(listing, S.SOLD, actor=actor, request=request)


def mark_reserved(listing: Listing, *, actor=None, request=None) -> Listing:
    """Called by the deal service when an offer is accepted; never by a view directly."""
    return transition_listing(listing, S.RESERVED, actor=actor, request=request)


def reopen_listing(listing: Listing, *, actor=None, request=None) -> Listing:
    """Return a reserved listing to the market after a deal is cancelled."""
    return transition_listing(listing, S.APPROVED, actor=actor, request=request)


# ---------------------------------------------------------------------------
# Images
# ---------------------------------------------------------------------------


@transaction.atomic
def add_listing_image(listing: Listing, uploaded_file, *, actor, caption: str = "") -> ListingImage:
    """
    Validate, sanitise and store one listing image.

    The original upload is never written to storage: it is decoded, stripped of
    metadata, downscaled if necessary and re-encoded first.
    """
    ensure_owner(listing, actor)

    locked = Listing.objects.select_for_update().get(pk=listing.pk)
    existing_count = locked.images.count()
    if existing_count >= settings.MAX_IMAGES_PER_LISTING:
        raise ValidationError(
            f"A listing can have at most {settings.MAX_IMAGES_PER_LISTING} images."
        )

    validate_image_upload(uploaded_file)  # raises ValidationError with a user message

    uploaded_file.seek(0)
    payload = uploaded_file.read()
    data, extension = sanitise_image(payload)

    image = ListingImage(
        listing=locked,
        caption=caption[:140],
        is_cover=(existing_count == 0),
        display_order=existing_count,
    )
    image.image.save(
        f"{uuid.uuid4().hex}.{extension}", ContentFile(data), save=False
    )

    with Image.open(BytesIO(data)) as probe:
        image.width, image.height = probe.size
    image.byte_size = len(data)

    try:
        image.save()
    except IntegrityError:
        # Two concurrent uploads both tried to be the cover: keep this one, drop the
        # cover flag and retry once.
        image.is_cover = False
        image.save()

    return image


@transaction.atomic
def set_cover_image(image: ListingImage, *, actor) -> ListingImage:
    """Choose which image represents the listing."""
    ensure_owner(image.listing, actor)

    ListingImage.objects.filter(listing=image.listing, is_cover=True).exclude(
        pk=image.pk
    ).update(is_cover=False)
    image.is_cover = True
    image.save(update_fields=["is_cover"])
    return image


@transaction.atomic
def delete_listing_image(image: ListingImage, *, actor) -> None:
    ensure_owner(image.listing, actor)
    listing = image.listing
    was_cover = image.is_cover
    image.delete()

    if was_cover:
        replacement = listing.images.order_by("display_order", "id").first()
        if replacement is not None:
            replacement.is_cover = True
            replacement.save(update_fields=["is_cover"])
