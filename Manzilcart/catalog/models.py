"""
Marketplace catalog: furniture categories, listings and listing images.

Publication model
-----------------
Every listing starts as ``draft`` or ``pending`` and becomes publicly visible only
when an administrator approves it. The allowed status transitions are declared once
(``LISTING_TRANSITIONS``) and enforced by ``catalog.services.transition_listing``;
views never write ``status`` directly.

Geographic scoping
------------------
A listing carries both ``emirate`` and ``service_area`` for fast marketplace filtering.
The pair is validated in ``clean()`` and, more importantly, by a composite foreign key
in PostgreSQL (``core.db.geo``), so a mismatched pair cannot be stored at all.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q
from django.urls import reverse

from core.models import TimeStampedModel


class Category(TimeStampedModel):
    """Furniture category (sofas, beds, tables ...). Two levels maximum, deliberately."""

    name = models.CharField(max_length=96)
    slug = models.SlugField(max_length=96, unique=True)
    parent = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="children",
    )
    is_active = models.BooleanField(default=True)
    display_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["display_order", "name"]
        verbose_name = "category"
        verbose_name_plural = "categories"
        indexes = [models.Index(fields=["parent", "is_active"], name="category_parent_active_idx")]

    def __str__(self) -> str:
        return f"{self.parent.name} / {self.name}" if self.parent else self.name

    @property
    def is_leaf(self) -> bool:
        return not self.children.exists()

    def clean(self):
        super().clean()
        if self.parent_id and self.parent_id == self.pk:
            raise ValidationError({"parent": "A category cannot be its own parent."})
        # Keep the tree two levels deep: a child of a child is a grandchild.
        if self.parent_id and self.parent and self.parent.parent_id:
            raise ValidationError({"parent": "Categories support two levels only."})

    def save(self, *args, **kwargs):
        if self.slug:
            self.slug = self.slug.strip().lower()
        return super().save(*args, **kwargs)


def listing_image_upload_to(instance: "ListingImage", filename: str) -> str:
    """
    Store images under an opaque path.

    A random filename prevents enumeration of other members' uploads and avoids
    leaking the original (often personal) filename.
    """
    extension = "jpg"
    if "." in filename:
        extension = filename.rsplit(".", 1)[-1].lower()[:8]
    return f"listings/{instance.listing_id}/{uuid.uuid4().hex}.{extension}"


class ListingQuerySet(models.QuerySet):
    def public(self) -> "ListingQuerySet":
        """Only listings that passed moderation and are still on the market."""
        return self.filter(
            status=Listing.Status.APPROVED, published_at__isnull=False
        )

    def in_emirate(self, emirate) -> "ListingQuerySet":
        return self.filter(emirate=emirate)

    def with_gallery(self) -> "ListingQuerySet":
        return self.prefetch_related("images")

    def for_seller(self, user) -> "ListingQuerySet":
        return self.filter(seller=user)


class Listing(TimeStampedModel):
    class Condition(models.TextChoices):
        LIKE_NEW = "like_new", "Like new"
        GOOD = "good", "Good"
        FAIR = "fair", "Fair"

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        PENDING = "pending", "Pending review"
        APPROVED = "approved", "Approved / published"
        REJECTED = "rejected", "Rejected"
        ARCHIVED = "archived", "Archived"
        # Reserved while an accepted deal exists; reopened if that deal is cancelled.
        RESERVED = "reserved", "Reserved"
        SOLD = "sold", "Sold"

    seller = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="listings",
    )
    title = models.CharField(max_length=140)
    slug = models.SlugField(max_length=160, unique=True)
    description = models.TextField(max_length=4000)
    category = models.ForeignKey(
        Category, on_delete=models.PROTECT, related_name="listings"
    )
    condition = models.CharField(
        max_length=16, choices=Condition.choices, db_index=True
    )
    asking_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("1.00"))],
        help_text="Price in AED. Payment happens directly between buyer and seller.",
    )
    currency = models.CharField(max_length=3, default="AED", editable=False)

    # Marketplace scope. Both fields are required for member listings.
    emirate = models.ForeignKey(
        "locations.Emirate", on_delete=models.PROTECT, related_name="listings"
    )
    service_area = models.ForeignKey(
        "locations.ServiceArea", on_delete=models.PROTECT, related_name="listings"
    )

    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.DRAFT,
        db_index=True,
    )
    moderation_note = models.TextField(
        blank=True,
        help_text="Internal reason for the last moderation decision (shown to the seller).",
    )
    submitted_at = models.DateTimeField(null=True, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="approved_listings",
    )
    published_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        help_text="When the listing became publicly visible.",
    )
    reserved_at = models.DateTimeField(null=True, blank=True)

    objects = ListingQuerySet.as_manager()

    class Meta:
        ordering = ["-published_at", "-created_at"]
        verbose_name = "listing"
        verbose_name_plural = "listings"
        constraints = [
            models.CheckConstraint(
                condition=Q(asking_price__gte=Decimal("1.00")),
                name="listing_asking_price_min",
            ),
            models.CheckConstraint(
                condition=Q(emirate__isnull=False, service_area__isnull=False),
                name="listing_location_required",
            ),
            # A published listing must carry a publication timestamp. The reverse is not
            # required: a listing that was published and later reserved/sold keeps its
            # publication date for history and ordering.
            models.CheckConstraint(
                condition=(~Q(status="approved") | Q(published_at__isnull=False)),
                name="listing_published_at_required_when_approved",
            ),
        ]
        indexes = [
            # The dominant query: browse one emirate, newest first, approved only.
            models.Index(
                fields=["emirate", "status", "-published_at"],
                name="listing_market_feed_idx",
            ),
            models.Index(
                fields=["category", "status", "-published_at"],
                name="listing_category_feed_idx",
            ),
            models.Index(fields=["seller", "-created_at"], name="listing_seller_idx"),
            models.Index(fields=["service_area", "status"], name="listing_area_status_idx"),
        ]

    def __str__(self) -> str:
        return self.title

    # -- URL ----------------------------------------------------------------
    def get_absolute_url(self) -> str:
        return reverse(
            "catalog:listing_detail",
            kwargs={"emirate_slug": self.emirate.slug, "slug": self.slug},
        )

    # -- invariants ---------------------------------------------------------
    def clean(self):
        super().clean()
        if self.asking_price is not None and self.asking_price < Decimal("1.00"):
            raise ValidationError(
                {"asking_price": "The asking price must be at least AED 1.00."}
            )
        if self.service_area_id and self.emirate_id:
            from locations.validation import validate_emirate_service_area

            # require_active=False: an approved listing must survive its area being
            # deactivated. New submissions are checked against active areas in services.
            validate_emirate_service_area(
                self.emirate, self.service_area, require_active=False
            )
        if self.category_id and not self.category.is_active:
            raise ValidationError({"category": "That category is not available."})

    def save(self, *args, **kwargs):
        if self.title:
            self.title = self.title.strip()
        super().save(*args, **kwargs)

    # -- presentation -------------------------------------------------------
    @property
    def cover_image(self):
        """The chosen cover, or the first image by display order."""
        images = list(self.images.all())
        for image in images:
            if image.is_cover:
                return image
        return images[0] if images else None

    @property
    def is_public(self) -> bool:
        return self.status == self.Status.APPROVED and self.published_at is not None

    @property
    def is_editable_by_owner(self) -> bool:
        """Members may edit drafts, rejected listings and live listings (which re-enter review)."""
        return self.status in {
            self.Status.DRAFT,
            self.Status.REJECTED,
            self.Status.APPROVED,
        }


class ListingImage(models.Model):
    """
    A validated, re-encoded image attached to a listing.

    Uploads are sanitised in ``catalog.services.add_listing_image``: the bytes are
    decoded by Pillow, EXIF metadata (including GPS) is stripped, oversized images are
    downscaled and the file is re-encoded before it ever reaches storage.
    """

    listing = models.ForeignKey(
        Listing, on_delete=models.CASCADE, related_name="images"
    )
    image = models.ImageField(upload_to=listing_image_upload_to, max_length=255)
    caption = models.CharField(max_length=140, blank=True)
    is_cover = models.BooleanField(default=False)
    display_order = models.PositiveSmallIntegerField(default=0)
    width = models.PositiveIntegerField(default=0, editable=False)
    height = models.PositiveIntegerField(default=0, editable=False)
    byte_size = models.PositiveIntegerField(default=0, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["display_order", "id"]
        verbose_name = "listing image"
        verbose_name_plural = "listing images"
        constraints = [
            # At most one cover image per listing, enforced by a partial unique index.
            models.UniqueConstraint(
                fields=["listing"],
                condition=Q(is_cover=True),
                name="listingimage_one_cover_per_listing",
            ),
        ]

    def __str__(self) -> str:
        return f"Image for {self.listing_id}"

    @property
    def aspect_ratio(self) -> float:
        if not self.height:
            return 4 / 3
        return round(self.width / self.height, 4)
