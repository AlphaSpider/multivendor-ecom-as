"""Forms for creating/editing auctions and placing bids."""

from __future__ import annotations

import uuid
from datetime import timedelta
from decimal import Decimal

from django import forms
from django.conf import settings
from django.utils import timezone

from auctions.models import Auction, AuctionApplication
from catalog.forms import MultipleImageField, MultipleImageInput
from catalog.models import Category
from locations.models import Emirate, ServiceArea
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
            f"At least {settings.MIN_IMAGES_PER_LISTING} and up to "
            f"{settings.MAX_IMAGES_PER_LISTING} photos, "
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


class AuctionApplicationForm(forms.ModelForm):
    """Member application to auction an item. Staff create the auction later."""

    images = MultipleImageField(
        required=False,
        help_text=(
            f"At least {settings.MIN_IMAGES_PER_LISTING} and up to "
            f"{settings.MAX_IMAGES_PER_LISTING} photos, "
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
    agree_review = forms.BooleanField(
        label=(
            "I understand ManzilCart staff review this application and decide the "
            "final auction details before anything is listed."
        ),
        error_messages={"required": "Please accept the review process to continue."},
    )
    agree_fallback = forms.BooleanField(
        label=(
            "I understand that if the selected bidder declines or does not respond "
            "in time, the item is offered to the next highest eligible bidder."
        ),
        error_messages={
            "required": "You must accept the fallback rules before applying."
        },
    )

    class Meta:
        model = AuctionApplication
        fields = (
            "title",
            "description",
            "category",
            "condition",
            "service_area",
            "proposed_starting_bid",
            "preferred_start_at",
            "preferred_end_at",
        )
        widgets = {
            "title": forms.TextInput(
                attrs={
                    "class": "input",
                    "maxlength": 140,
                    "placeholder": "e.g. Three-seater linen sofa",
                }
            ),
            "description": forms.Textarea(
                attrs={
                    "class": "input",
                    "rows": 6,
                    "maxlength": 4000,
                    "placeholder": (
                        "Describe the piece: dimensions, age, any marks, and why you "
                        "are selling. Honest descriptions attract serious bids."
                    ),
                }
            ),
            "category": forms.Select(attrs={"class": "input"}),
            "condition": forms.Select(attrs={"class": "input"}),
            "service_area": forms.Select(attrs={"class": "input"}),
            "proposed_starting_bid": forms.NumberInput(
                attrs={
                    "class": "input",
                    "min": "1",
                    "step": "1",
                    "inputmode": "decimal",
                }
            ),
            "preferred_start_at": forms.DateTimeInput(
                attrs={"type": "datetime-local", "class": "input"},
                format="%Y-%m-%dT%H:%M",
            ),
            "preferred_end_at": forms.DateTimeInput(
                attrs={"type": "datetime-local", "class": "input"},
                format="%Y-%m-%dT%H:%M",
            ),
        }
        labels = {
            "proposed_starting_bid": "Proposed starting / minimum bid (AED)",
            "preferred_start_at": "Preferred bidding start (optional)",
            "preferred_end_at": "Preferred bidding end (optional)",
        }
        help_texts = {
            "proposed_starting_bid": (
                "The lowest bid you would accept. Staff may adjust it before listing."
            ),
            "preferred_start_at": (
                "A preference only. Staff confirm the final dates before listing."
            ),
        }

    def __init__(self, *args, emirate=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.emirate = emirate
        self.fields["category"].queryset = Category.objects.filter(
            is_active=True
        ).order_by("name")
        self.fields["service_area"].queryset = (
            service_areas_for_emirate(emirate)
            if emirate is not None
            else ServiceArea.objects.none()
        )
        self.fields["service_area"].empty_label = "Choose an area"
        self.fields["category"].empty_label = "Choose a category"
        for field in ("preferred_start_at", "preferred_end_at"):
            self.fields[field].input_formats = ["%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M"]
        if self.instance.pk and not self.is_bound:
            self.initial["agree_review"] = True
            self.initial["agree_fallback"] = True

    def clean_preferred_end_at(self):
        start = self.cleaned_data.get("preferred_start_at")
        end = self.cleaned_data.get("preferred_end_at")
        if bool(start) != bool(end):
            raise forms.ValidationError(
                "Provide both a preferred start and end time, or neither."
            )
        if start and end and end <= start:
            raise forms.ValidationError("The end time must be after the start time.")
        return end


class StaffAuctionForm(forms.Form):
    """Staff pre-filled auction editor used by the application review workflow."""

    seller = forms.ModelChoiceField(
        queryset=None,
        widget=forms.Select(attrs={"class": "input"}),
        label="Seller",
    )
    title = forms.CharField(
        max_length=140, widget=forms.TextInput(attrs={"class": "input"})
    )
    description = forms.CharField(
        max_length=4000,
        widget=forms.Textarea(attrs={"class": "input", "rows": 6}),
    )
    category = forms.ModelChoiceField(
        queryset=Category.objects.none(),
        widget=forms.Select(attrs={"class": "input"}),
    )
    condition = forms.ChoiceField(
        choices=Auction.Condition.choices,
        widget=forms.Select(attrs={"class": "input"}),
    )
    emirate = forms.ModelChoiceField(
        queryset=Emirate.objects.none(),
        widget=forms.Select(attrs={"class": "input"}),
    )
    service_area = forms.ModelChoiceField(
        queryset=ServiceArea.objects.none(),
        widget=forms.Select(attrs={"class": "input"}),
    )
    start_at = forms.DateTimeField(
        label="Bidding starts",
        input_formats=["%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M"],
        widget=forms.DateTimeInput(
            attrs={"type": "datetime-local", "class": "input"},
            format="%Y-%m-%dT%H:%M",
        ),
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
        label="Starting / minimum bid (AED)",
        max_digits=10,
        decimal_places=2,
        min_value=Decimal("1.00"),
        widget=forms.NumberInput(
            attrs={"class": "input", "min": "1", "step": "1", "inputmode": "decimal"}
        ),
    )
    images = MultipleImageField(
        required=False,
        help_text=(
            f"At least {settings.MIN_IMAGES_PER_LISTING} photos are needed to publish. "
            f"Maximum {settings.MAX_IMAGES_PER_LISTING} per auction."
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
    allow_suspended = forms.BooleanField(
        required=False,
        label="List even though the applicant is suspended (explicit staff override)",
    )

    def __init__(
        self,
        *args,
        fixed_emirate: Emirate | None = None,
        fixed_seller=None,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        from django.contrib.auth import get_user_model

        User = get_user_model()
        self.fields["seller"].queryset = User.objects.filter(is_active=True).order_by(
            "display_name", "phone"
        )
        self.fields["category"].queryset = Category.objects.filter(
            is_active=True
        ).order_by("name")
        self.fields["emirate"].queryset = Emirate.objects.filter(
            is_active=True
        ).order_by("display_order", "name")
        self.fields["service_area"].queryset = (
            service_areas_for_emirate(fixed_emirate)
            if fixed_emirate is not None
            else ServiceArea.objects.filter(is_active=True)
            .select_related("emirate")
            .order_by("emirate__display_order", "name")
        )
        if fixed_seller is not None:
            self.fields["seller"].widget = forms.HiddenInput()
            self.fields["seller"].initial = fixed_seller.pk
            self.initial["seller"] = fixed_seller.pk
        if fixed_emirate is not None:
            self.fields["emirate"].widget = forms.HiddenInput()
            self.fields["emirate"].initial = fixed_emirate.pk
            self.initial["emirate"] = fixed_emirate.pk
            self.fixed_emirate = fixed_emirate
        else:
            self.fixed_emirate = None

    def clean_end_at(self):
        start = self.cleaned_data.get("start_at")
        end = self.cleaned_data.get("end_at")
        if start and end and end <= start:
            raise forms.ValidationError("The end time must be after the start time.")
        return end

    def clean(self):
        cleaned = super().clean()
        emirate = cleaned.get("emirate")
        area = cleaned.get("service_area")
        if emirate and area and area.emirate_id != emirate.pk:
            self.add_error("service_area", "Choose an area inside the selected emirate.")
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
