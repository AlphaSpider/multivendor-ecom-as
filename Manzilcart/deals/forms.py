"""Forms for offers and cancellation requests."""

from __future__ import annotations

from decimal import Decimal

from django import forms


class OfferForm(forms.Form):
    amount = forms.DecimalField(
        label="Your offer (AED)",
        max_digits=10,
        decimal_places=2,
        min_value=Decimal("1.00"),
        widget=forms.NumberInput(
            attrs={"class": "input", "min": "1", "step": "1", "inputmode": "decimal"}
        ),
    )
    note = forms.CharField(
        required=False,
        max_length=280,
        label="Note (optional)",
        widget=forms.TextInput(
            attrs={"class": "input", "placeholder": "e.g. Can collect this weekend"}
        ),
    )


class CancellationRequestForm(forms.Form):
    reason = forms.CharField(
        max_length=1000,
        label="Why should this deal be cancelled?",
        widget=forms.Textarea(
            attrs={
                "class": "input",
                "rows": 3,
                "placeholder": "The seller and ManzilCart operations will see this.",
            }
        ),
    )

    def clean_reason(self):
        reason = (self.cleaned_data["reason"] or "").strip()
        if len(reason) < 5:
            raise forms.ValidationError("Please give a little more detail.")
        return reason


class CancellationResponseForm(forms.Form):
    accept = forms.BooleanField(required=False)
    note = forms.CharField(
        required=False,
        max_length=1000,
        widget=forms.Textarea(attrs={"class": "input", "rows": 2}),
    )
