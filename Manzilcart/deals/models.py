"""
Offers, accepted deals, cancellations and service selections.

Concurrency model
-----------------
* An accepted deal is unique per listing, enforced by a **partial unique index**
  (``deal_one_active_per_listing``). Two buyers can negotiate in parallel, but only one
  can hold an accepted deal.
* Acceptance locks the deal row and then the listing row (always in that order, to
  avoid deadlocks), re-checks the listing state, and only then writes the accepted
  deal and reserves the listing.
* At most one pending offer and one accepted offer may exist per deal; both are partial
  unique constraints, so a retry or a racing request cannot create a second one.

Payment is deliberately absent: the agreed price is recorded for history, and the
buyer pays the seller directly.
"""

from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import F, Q

from core.models import TimeStampedModel


class Deal(TimeStampedModel):
    class Status(models.TextChoices):
        NEGOTIATING = "negotiating", "Negotiating"
        ACCEPTED = "accepted", "Accepted"
        CANCELLATION_REQUESTED = "cancellation_requested", "Cancellation requested"
        CANCELLED = "cancelled", "Cancelled"

    listing = models.ForeignKey(
        "catalog.Listing",
        on_delete=models.PROTECT,
        related_name="deals",
    )
    buyer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="purchases",
    )
    seller = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="sales",
    )
    emirate = models.ForeignKey(
        "locations.Emirate",
        on_delete=models.PROTECT,
        related_name="deals",
    )
    conversation = models.ForeignKey(
        "conversations.Conversation",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="deals",
        help_text="Optional link to the thread where the negotiation happened.",
    )

    status = models.CharField(
        max_length=24,
        choices=Status.choices,
        default=Status.NEGOTIATING,
        db_index=True,
    )
    agreed_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Recorded when an offer is accepted. Payment happens directly.",
    )
    accepted_offer = models.ForeignKey(
        "deals.DealOffer",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    accepted_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name = "deal"
        verbose_name_plural = "deals"
        constraints = [
            models.CheckConstraint(
                condition=~Q(buyer=F("seller")),
                name="deal_participants_distinct",
            ),
            models.CheckConstraint(
                condition=(
                    ~Q(status__in=["accepted", "cancellation_requested"])
                    | Q(agreed_price__isnull=False)
                ),
                name="deal_agreed_price_required_when_accepted",
            ),
            # The core guarantee: one listing cannot be promised to two buyers.
            models.UniqueConstraint(
                fields=["listing"],
                condition=Q(status__in=["accepted", "cancellation_requested"]),
                name="deal_one_active_per_listing",
            ),
            # A buyer keeps one open negotiation per listing; cancelled deals do not
            # block a fresh attempt if the seller puts the item back on the market.
            models.UniqueConstraint(
                fields=["listing", "buyer"],
                condition=Q(status="negotiating"),
                name="deal_one_open_per_buyer_listing",
            ),
        ]
        indexes = [
            models.Index(fields=["buyer", "status", "-created_at"], name="deal_buyer_status_idx"),
            models.Index(fields=["seller", "status", "-created_at"], name="deal_seller_status_idx"),
            models.Index(fields=["listing", "status"], name="deal_listing_status_idx"),
            models.Index(fields=["emirate", "status"], name="deal_emirate_status_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.reference} — {self.listing}"

    @property
    def reference(self) -> str:
        return f"MC-D-{self.pk:06d}" if self.pk else "MC-D-new"

    def is_participant(self, user) -> bool:
        if user is None or not getattr(user, "is_authenticated", False):
            return False
        return user.pk in {self.buyer_id, self.seller_id}

    def other_participant(self, user):
        if not self.is_participant(user):
            return None
        return self.seller if user.pk == self.buyer_id else self.buyer

    @property
    def is_active(self) -> bool:
        return self.status in {self.Status.ACCEPTED, self.Status.CANCELLATION_REQUESTED}


class DealOffer(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        ACCEPTED = "accepted", "Accepted"
        REJECTED = "rejected", "Rejected"
        COUNTERED = "countered", "Countered"
        WITHDRAWN = "withdrawn", "Withdrawn"

    deal = models.ForeignKey(Deal, on_delete=models.CASCADE, related_name="offers")
    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="deal_offers",
    )
    amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("1.00"))],
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    note = models.CharField(max_length=280, blank=True)
    in_response_to = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="responses",
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    responded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["created_at", "id"]
        verbose_name = "offer"
        verbose_name_plural = "offers"
        constraints = [
            models.CheckConstraint(
                condition=Q(amount__gte=Decimal("1.00")),
                name="dealoffer_amount_min",
            ),
            models.UniqueConstraint(
                fields=["deal"],
                condition=Q(status="pending"),
                name="dealoffer_one_pending_per_deal",
            ),
            models.UniqueConstraint(
                fields=["deal"],
                condition=Q(status="accepted"),
                name="dealoffer_one_accepted_per_deal",
            ),
        ]
        indexes = [
            models.Index(fields=["deal", "-created_at"], name="dealoffer_deal_idx"),
            models.Index(fields=["sender", "-created_at"], name="dealoffer_sender_idx"),
        ]

    def __str__(self) -> str:
        return f"AED {self.amount} by {self.sender_id} ({self.status})"


class DealCancellation(TimeStampedModel):
    """One cancellation request and the other party's response."""

    class Status(models.TextChoices):
        PENDING = "pending", "Awaiting response"
        REJECTED = "rejected", "Rejected by the other party"
        ACCEPTED = "accepted", "Accepted by the other party"
        ESCALATED = "escalated", "Escalated to operations"
        FINALIZED = "finalized", "Cancellation finalised by staff"

    deal = models.ForeignKey(
        Deal, on_delete=models.CASCADE, related_name="cancellations"
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="cancellation_requests",
    )
    reason = models.TextField(max_length=1000)
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    responded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="cancellation_responses",
    )
    response_note = models.TextField(max_length=1000, blank=True)
    responded_at = models.DateTimeField(null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name = "cancellation request"
        verbose_name_plural = "cancellation requests"
        constraints = [
            models.UniqueConstraint(
                fields=["deal"],
                condition=Q(status__in=["pending", "escalated"]),
                name="dealcancellation_one_open_per_deal",
            ),
        ]
        indexes = [
            models.Index(fields=["deal", "-created_at"], name="dealcancellation_deal_idx"),
            models.Index(fields=["status", "-created_at"], name="dealcancellation_status_idx"),
        ]

    def __str__(self) -> str:
        return f"Cancellation for {self.deal_id} ({self.status})"


class DealServiceSelection(models.Model):
    """An optional service the buyer selected for an accepted or negotiating deal."""

    deal = models.ForeignKey(
        Deal, on_delete=models.CASCADE, related_name="service_selections"
    )
    service_option = models.ForeignKey(
        "locations.ServiceOption",
        on_delete=models.PROTECT,
        related_name="deal_selections",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["service_option__display_order", "service_option__name"]
        verbose_name = "service selection"
        verbose_name_plural = "service selections"
        constraints = [
            models.UniqueConstraint(
                fields=["deal", "service_option"],
                name="dealserviceselection_unique",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.service_option} for {self.deal_id}"


class DealEvent(models.Model):
    """Append-only history of everything that happened to a deal."""

    class Type(models.TextChoices):
        OFFER_MADE = "offer_made", "Offer made"
        OFFER_COUNTERED = "offer_countered", "Offer countered"
        OFFER_REJECTED = "offer_rejected", "Offer rejected"
        DEAL_ACCEPTED = "deal_accepted", "Deal accepted"
        CANCELLATION_REQUESTED = "cancellation_requested", "Cancellation requested"
        CANCELLATION_REJECTED = "cancellation_rejected", "Cancellation rejected"
        CANCELLATION_ESCALATED = "cancellation_escalated", "Cancellation escalated"
        CANCELLATION_FINALIZED = "cancellation_finalized", "Cancellation finalised"
        LISTING_REOPENED = "listing_reopened", "Listing reopened"
        SERVICES_UPDATED = "services_updated", "Service selections updated"

    deal = models.ForeignKey(Deal, on_delete=models.CASCADE, related_name="events")
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="deal_events",
    )
    event_type = models.CharField(max_length=32, choices=Type.choices, db_index=True)
    summary = models.CharField(max_length=255, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["created_at", "id"]
        verbose_name = "deal event"
        verbose_name_plural = "deal events"
        indexes = [
            models.Index(fields=["deal", "-created_at"], name="dealevent_deal_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.get_event_type_display()} — {self.deal_id}"

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValueError("DealEvent rows are append-only.")
        return super().save(*args, **kwargs)
