"""
Account forms.

Forms validate *shape* and resolve untrusted references (area slug -> model), while
business rules live in ``accounts.services``. Neither layer trusts the browser: the
emirate is resolved from the URL/session, never from a hidden field.
"""

from __future__ import annotations

from django import forms
from django.conf import settings
from django.contrib.auth import password_validation
from django.contrib.auth.forms import AuthenticationForm

from accounts.models import MemberEmail, User
from core.exceptions import DomainError
from core.phone import normalise_phone
from core.ratelimit import client_ip, consume, is_limited, reset
from locations.models import ServiceArea
from locations.selectors import service_areas_for_emirate
from locations.validation import validate_emirate_service_area

_TEXT_INPUT = {"class": "input", "autocomplete": "off"}
GENERIC_LOGIN_ERROR = "Invalid credentials."


def _normalise_email(raw: str) -> str:
    return (raw or "").strip().lower()


def resolve_member_by_identifier(raw: str) -> User | None:
    """Resolve a phone number or email address to one member, if possible."""
    value = (raw or "").strip()
    if not value:
        return None

    if "@" in value:
        email = _normalise_email(value)
        email_row = (
            MemberEmail.objects.select_related("user")
            .filter(email__iexact=email)
            .first()
        )
        if email_row is not None:
            return email_row.user
        # Fallback for legacy rows created before MemberEmail existed.
        matches = User.objects.filter(email__iexact=email)
        if matches.count() == 1:
            return matches.first()
        return None

    try:
        phone = normalise_phone(value).e164
    except DomainError:
        return None
    return User.objects.filter(phone=phone).first()


class LoginIdentifierForm(forms.Form):
    """Default sign-in step: phone or email, followed by a one-time code."""

    identifier = forms.CharField(
        label="Mobile number or email",
        max_length=254,
        widget=forms.TextInput(
            attrs={
                "class": "input",
                "autocomplete": "username",
                "autofocus": "autofocus",
                "placeholder": "050 123 4567 or you@example.com",
            }
        ),
        help_text=(
            "We will send a code to your WhatsApp and, if you added one, your email."
        ),
    )

    def clean_identifier(self) -> str:
        # Validate only the *shape* of the identifier. Whether it belongs to a member
        # is decided in the view, so the response cannot be used as a membership oracle.
        raw = self.cleaned_data["identifier"].strip()
        if not raw:
            raise forms.ValidationError(GENERIC_LOGIN_ERROR)
        try:
            if "@" in raw:
                forms.EmailField().clean(raw)
            else:
                normalise_phone(raw)
        except (forms.ValidationError, DomainError):
            raise forms.ValidationError(GENERIC_LOGIN_ERROR) from None
        return raw


class PasswordLoginForm(AuthenticationForm):
    """Phone/email + password sign-in for members who have set a password."""

    username = forms.CharField(
        label="Mobile number or email",
        max_length=254,
        widget=forms.TextInput(
            attrs={
                "class": "input",
                "autocomplete": "username",
                "autofocus": "autofocus",
                "placeholder": "050 123 4567 or you@example.com",
            }
        ),
    )
    password = forms.CharField(
        label="Password",
        strip=False,
        widget=forms.PasswordInput(
            attrs={"class": "input", "autocomplete": "current-password"}
        ),
    )
    error_messages = {
        **AuthenticationForm.error_messages,
        "invalid_login": GENERIC_LOGIN_ERROR,
        "inactive": GENERIC_LOGIN_ERROR,
    }

    def clean_username(self) -> str:
        raw = self.cleaned_data["username"].strip()
        member = resolve_member_by_identifier(raw)
        if member is not None:
            # AuthenticationForm/ModelBackend authenticate against USERNAME_FIELD
            # (phone), so resolve an email to the member's phone first.
            return member.phone
        # Keep a value that cannot match, letting AuthenticationForm produce its
        # standard invalid-credentials response rather than disclosing membership.
        return _normalise_email(raw) if "@" in raw else raw

    def _throttle_keys(self, identifier: str) -> tuple[str, str]:
        return (
            f"password-login:user:{identifier}",
            f"password-login:ip:{client_ip(self.request)}",
        )

    def clean(self):
        identifier = self.cleaned_data.get("username")
        if identifier:
            keys = self._throttle_keys(identifier)
            limit = settings.PASSWORD_LOGIN_MAX_ATTEMPTS
            window = settings.PASSWORD_LOGIN_WINDOW_SECONDS
            if any(is_limited(key, limit, window) for key in keys):
                raise forms.ValidationError(
                    "Too many sign-in attempts. Please wait a few minutes or use a one-time code.",
                    code="rate_limited",
                )
            for key in keys:
                consume(key, limit, window)
        return super().clean()

    def reset_throttle(self) -> None:
        identifier = self.cleaned_data.get("username")
        if identifier:
            for key in self._throttle_keys(identifier):
                reset(key)


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


