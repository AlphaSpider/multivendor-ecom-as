"""
Account forms.

Forms validate *shape* and resolve untrusted references (area slug -> model), while
business rules live in ``accounts.services``. Neither layer trusts the browser: the
emirate is resolved from the URL/session, never from a hidden field.
"""

from __future__ import annotations

from django import forms
from django.conf import settings

from core.exceptions import DomainError
from core.phone import normalise_phone
from locations.models import ServiceArea
from locations.selectors import service_areas_for_emirate
from locations.validation import validate_emirate_service_area

_TEXT_INPUT = {"class": "input", "autocomplete": "off"}


class PhoneNumberForm(forms.Form):
    """Step 1: collect the mobile number."""

    phone = forms.CharField(
        label="Mobile number",
        max_length=24,
        widget=forms.TextInput(
            attrs={
                "class": "input",
                "inputmode": "tel",
                "autocomplete": "tel",
                "placeholder": "050 123 4567",
                "autofocus": "autofocus",
            }
        ),
        help_text="We will send a verification code on WhatsApp.",
    )

    def clean_phone(self) -> str:
        raw = self.cleaned_data["phone"]
        try:
            return normalise_phone(raw).e164
        except DomainError as exc:
            # Localised to the field, so the template can show it next to the input.
            raise forms.ValidationError(exc.message) from exc


class VerificationCodeForm(forms.Form):
    """Step 2: verify the code sent to the number."""

    code = forms.CharField(
        label="Verification code",
        max_length=settings.OTP_LENGTH,
        min_length=settings.OTP_LENGTH,
        widget=forms.TextInput(
            attrs={
                "class": "input code-input",
                "inputmode": "numeric",
                "autocomplete": "one-time-code",
                "pattern": "[0-9]*",
                "maxlength": settings.OTP_LENGTH,
                "autofocus": "autofocus",
            }
        ),
    )

    def clean_code(self) -> str:
        code = (self.cleaned_data["code"] or "").strip()
        if not code.isdigit():
            raise forms.ValidationError("Enter the digits from the message.")
        return code


class RegistrationDetailsForm(forms.Form):
    """Step 3: name, optional email and the supported area of residence."""

    display_name = forms.CharField(
        label="Your name",
        max_length=64,
        widget=forms.TextInput(dict(_TEXT_INPUT, autocomplete="name")),
        help_text="Shown to other members on your listings and in chat.",
    )
    email = forms.EmailField(
        label="Email (optional)",
        required=False,
        max_length=254,
        widget=forms.EmailInput(dict(_TEXT_INPUT, autocomplete="email")),
        help_text="Used for auction and deal notifications only.",
    )
    service_area = forms.ModelChoiceField(
        label="Your area",
        queryset=ServiceArea.objects.none(),
        empty_label="Select your area",
        widget=forms.Select(attrs={"class": "input"}),
    )

    def __init__(self, *args, emirate=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.emirate = emirate
        if emirate is not None:
            # Only areas of the resolved emirate are selectable, server side.
            self.fields["service_area"].queryset = service_areas_for_emirate(emirate)

    def clean(self):
        cleaned = super().clean()
        service_area = cleaned.get("service_area")
        if self.emirate is None:
            raise forms.ValidationError(
                "Your marketplace could not be determined. Please start again."
            )
        if service_area is not None:
            try:
                emirate, service_area = validate_emirate_service_area(
                    self.emirate, service_area
                )
            except DomainError as exc:
                self.add_error("service_area", exc.message)
            else:
                cleaned["emirate"] = emirate
                cleaned["service_area"] = service_area
        return cleaned


class ProfileForm(RegistrationDetailsForm):
    """Profile editing re-uses the same server-side location validation."""

    def __init__(self, *args, instance=None, **kwargs):
        initial = kwargs.setdefault("initial", {})
        if instance is not None:
            initial.setdefault("display_name", instance.display_name)
            initial.setdefault("email", instance.email)
            initial.setdefault("service_area", instance.service_area_id)
        super().__init__(*args, **kwargs)


class EmirateChoiceForm(forms.Form):
    """Used by the marketplace switcher on non-emirate pages."""

    emirate = forms.SlugField(widget=forms.HiddenInput())
