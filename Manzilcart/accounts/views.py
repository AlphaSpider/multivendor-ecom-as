"""
Account views: sign-up, OTP/password sign-in, profile management and the member
dashboard.

Flow
----
1. ``RegisterView``          POST phone/email/password -> account created immediately.
2. ``LoginStartView``        POST phone/email -> OTP challenge sent to WhatsApp and email.
3. ``VerifyCodeView``        POST code -> signed in; phone/email marked verified.
4. ``PasswordLoginView``     POST phone/email + password -> signed in.

The challenge id lives in the server-side session only; the browser cannot verify
somebody else's challenge.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login as auth_login, logout as auth_logout
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.views import redirect_to_login
from django.contrib.contenttypes.models import ContentType
from django.db.models import Q
from django.http import HttpResponseRedirect
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views import View
from django.views.generic import FormView, TemplateView

from accounts import services
from accounts.forms import (
    GENERIC_LOGIN_ERROR,
    LoginIdentifierForm,
    PasswordLoginForm,
    PasswordSettingsForm,
    ProfileForm,
    RegistrationForm,
    VerificationCodeForm,
    resolve_member_by_identifier,
)
from accounts.models import OTPChallenge
from core.exceptions import DomainError, RateLimitedError
from core.logging_utils import mask_email, mask_phone
from locations.selectors import (
    active_emirate_for_slug,
    emirate_from_session,
    remember_emirate_in_session,
)

logger = logging.getLogger("manzilcart.accounts.views")

SESSION_EMIRATE_PENDING = "manzilcart.pending_emirate_slug"
SESSION_LOGIN_NEXT = "manzilcart.login_next"


class RegistrationStepMixin:
    """Shared helpers for the multi-step verification flow."""

    def get_challenge(self) -> OTPChallenge | None:
        challenge_id = self.request.session.get(services.SESSION_CHALLENGE_ID)
        if not challenge_id:
            return None
        return OTPChallenge.objects.filter(pk=challenge_id).first()

    def store_challenge(self, challenge: OTPChallenge) -> None:
        self.request.session[services.SESSION_CHALLENGE_ID] = challenge.pk
        self.request.session[services.SESSION_PHONE] = challenge.phone
        # Keep the session usable through the whole (short) flow.
        self.request.session.set_expiry(60 * 30)

    def clear_challenge(self) -> None:
        for key in (services.SESSION_CHALLENGE_ID, services.SESSION_PHONE):
            self.request.session.pop(key, None)

    def masked_phone(self) -> str:
        phone = self.request.session.get(services.SESSION_PHONE)
        return mask_phone(phone) if phone else ""

    def masked_email(self) -> str:
        challenge = self.get_challenge()
        user = getattr(challenge, "user", None)
        if user is None:
            return ""
        return mask_email(user.primary_email)


def get_post_login_redirect_url(request, user=None) -> str:
    """Determine the home redirect URL for a signed-in member.

    Redirects to the marketplace home for the member's emirate (or session emirate),
    falling back to the site landing page.
    """
    u = user if user is not None else getattr(request, "user", None)
    emirate = None
    if u and getattr(u, "is_authenticated", False):
        emirate = getattr(u, "emirate", None)
    if emirate is None:
        emirate = emirate_from_session(request)

    if emirate and getattr(emirate, "is_active", False):
        remember_emirate_in_session(request, emirate)
        return reverse("marketplace:home", kwargs={"emirate_slug": emirate.slug})

    return reverse("locations:landing")


class LoginStartView(RegistrationStepMixin, FormView):
    """Default sign-in: phone/email first, then a one-time code."""

    form_class = LoginIdentifierForm
    template_name = "accounts/phone_entry.html"

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return redirect(get_post_login_redirect_url(request))
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["flow"] = "login"
        context["next"] = (
            self.request.GET.get("next") or self.request.POST.get("next") or ""
        )
        return context

    def form_valid(self, form):
        member = resolve_member_by_identifier(form.cleaned_data["identifier"])
        # Unknown or inactive identifiers take the same path as a real member: a
        # verification challenge is created and the visitor sees the same next step.
        # Only an active member's challenge is dispatched to any channel.
        try:
            if member is None or not member.is_active:
                challenge = services.start_decoy_verification(request=self.request)
            else:
                challenge = services.start_login_verification(
                    user=member, request=self.request
                )
        except RateLimitedError as exc:
            form.add_error(None, exc.message)
            return self.form_invalid(form)
        except DomainError:
            form.add_error(None, GENERIC_LOGIN_ERROR)
            return self.form_invalid(form)

        target = self.request.GET.get("next") or self.request.POST.get("next")
        if target and url_has_allowed_host_and_scheme(
            target, {self.request.get_host()}, require_https=self.request.is_secure()
        ):
            self.request.session[SESSION_LOGIN_NEXT] = target
        else:
            self.request.session.pop(SESSION_LOGIN_NEXT, None)

        self.store_challenge(challenge)
        return redirect("accounts:verify")


class RegisterView(RegistrationStepMixin, FormView):
    """Create an account immediately with phone, optional email and password."""

    form_class = RegistrationForm
    template_name = "accounts/register.html"

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return redirect(get_post_login_redirect_url(request))
        self.emirate = (
            active_emirate_for_slug(request.session.get(SESSION_EMIRATE_PENDING))
            or emirate_from_session(request)
            or getattr(request, "emirate", None)
        )
        if self.emirate is None or not self.emirate.is_active:
            # The signup form needs a supported marketplace for the area selector.
            return redirect("locations:landing")
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["emirate"] = self.emirate
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["emirate"] = self.emirate
        context["flow"] = "register"
        return context

    def form_valid(self, form):
        try:
            user = services.register_member(
                phone=form.cleaned_data["phone"],
                email=form.cleaned_data.get("email", ""),
                password=form.cleaned_data["password1"],
                display_name=form.cleaned_data["display_name"],
                emirate_slug=self.emirate.slug,
                service_area_slug=form.cleaned_data["service_area"].slug,
                request=self.request,
            )
        except DomainError as exc:
            form.add_error(None, exc.message)
            return self.form_invalid(form)

        self.request.session.pop(SESSION_EMIRATE_PENDING, None)
        auth_login(self.request, user)
        return redirect(get_post_login_redirect_url(self.request, user=user))


class PasswordLoginView(FormView):
    """Sign in with the mobile number and the password set by the member."""

    form_class = PasswordLoginForm
    template_name = "accounts/password_login.html"

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return redirect(get_post_login_redirect_url(request))
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["request"] = self.request
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["next"] = (
            self.request.GET.get("next") or self.request.POST.get("next") or ""
        )
        return context

    def form_valid(self, form):
        user = form.get_user()
        try:
            services.sign_in_with_password(
                user=user,
                request=self.request,
                backend=form.user_cache.backend,
            )
        except DomainError as exc:
            form.add_error(None, exc.message)
            return self.form_invalid(form)

        form.reset_throttle()
        return redirect(self.get_success_url(user))

    def get_success_url(self, user) -> str:
        target = self.request.GET.get("next") or self.request.POST.get("next")
        if target and url_has_allowed_host_and_scheme(
            target, {self.request.get_host()}, require_https=self.request.is_secure()
        ):
            return target
        return get_post_login_redirect_url(self.request, user=user)


class VerifyCodeView(RegistrationStepMixin, FormView):
    """Step 2 - verify the code."""

    form_class = VerificationCodeForm
    template_name = "accounts/verify_code.html"

    def dispatch(self, request, *args, **kwargs):
        self.challenge = self.get_challenge()
        if self.challenge is None:
            return redirect("accounts:login")
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["masked_phone"] = self.masked_phone()
        context["masked_email"] = self.masked_email()
        context["has_email"] = bool(context["masked_email"])
        context["expires_seconds"] = max(0, int(self.challenge.expiry.total_seconds()))
        context["resend_cooldown_seconds"] = settings.OTP_RESEND_COOLDOWN_SECONDS
        context["flow"] = "verify"
        return context

    def form_valid(self, form):
        try:
            challenge = services.verify_challenge_code(
                challenge=self.challenge, submitted_code=form.cleaned_data["code"]
            )
        except RateLimitedError as exc:
            self.clear_challenge()
            form.add_error(None, exc.message)
            return self.form_invalid(form)
        except DomainError:
            form.add_error("code", GENERIC_LOGIN_ERROR)
            return self.form_invalid(form)

        if challenge.purpose != OTPChallenge.Purpose.LOGIN:
            self.clear_challenge()
            return redirect("accounts:login")

        try:
            services.sign_in_with_verified_challenge(
                challenge=challenge, request=self.request
            )
        except DomainError:
            self.clear_challenge()
            form.add_error(None, GENERIC_LOGIN_ERROR)
            return self.form_invalid(form)
        self.clear_challenge()
        self.request.session.pop(SESSION_EMIRATE_PENDING, None)
        return redirect(self.get_success_url())

    def get_success_url(self) -> str:
        target = self.request.session.pop(SESSION_LOGIN_NEXT, None)
        if target and url_has_allowed_host_and_scheme(
            target, {self.request.get_host()}, require_https=self.request.is_secure()
        ):
            return target
        target = self.request.GET.get("next") or self.request.POST.get("next")
        if target and url_has_allowed_host_and_scheme(
            target, {self.request.get_host()}, require_https=self.request.is_secure()
        ):
            return target
        return get_post_login_redirect_url(self.request)


class ResendCodeView(RegistrationStepMixin, View):
    """Resend the code for the current challenge, with all the send limits applied."""

    def post(self, request, *args, **kwargs):
        challenge = self.get_challenge()
        if challenge is None:
            return redirect("accounts:login")
        try:
            services.resend_verification(challenge=challenge, request=request)
        except RateLimitedError as exc:
            masked_email = self.masked_email()
            return render(
                request,
                "accounts/verify_code.html",
                {
                    "form": VerificationCodeForm(),
                    "masked_phone": self.masked_phone(),
                    "masked_email": masked_email,
                    "has_email": bool(masked_email),
                    "expires_seconds": max(0, int(challenge.expiry.total_seconds())),
                    "resend_cooldown_seconds": settings.OTP_RESEND_COOLDOWN_SECONDS,
                    "resend_error": exc.message,
                    "flow": "verify",
                },
            )
        except DomainError:
            self.clear_challenge()
            return redirect("accounts:login")
        return redirect("accounts:verify")


class LogoutView(View):
    """POST-only logout (never GET, so a link cannot sign a member out)."""

    def post(self, request, *args, **kwargs):
        auth_logout(request)
        return redirect("locations:landing")

    def get(self, request, *args, **kwargs):
        return HttpResponseRedirect(reverse("locations:landing"))


class DashboardView(LoginRequiredMixin, TemplateView):
    """Member home: identity, activity counts and quick selling actions."""

    template_name = "accounts/dashboard.html"
    login_url = "accounts:login"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        context["member"] = user
        emirate = user.emirate or getattr(self.request, "emirate", None)
        context["emirate"] = emirate
        context["service_area"] = user.service_area
        if emirate is not None and getattr(emirate, "is_active", False):
            context["auction_create_url"] = reverse(
                "auction_marketplace:apply",
                kwargs={"emirate_slug": emirate.slug},
            )
            context["listing_create_url"] = reverse(
                "catalog:listing_create",
                kwargs={"emirate_slug": emirate.slug},
            )

        display = (user.display_name or "").split()
        if display:
            context["initials"] = "".join(word[0] for word in display[:2]).upper()

        # Activity counts. Two-way stats need the union of both roles, so they are
        # summed; every query is a cheap indexed count scoped to one member.
        context["listings_count"] = user.listings.count()
        context["wishlist_count"] = user.wishlist_items.count()
        context["auctions_count"] = (
            user.auctions.count()
            + user.bids.count()
            + user.auction_applications.count()
        )
        context["conversations_count"] = (
            user.buying_conversations.count() + user.selling_conversations.count()
        )
        context["deals_count"] = user.purchases.count() + user.sales.count()
        return context


class SettingsView(LoginRequiredMixin, FormView):
    """
    Account settings: interface language, appearance, password, browsing
    marketplace and sign-out. Verified details (name, email, area) remain on the
    profile page; this page only offers choices that need no verification.
    """

    form_class = PasswordSettingsForm
    template_name = "accounts/settings.html"
    login_url = "accounts:login"

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["member"] = self.request.user
        context["emirate"] = self.request.user.emirate or getattr(
            self.request, "emirate", None
        )
        context["service_area"] = self.request.user.service_area
        return context

    def form_valid(self, form):
        services.update_password(
            user=self.request.user,
            password=form.cleaned_data["new_password1"],
            request=self.request,
        )

        messages.success(self.request, "Your password has been updated.")
        return redirect("accounts:settings")


class ProfileView(LoginRequiredMixin, FormView):
    """Profile management, including a validated move to another supported area."""

    form_class = ProfileForm
    template_name = "accounts/profile.html"
    login_url = "accounts:login"

    def get_emirate(self):
        return self.request.user.emirate or getattr(self.request, "emirate", None)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["instance"] = self.request.user
        kwargs["emirate"] = self.get_emirate()
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["emirate"] = self.get_emirate()
        context["member"] = self.request.user
        return context

    def form_valid(self, form):
        try:
            services.update_profile(
                user=self.request.user,
                display_name=form.cleaned_data["display_name"],
                emirate_slug=self.get_emirate().slug,
                service_area_slug=form.cleaned_data["service_area"].slug,
                email=form.cleaned_data.get("email", ""),
                request=self.request,
            )
        except DomainError as exc:
            form.add_error(None, exc.message)
            return self.form_invalid(form)

        from django.contrib import messages

        messages.success(self.request, "Your profile has been updated.")
        return redirect("accounts:profile")