class RegistrationForm(forms.Form):
    """Create an account immediately with phone, optional email and password."""

    display_name = forms.CharField(
        label="Your name",
        max_length=64,
        widget=forms.TextInput(dict(_TEXT_INPUT, autocomplete="name")),
        help_text="Shown to other members on your listings and in chat.",
    )
    phone = forms.CharField(
        label="Mobile number",
        max_length=24,
        widget=forms.TextInput(
            attrs={
                "class": "input",
                "inputmode": "tel",
                "autocomplete": "tel",
                "placeholder": "050 123 4567",
            }
        ),
        help_text="Required. You can sign in with this number and a code.",
    )
    email = forms.EmailField(
        label="Email (optional)",
        required=False,
        max_length=254,
        widget=forms.EmailInput(dict(_TEXT_INPUT, autocomplete="email")),
        help_text="Optional. Used for sign-in codes and notifications.",
    )
    service_area = forms.ModelChoiceField(
        label="Your area",
        queryset=ServiceArea.objects.none(),
        empty_label="Select your area",
        widget=forms.Select(attrs={"class": "input"}),
    )
    password1 = forms.CharField(
        label="Password",
        strip=False,
        widget=forms.PasswordInput(
            attrs={"class": "input", "autocomplete": "new-password"}
        ),
        help_text=(
            "At least 8 characters with at least one uppercase letter, one digit "
            "and one special character."
        ),
    )
    password2 = forms.CharField(
        label="Confirm password",
        strip=False,
        widget=forms.PasswordInput(
            attrs={"class": "input", "autocomplete": "new-password"}
        ),
    )
    field_order = [
        "display_name",
        "phone",
        "email",
        "service_area",
        "password1",
        "password2",
    ]

    def __init__(self, *args, emirate=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.emirate = emirate
        if emirate is not None:
            # Only areas of the resolved emirate are selectable, server side.
            self.fields["service_area"].queryset = service_areas_for_emirate(emirate)

    def clean_phone(self) -> str:
        raw = self.cleaned_data["phone"]
        try:
            phone = normalise_phone(raw).e164
        except DomainError as exc:
            raise forms.ValidationError(exc.message) from exc
        if User.objects.filter(phone=phone).exists():
            raise forms.ValidationError(
                "This mobile number already has an account. Sign in instead."
            )
        return phone

    def clean_email(self) -> str:
        email = _normalise_email(self.cleaned_data.get("email", ""))
        if not email:
            return ""
        if (
            MemberEmail.objects.filter(email__iexact=email).exists()
            or User.objects.filter(email__iexact=email).exists()
        ):
            raise forms.ValidationError(
                "This email already belongs to another account."
            )
        return email

    def clean(self):
        cleaned = super().clean()
        password1 = cleaned.get("password1")
        password2 = cleaned.get("password2")
        if password1 and password2 and password1 != password2:
            self.add_error("password2", "The two passwords did not match.")
        if password1:
            try:
                password_validation.validate_password(password1)
            except forms.ValidationError as exc:
                self.add_error("password1", exc)

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


class ProfileForm(forms.Form):
    """Edit the member's name, optional email and area."""

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
        help_text="Optional. Used for sign-in codes and notifications.",
    )
    service_area = forms.ModelChoiceField(
        label="Your area",
        queryset=ServiceArea.objects.none(),
        empty_label="Select your area",
        widget=forms.Select(attrs={"class": "input"}),
    )

    def __init__(self, *args, instance=None, emirate=None, **kwargs):
        self.instance = instance
        self.emirate = emirate
        initial = kwargs.setdefault("initial", {})
        if instance is not None:
            initial.setdefault("display_name", instance.display_name)
            initial.setdefault("email", instance.primary_email)
            initial.setdefault("service_area", instance.service_area_id)
        super().__init__(*args, **kwargs)
        if emirate is not None:
            self.fields["service_area"].queryset = service_areas_for_emirate(emirate)

    def clean_email(self) -> str:
        email = _normalise_email(self.cleaned_data.get("email", ""))
        if not email:
            return ""
        email_rows = MemberEmail.objects.filter(email__iexact=email)
        legacy_rows = User.objects.filter(email__iexact=email)
        if self.instance is not None:
            email_rows = email_rows.exclude(user=self.instance)
            legacy_rows = legacy_rows.exclude(pk=self.instance.pk)
        if email_rows.exists() or legacy_rows.exists():
            raise forms.ValidationError(
                "This email already belongs to another account."
            )
        return email

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


class PasswordSettingsForm(forms.Form):
    """Set a first password, or change an existing one with the current password."""

    current_password = forms.CharField(
        label="Current password",
        required=False,
        strip=False,
        widget=forms.PasswordInput(
            attrs={"class": "input", "autocomplete": "current-password"}
        ),
    )
    new_password1 = forms.CharField(
        label="New password",
        strip=False,
        widget=forms.PasswordInput(
            attrs={"class": "input", "autocomplete": "new-password"}
        ),
        help_text=(
            "At least 8 characters with at least one uppercase letter, one digit "
            "and one special character. You can still sign in with a one-time code."
        ),
    )
    new_password2 = forms.CharField(
        label="Confirm new password",
        strip=False,
        widget=forms.PasswordInput(
            attrs={"class": "input", "autocomplete": "new-password"}
        ),
    )

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        if user.has_usable_password():
            self.fields["current_password"].required = True
        else:
            self.fields.pop("current_password")

    def clean_current_password(self) -> str:
        current = self.cleaned_data.get("current_password", "")
        if current and not self.user.check_password(current):
            raise forms.ValidationError("Your current password is not correct.")
        return current

    def clean(self):
        cleaned = super().clean()
        password1 = cleaned.get("new_password1")
        password2 = cleaned.get("new_password2")
        if password1 and password2 and password1 != password2:
            self.add_error("new_password2", "The two passwords did not match.")
        if password1:
            try:
                password_validation.validate_password(password1, self.user)
            except forms.ValidationError as exc:
                self.add_error("new_password1", exc)
        return cleaned


class EmirateChoiceForm(forms.Form):
    """Used by the marketplace switcher on non-emirate pages."""

    emirate = forms.SlugField(widget=forms.HiddenInput())
