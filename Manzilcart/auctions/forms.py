"""Forms for creating/editing auctions and placing bids."""

from __future__ import annotations

import uuid
from datetime import timedelta
from decimal import Decimal

from django import forms
from django.conf import settings
from django.utils import timezone

from auctions.models import Auction
from catalog.forms import MultipleImageField, MultipleImageInput
from catalog.models import Category
from locations.selectors import service_areas_for_emirate


class AuctionForm(forms.Form):
    """Seller-facing standalone auction item. Business rules live in ``auctions.services``."""

    title = forms.CharField(
        max_length=140,
        widget=forms.TextInput(
            attrs={
                "class": "input",
                "placeholder": "e.g. Three-seater linen sofa",
                "maxlength": 140,
            }
        ),
        label="What are you auctioning?",
    )
    description = forms.CharField(
        max_length=4000,
        widget=forms.Textarea(
            attrs={
                "class": "input",
                "rows": 6,
                "maxlength": 4000,
                "placeholder": (
                    "Describe the piece: dimensions, age, any marks, and why you are "
                    "selling. Honest descriptions attract serious bids."
                ),
            }
        ),
        label="Description",
    )
    category = forms.ModelChoiceField(
        queryset=Category.objects.none(),
        widget=forms.Select(attrs={"class": "input"}),
        label="Category",
    )
    condition = forms.ChoiceField(
        choices=Auction.Condition.choices,
        widget=forms.Select(attrs={"class": "input"}),
        label="Condition",
    )
    service_area = forms.ModelChoiceField(
        queryset=None,
        widget=forms.Select(attrs={"class": "input"}),
        label="Area",
    )
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
        label="Minimum amount (AED)",
        required=True,
        max_digits=10,
        decimal_places=2,
        min_value=Decimal("1.00"),
        widget=forms.NumberInput(
            attrs={"class": "input", "min": "1", "step": "1", "inputmode": "decimal"}
        ),
        help_text=(
            "Required. This is the auction's starting price; bids below this "
            "amount are not accepted."
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
    images = MultipleImageField(
        required=False,
        help_text=(
            f"Up to {settings.MAX_IMAGES_PER_LISTING} photos, "
            f"{settings.MAX_IMAGE_UPLOAD_SIZE // (1024 * 1024)} MB each. "
            "JPEG, PNG or WebP."
        ),
        widget=MultipleImageInput(
            attrs={
                "accept": "image/jpeg,image/png,image/webp",
                "multiple": True,
                "data-image-input": "",
                "data-preview-target": "#image-preview",
            }
        ),
    )

    def __init__(self, *args, emirate=None, instance: Auction | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.emirate = emirate
        self.instance = instance
        self.fields["category"].queryset = Category.objects.filter(is_active=True).order_by(
            "name"
        )
        self.fields["service_area"].queryset = (
            service_areas_for_emirate(emirate) if emirate is not None else self.fields["service_area"].queryset.none()
        )
        if instance is not None and not self.is_bound:
            self.initial.update(
                {
                    "title": instance.title,
                    "description": instance.description,
                    "category": instance.category_id,
                    "condition": instance.condition,
                    "service_area": instance.service_area_id,
                    "start_at": timezone.localtime(instance.start_at).replace(
                        second=0, microsecond=0
                    ),
                    "end_at": timezone.localtime(instance.end_at).replace(
                        second=0, microsecond=0
                    ),
                    "reserve_price": instance.reserve_price,
                    "agree_fallback": True,
                }
            )
        elif not self.is_bound and not self.initial.get("start_at"):
            now = timezone.localtime(timezone.now()).replace(second=0, microsecond=0)
            self.initial.setdefault("start_at", now)
            self.initial.setdefault("end_at", now + timedelta(days=3))
            self.initial.setdefault("agree_fallback", True)

    def clean_end_at(self):
        start_at = self.cleaned_data.get("start_at")
        end_at = self.cleaned_data.get("end_at")
        if start_at and end_at and end_at <= start_at:
            raise forms.ValidationError("The end time must be after the start time.")
        return end_at


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
