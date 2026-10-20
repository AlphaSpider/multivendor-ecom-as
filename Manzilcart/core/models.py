"""
Shared database models.

Only genuinely cross-cutting models live here. Domain models belong to their own
app (locations, catalog, deals, auctions, ...).
"""

from __future__ import annotations

from django.conf import settings
from django.db import models


class TimeStampedModel(models.Model):
    """Abstract base adding indexed creation/update timestamps."""

    created_at = models.DateTimeField(auto_now_add=True, db_index=True, editable=False)
    updated_at = models.DateTimeField(auto_now=True, editable=False)

    class Meta:
        abstract = True


class AuditEvent(models.Model):
    """
    Append-only record of security- and business-significant actions.

    Used for administrative accountability (who approved a listing, who finalised
    a cancellation, who retried a notification). Rows are never updated or deleted
    through the application; ``metadata`` must not contain OTP codes, tokens or
    passwords - see ``core.logging_utils`` for redaction helpers.
    """

    class Action(models.TextChoices):
        # Moderation
        LISTING_SUBMITTED = "listing.submitted", "Listing submitted for review"
        LISTING_APPROVED = "listing.approved", "Listing approved"
        LISTING_REJECTED = "listing.rejected", "Listing rejected"
        LISTING_ARCHIVED = "listing.archived", "Listing archived"
        LISTING_SOLD = "listing.sold", "Listing marked sold"
        # Geography / configuration
        EMIRATE_CREATED = "emirate.created", "Emirate created"
        EMIRATE_UPDATED = "emirate.updated", "Emirate updated"
        SERVICE_AREA_CREATED = "service_area.created", "Service area created"
        SERVICE_AREA_UPDATED = "service_area.updated", "Service area updated"
        # Accounts
        ACCOUNT_CREATED = "account.created", "Account created"
        PHONE_VERIFIED = "account.phone_verified", "Mobile number verified"
        LOGIN_SUCCEEDED = "account.login_succeeded", "Login succeeded"
        LOGIN_FAILED = "account.login_failed", "Login failed"
        PROFILE_UPDATED = "account.profile_updated", "Profile updated"
        # Deals
        DEAL_ACCEPTED = "deal.accepted", "Deal accepted"
        DEAL_CANCELLATION_REQUESTED = (
            "deal.cancellation_requested",
            "Cancellation requested",
        )
        DEAL_CANCELLATION_APPROVED = (
            "deal.cancellation_approved",
            "Cancellation approved",
        )
        DEAL_CANCELLATION_REJECTED = (
            "deal.cancellation_rejected",
            "Cancellation rejected",
        )
        DEAL_CANCELLATION_ESCALATED = (
            "deal.cancellation_escalated",
            "Cancellation escalated",
        )
        DEAL_CANCELLED = "deal.cancelled", "Deal cancelled"
        DEAL_COMPLETED = "deal.completed", "Deal completed"
        # Auctions
        AUCTION_CREATED = "auction.created", "Auction created"
        AUCTION_SUBMITTED = "auction.submitted", "Auction submitted for review"
        AUCTION_APPROVED = "auction.approved", "Auction approved"
        AUCTION_REJECTED = "auction.rejected", "Auction rejected"
        AUCTION_BID_LOCKED = "auction.bid_locked", "Bid locked by seller"
        AUCTION_AWARDED = "auction.awarded", "Auction awarded"
        AUCTION_UNSOLD = "auction.unsold", "Auction closed unsold"
        AUCTION_CANCELLED = "auction.cancelled", "Auction cancelled"
        # Notifications / operations
        NOTIFICATION_FAILED = "notification.failed", "Notification failed"
        NOTIFICATION_RETRIED = "notification.retried", "Notification retried"
        OPS_ALERT_SENT = "ops.alert_sent", "Operations alert sent"
        # Generic administration
        ADMIN_ACTION = "admin.action", "Administrative action"

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="audit_events",
        help_text="Staff user or member who performed the action, when known.",
    )
    action = models.CharField(max_length=64, choices=Action.choices, db_index=True)
    entity_type = models.CharField(max_length=64, db_index=True)
    entity_id = models.CharField(max_length=64, blank=True, db_index=True)
    emirate = models.ForeignKey(
        "locations.Emirate",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="audit_events",
    )
    summary = models.CharField(max_length=255, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name = "audit event"
        verbose_name_plural = "audit events"
        indexes = [
            models.Index(
                fields=["entity_type", "entity_id", "-created_at"],
                name="audit_entity_recent_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.action} {self.entity_type}:{self.entity_id}".strip()

    def save(self, *args, **kwargs):
        """Audit rows are append-only: reject updates to an existing row."""
        if self.pk is not None:
            raise ValueError("AuditEvent rows are append-only and cannot be modified.")
        return super().save(*args, **kwargs)
