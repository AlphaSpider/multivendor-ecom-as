"""Listing submission and search forms."""

from __future__ import annotations

from decimal import Decimal

from django import forms
from django.conf import settings

from catalog.models import Category, Listing
from locations.selectors import service_areas_for_emirate

_CONDITION_CHOICES = [("", "Any condition")] + list(Listing.Condition.choices)
_SORT_CHOICES = [
    ("newest", "Newest first"),
    ("price_low", "Price: low to high"),
    ("price_high", "Price: high to low"),
    ("oldest", "Oldest first"),
]


class MultipleImageInput(forms.ClearableFileInput):
    """A file input that allows selecting several images at once."""

    allow_multiple_selected = True


class MultipleImageField(forms.FileField):
    """Accepts a list of uploaded files and returns them individually."""

    widget = MultipleImageInput

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("widget", MultipleImageInput(attrs={"multiple": True}))
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        single_clean = super().clean
        if isinstance(data, (list, tuple)):
            return [single_clean(item, initial) for item in data]
        if data in self.empty_values:
            return []
        return [single_clean(data, initial)]


class ListingForm(forms.ModelForm):
    """
    Create/edit form.

    The emirate is intentionally absent: it is resolved from the marketplace URL, so a
    crafted POST cannot move a listing into another emirate.
    """

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

    class Meta:
        model = Listing
        fields = ("title", "category", "condition", "asking_price", "description", "service_area")
        widgets = {
            "title": forms.TextInput(
                attrs={"class": "input", "placeholder": "e.g. Three-seater linen sofa", "maxlength": 140}
            ),
            "category": forms.Select(attrs={"class": "input"}),
            "condition": forms.Select(attrs={"class": "input"}),
            "asking_price": forms.NumberInput(
                attrs={"class": "input", "step": "0.01", "min": "1", "inputmode": "decimal"}
            ),
            "description": forms.Textarea(
                attrs={
                    "class": "input",
                    "rows": 6,
                    "maxlength": 4000,
                    "placeholder": (
                        "Describe the piece: dimensions, age, any marks, and why you are "
                        "selling. Honest descriptions sell faster."
                    ),
                }
            ),
            "service_area": forms.Select(attrs={"class": "input"}),
        }
        labels = {
            "title": "What are you selling?",
            "category": "Category",
            "condition": "Condition",
            "asking_price": "Asking price (AED)",
            "description": "Description",
            "service_area": "Area",
        }

    def __init__(self, *args, emirate=None, instance=None, **kwargs):
        super().__init__(*args, instance=instance, **kwargs)
        self.emirate = emirate
        self.fields["category"].queryset = Category.objects.filter(is_active=True).order_by(
            "display_order", "name"
        )
        if emirate is not None:
            self.fields["service_area"].queryset = service_areas_for_emirate(emirate)
        else:
            self.fields["service_area"].queryset = self.fields["service_area"].queryset.none()
        if instance is not None:
            self.fields["service_area"].initial = instance.service_area_id

        for field in self.fields.values():
            widget = field.widget
            classes = widget.attrs.get("class", "")
            if "input" not in classes:
                widget.attrs["class"] = (classes + " input").strip()

    def clean_asking_price(self) -> Decimal:
        price = self.cleaned_data["asking_price"]
        if price is None or price < Decimal("1.00"):
            raise forms.ValidationError("Enter a price of at least AED 1.00.")
        if price > Decimal("1000000.00"):
            raise forms.ValidationError("Please contact support for items above AED 1,000,000.")
        return price

    def clean_description(self) -> str:
        description = (self.cleaned_data["description"] or "").strip()
        if len(description) < 20:
            raise forms.ValidationError(
                "Please write at least 20 characters so buyers understand the condition."
            )
        return description


class ListingSearchForm(forms.Form):
    """Search/filter form. All values are re-validated before reaching the ORM."""

    q = forms.CharField(required=False, max_length=120, widget=forms.TextInput(attrs={"class": "input", "type": "search"}))
    category = forms.SlugField(required=False, max_length=96, widget=forms.HiddenInput())
    condition = forms.ChoiceField(required=False, choices=_CONDITION_CHOICES)
    min_price = forms.DecimalField(required=False, min_value=Decimal("0"), max_digits=10, decimal_places=2)
    max_price = forms.DecimalField(required=False, min_value=Decimal("0"), max_digits=10, decimal_places=2)
    sort = forms.ChoiceField(required=False, choices=_SORT_CHOICES, initial="newest")

    def clean(self):
        cleaned = super().clean()
        min_price = cleaned.get("min_price")
        max_price = cleaned.get("max_price")
        if min_price is not None and max_price is not None and min_price > max_price:
            # Swap instead of erroring: a human almost certainly just typed them the
            # wrong way round, and an empty result helps nobody.
            cleaned["min_price"], cleaned["max_price"] = max_price, min_price
        return cleaned
