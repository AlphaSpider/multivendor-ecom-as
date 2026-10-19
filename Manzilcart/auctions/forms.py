"""Forms for creating auctions and placing bids."""

from __future__ import annotations

import uuid
from datetime import timedelta
from decimal import Decimal

from django import forms
from django.utils import timezone


class AuctionForm(forms.Form):
    """Seller-facing auction schedule. Business rules live in ``auctions.services``."""

    start_at = forms.DateTimeField(
        label="Bidding starts",
        input_formats=["%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M"],
        widget=forms.DateTimeInput(
            attrs={"type": "datetime-local", "class": "input"},
            format="%Y-%m-%dT%H:%M",
        ),
        help_text="Times are shown in your local timezone.",
    )
    end_at = forms.DateTimeField(
        label="Bidding ends",
        input_formats=["%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M"],
        widget=forms.DateTimeInput(
            attrs={"type": "datetime-local", "class": "input"},
            format="%Y-%m-%dT%H:%M",
        ),
    )
    reserve_price = forms.DecimalField(
        label="Reserve price (AED)",
        required=False,
        max_digits=10,
        decimal_places=2,
        min_value=Decimal("1.00"),
        widget=forms.NumberInput(
            attrs={"class": "input", "min": "1", "step": "1", "inputmode": "decimal"}
        ),
        help_text=(
            "Optional. Bids below this amount still appear but cannot be locked as "
            "the winner. Leave blank for no reserve."
        ),
    )
    agree_fallback = forms.BooleanField(
        label=(
            "I understand that if the selected bidder declines or does not respond "
            "in time, the auction is offered to the next highest eligible bidder."
        ),
        error_messages={
            "required": "You must accept the fallback rules before starting an auction."
        },
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.is_bound and not self.initial.get("start_at"):
            now = timezone.localtime(timezone.now()).replace(second=0, microsecond=0)
            self.initial.setdefault("start_at", now)
            self.initial.setdefault("end_at", now + timedelta(days=3))

    def clean(self):
        cleaned = super().clean()
        start_at = cleaned.get("start_at")
        end_at = cleaned.get("end_at")
        if start_at and end_at and end_at <= start_at:
            self.add_error("end_at", "The end time must be after the start time.")
        return cleaned


class BidForm(forms.Form):
    """A single bid. ``client_bid_id`` makes retries idempotent."""

    amount = forms.DecimalField(
        label="Your bid (AED)",
        max_digits=10,
        decimal_places=2,
        min_value=Decimal("1.00"),
        widget=forms.NumberInput(
            attrs={"class": "input", "min": "1", "step": "1", "inputmode": "decimal"}
        ),
    )
    client_bid_id = forms.UUIDField(
        required=False,
        widget=forms.HiddenInput,
    )

    def __init__(self, *args, **kwargs):
        initial = kwargs.setdefault("initial", {})
        initial.setdefault("client_bid_id", uuid.uuid4())
        super().__init__(*args, **kwargs)
