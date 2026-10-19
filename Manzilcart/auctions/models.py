"""
Auctions: seller-controlled bid locking, bidder confirmation and fallback.

The seller never gets an automatic winner. They explicitly lock the highest eligible
bid before the scheduled end. After that the selected bidder confirms or declines
within a configurable window; declining or timing out offers the auction to the next
highest eligible bidder (each bidder at most once). When nobody confirms, the auction
is unsold. Every round is recorded, and the eventual award creates exactly one deal
linked to the auction by a unique foreign key.

Bidding ties are resolved by the earliest bid at the tied amount. The bid list is
ordered ``-amount, created_at, id`` before de-duplicating by bidder, which gives each
bidder's highest bid and the earliest bid at that amount.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q

from core.models import TimeStampedModel


class Auction(TimeStampedModel):
    class Status(models.TextChoices):
        SCHEDULED = "scheduled", "Scheduled"
        LIVE = "live", "Live"
        LOCKED = "locked", "Bid locked — awaiting confirmation"
        AWARDED = "awarded", "Awarded"
        UNSOLD = "unsold", "Unsold"
        CANCELLED = "cancelled", "Cancelled"

    listing = models.ForeignKey(
        "catalog.Listing",
        on_delete=models.PROTECT,
        related_name="auctions",
    )
    seller = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="auctions",
    )
    emirate = models.ForeignKey(
        "locations.Emirate",
        on_delete=models.PROTECT,
        related_name="auctions",
    )
    start_at = models.DateTimeField(db_index=True)
    end_at = models.DateTimeField(db_index=True)
    reserve_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("1.00"))],
        help_text="Optional. Bids below this amount are not eligible to win.",
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.SCHEDULED,
        db_index=True,
    )
    seller_agreed_fallback = models.BooleanField(
        default=False,
        help_text="Seller accepted that unconfirmed bids fall through to the next bidder.",
    )

    selected_bid = models.ForeignKey(
        "auctions.Bid",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    selected_bidder = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="auction_selections",
    )
    confirmation_deadline = models.DateTimeField(null=True, blank=True)

    awarded_deal = models.OneToOneField(
        "deals.Deal",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="auction_award",
        help_text="Set exactly once when a bidder confirms.",
    )
    awarded_at = models.DateTimeField(null=True, blank=True)
    outcome_note = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name = "auction"
        verbose_name_plural = "auctions"
        constraints = [
            models.CheckConstraint(
                condition=Q(end_at__gt=models.F("start_at")),
                name="auction_end_after_start",
            ),
            models.CheckConstraint(
                condition=Q(reserve_price__isnull=True) | Q(reserve_price__gte=Decimal("1.00")),
                name="auction_reserve_price_min",
            ),
            # One non-terminal auction per listing. A failed auction can be re-run.
            models.UniqueConstraint(
                fields=["listing"],
                condition=Q(status__in=["scheduled", "live", "locked"]),
                name="auction_one_active_per_listing",
            ),
            models.CheckConstraint(
                condition=(
                    ~Q(status="locked")
                    | (
                        Q(selected_bid__isnull=False)
                        & Q(selected_bidder__isnull=False)
                        & Q(confirmation_deadline__isnull=False)
                    )
                ),
                name="auction_locked_requires_selection",
            ),
        ]
        indexes = [
            models.Index(fields=["status", "start_at"], name="auction_status_start_idx"),
            models.Index(fields=["status", "end_at"], name="auction_status_end_idx"),
            models.Index(fields=["emirate", "status"], name="auction_emirate_status_idx"),
            models.Index(fields=["seller", "-created_at"], name="auction_seller_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.reference} — {self.listing}"

    @property
    def reference(self) -> str:
        return f"MC-A-{self.pk:06d}" if self.pk else "MC-A-new"

    @property
    def is_open_for_bids(self) -> bool:
        from django.utils import timezone

        now = timezone.now()
        return self.status == self.Status.LIVE and self.start_at <= now < self.end_at

    @property
    def is_terminal(self) -> bool:
        return self.status in {
            self.Status.AWARDED,
            self.Status.UNSOLD,
            self.Status.CANCELLED,
        }


class Bid(models.Model):
    auction = models.ForeignKey(Auction, on_delete=models.CASCADE, related_name="bids")
    bidder = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="bids",
    )
    amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("1.00"))],
    )
    client_bid_id = models.UUIDField(
        null=True,
        blank=True,
        default=uuid.uuid4,
        editable=False,
        help_text="Idempotency key supplied by the client so retries cannot double-bid.",
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-amount", "created_at", "id"]
        verbose_name = "bid"
        verbose_name_plural = "bids"
        constraints = [
            models.CheckConstraint(
                condition=Q(amount__gte=Decimal("1.00")),
                name="bid_amount_min",
            ),
            models.UniqueConstraint(
                fields=["auction", "client_bid_id"],
                condition=Q(client_bid_id__isnull=False),
                name="bid_unique_client_id_per_auction",
            ),
        ]
        indexes = [
            # Selection/fallback query: highest amount first, earliest tie first.
            models.Index(fields=["auction", "-amount", "created_at"], name="bid_auction_rank_idx"),
            models.Index(fields=["auction", "bidder", "-amount"], name="bid_bidder_idx"),
        ]

    def __str__(self) -> str:
        return f"AED {self.amount} on auction {self.auction_id}"


class AuctionSelection(TimeStampedModel):
    """One confirmation round with one bidder."""

    class Status(models.TextChoices):
        PENDING = "pending", "Awaiting bidder confirmation"
        CONFIRMED = "confirmed", "Confirmed"
        DECLINED = "declined", "Declined"
        TIMED_OUT = "timed_out", "Timed out"

    auction = models.ForeignKey(
        Auction, on_delete=models.CASCADE, related_name="selections"
    )
    bidder = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="auction_offers",
    )
    bid = models.ForeignKey(
        Bid, on_delete=models.PROTECT, related_name="selections"
    )
    sequence = models.PositiveSmallIntegerField()
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    confirmation_deadline = models.DateTimeField()
    responded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["auction", "sequence"]
        verbose_name = "auction selection"
        verbose_name_plural = "auction selections"
        constraints = [
            models.UniqueConstraint(
                fields=["auction", "sequence"],
                name="auctionselection_unique_sequence",
            ),
            # Each bidder appears at most once in the fallback chain.
            models.UniqueConstraint(
                fields=["auction", "bidder"],
                name="auctionselection_unique_bidder",
            ),
        ]
        indexes = [
            models.Index(
                fields=["status", "confirmation_deadline"],
                name="auctionselection_due_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.auction_id} / bidder {self.bidder_id} ({self.status})"


class AuctionEvent(models.Model):
    class Type(models.TextChoices):
        CREATED = "created", "Auction created"
        STARTED = "started", "Auction opened for bids"
        BID_PLACED = "bid_placed", "Bid placed"
        BID_LOCKED = "bid_locked", "Bid locked"
        SELECTION_OFFERED = "selection_offered", "Confirmation requested"
        SELECTION_CONFIRMED = "selection_confirmed", "Bidder confirmed"
        SELECTION_DECLINED = "selection_declined", "Bidder declined"
        SELECTION_TIMED_OUT = "selection_timed_out", "Bidder timed out"
        AWARDED = "awarded", "Auction awarded"
        UNSOLD = "unsold", "Auction unsold"
        CANCELLED = "cancelled", "Auction cancelled"

    auction = models.ForeignKey(
        Auction, on_delete=models.CASCADE, related_name="events"
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="auction_events",
    )
    event_type = models.CharField(max_length=32, choices=Type.choices, db_index=True)
    summary = models.CharField(max_length=255, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["created_at", "id"]
        verbose_name = "auction event"
        verbose_name_plural = "auction events"
        indexes = [models.Index(fields=["auction", "-created_at"], name="auctionevent_auction_idx")]

    def __str__(self) -> str:
        return f"{self.get_event_type_display()} — {self.auction_id}"

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValueError("AuctionEvent rows are append-only.")
        return super().save(*args, **kwargs)
