"""
Listing moderation records.

Kept in its own app so the review queue and its audit history can evolve without the
catalog models growing into a moderation subsystem. The actual moderation UI is Django
Admin (see ``moderation/admin.py``), as the specification prefers a small back office.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models

from catalog.models import Listing


class ListingReview(models.Model):
    """
    One moderation decision on a listing.

    Rows are append-only: a listing that is edited and resubmitted gets a new review,
    so the full decision history survives. ``previous_status`` / ``new_status`` make the
    record meaningful even if the listing's current state later changes.
    """

    class Decision(models.TextChoices):
        APPROVE = "approve", "Approved"
        REJECT = "reject", "Rejected"

    listing = models.ForeignKey(
        Listing, on_delete=models.CASCADE, related_name="reviews"
    )
    reviewer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="listing_reviews",
    )
    decision = models.CharField(max_length=16, choices=Decision.choices)
    reason = models.TextField(
        blank=True,
        help_text="Shown to the seller when a listing is rejected. Required for rejections.",
    )
    previous_status = models.CharField(max_length=16, blank=True)
    new_status = models.CharField(max_length=16, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name = "listing review"
        verbose_name_plural = "listing reviews"
        indexes = [
            models.Index(fields=["listing", "-created_at"], name="listingreview_listing_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.get_decision_display()} - {self.listing_id}"

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValueError("ListingReview rows are append-only.")
        return super().save(*args, **kwargs)


class AuctionReview(models.Model):
    """
    One moderation decision on a standalone auction.

    Kept separate from the auction row so the decision history is append-only: an edited
    auction that is resubmitted gets a new review instead of overwriting the old one.
    """

    class Decision(models.TextChoices):
        APPROVE = "approve", "Approved"
        REJECT = "reject", "Rejected"

    auction = models.ForeignKey(
        "auctions.Auction", on_delete=models.CASCADE, related_name="reviews"
    )
    reviewer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="auction_reviews",
    )
    decision = models.CharField(max_length=16, choices=Decision.choices)
    reason = models.TextField(
        blank=True,
        help_text="Shown to the seller when an auction is rejected. Required for rejections.",
    )
    previous_status = models.CharField(max_length=16, blank=True)
    new_status = models.CharField(max_length=16, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name = "auction review"
        verbose_name_plural = "auction reviews"
        indexes = [
            models.Index(fields=["auction", "-created_at"], name="auctionreview_auction_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.get_decision_display()} - auction {self.auction_id}"

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValueError("AuctionReview rows are append-only.")
        return super().save(*args, **kwargs)
