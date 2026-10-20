"""
Account views: the OTP sign-up / sign-in flow, profile management and the member
dashboard.

Flow
----
1. ``PhoneEntryView``        POST phone  -> challenge created, identical response whether
                                             or not the number already has an account.
2. ``VerifyCodeView``        POST code   -> verified: returning member is signed in,
                                             new member continues to the details step.
3. ``RegistrationDetailsView`` POST area -> account created, signed in.

All three steps keep their state in the server-side session; the challenge id is never
accepted from the browser, so a visitor cannot verify somebody else's challenge.
"""

from __future__ import annotations

import logging

from django.contrib.auth import logout as auth_logout
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.views import redirect_to_login
from django.http import Http404, HttpResponseRedirect
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views import View
from django.views.generic import FormView, TemplateView

from accounts import services
from accounts.forms import (
    PhoneNumberForm,
    ProfileForm,
    RegistrationDetailsForm,
    VerificationCodeForm,
)
from accounts.models import OTPChallenge, User
from core.exceptions import DomainError, RateLimitedError
from core.logging_utils import mask_phone
from locations.selectors import active_emirate_for_slug, emirate_from_session

logger = logging.getLogger("manzilcart.accounts.views")

SESSION_EMIRATE_PENDING = "manzilcart.pending_emirate_slug"


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


class PhoneEntryView(RegistrationStepMixin, FormView):
    """
    Step 1 - the single entry point for both registration and sign-in.

    Returning members and new members receive exactly the same response, which is how
    the platform avoids disclosing whether a number has an account.
    """

    form_class = PhoneNumberForm
    template_name = "accounts/phone_entry.html"

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return redirect("accounts:dashboard")
        # The emirate can be supplied by the marketplace URL (…/join/) or inherited
        # from the session.
        slug = kwargs.get("emirate_slug")
        if slug:
            emirate = active_emirate_for_slug(slug)
            if emirate is None:
                raise Http404("Marketplace not found.")
            request.session[SESSION_EMIRATE_PENDING] = emirate.slug
        self.emirate = active_emirate_for_slug(
            request.session.get(SESSION_EMIRATE_PENDING)
        ) or emirate_from_session(request) or getattr(request, "emirate", None)
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["emirate"] = self.emirate
        context["flow"] = "start"
        return context

    def form_valid(self, form):
        try:
            services.start_verification(
                raw_phone=form.cleaned_data["phone"],
                request=self.request,
                emirate=self.emirate if self.emirate and self.emirate.is_active else None,
            )
        except RateLimitedError as exc:
            form.add_error(None, exc.message)
            return self.form_invalid(form)
        except DomainError as exc:
            form.add_error("phone", exc.message)
            return self.form_invalid(form)

        # The challenge id lives in the session only.
        challenge = services.OTPChallenge.objects.filter(
            phone=form.cleaned_data["phone"]
        ).order_by("-created_at").first()
        if challenge is None:  # pragma: no cover - defensive
            form.add_error(None, "We could not start verification. Please try again.")
            return self.form_invalid(form)

        self.store_challenge(challenge)
        return redirect("accounts:verify")


class VerifyCodeView(RegistrationStepMixin, FormView):
    """Step 2 - verify the code."""

    form_class = VerificationCodeForm
    template_name = "accounts/verify_code.html"

    def dispatch(self, request, *args, **kwargs):
        self.challenge = self.get_challenge()
        if self.challenge is None:
            return redirect("accounts:start")
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["masked_phone"] = self.masked_phone()
        context["expires_seconds"] = max(0, int(self.challenge.expiry.total_seconds()))
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
        except DomainError as exc:
            form.add_error("code", exc.message)
            return self.form_invalid(form)

        if challenge.purpose == OTPChallenge.Purpose.LOGIN:
            try:
                services.sign_in_with_verified_challenge(
                    challenge=challenge, request=self.request
                )
            except DomainError as exc:
                self.clear_challenge()
                form.add_error(None, exc.message)
                return self.form_invalid(form)
            self.clear_challenge()
            self.request.session.pop(SESSION_EMIRATE_PENDING, None)
            return redirect(self.get_success_url())

        # New member: pick a residential area before the account is created.
        return redirect("accounts:details")

    def get_success_url(self) -> str:
        target = self.request.GET.get("next") or self.request.POST.get("next")
        if target and url_has_allowed_host_and_scheme(
            target, {self.request.get_host()}, require_https=self.request.is_secure()
        ):
            return target
        return reverse("accounts:dashboard")


class ResendCodeView(RegistrationStepMixin, View):
    """Resend the code for the current challenge, with all the send limits applied."""

    def post(self, request, *args, **kwargs):
        challenge = self.get_challenge()
        if challenge is None:
            return redirect("accounts:start")
        try:
            services.resend_verification(challenge=challenge, request=request)
        except RateLimitedError as exc:
            return render(
                request,
                "accounts/verify_code.html",
                {
                    "form": VerificationCodeForm(),
                    "masked_phone": self.masked_phone(),
                    "expires_seconds": max(0, int(challenge.expiry.total_seconds())),
                    "resend_error": exc.message,
                    "flow": "verify",
                },
            )
        except DomainError as exc:
            self.clear_challenge()
            return redirect("accounts:start")
        return redirect("accounts:verify")


class RegistrationDetailsView(RegistrationStepMixin, FormView):
    """Step 3 - name, optional email and supported service area."""

    form_class = RegistrationDetailsForm
    template_name = "accounts/registration_details.html"

    def dispatch(self, request, *args, **kwargs):
        self.challenge = self.get_challenge()
        if self.challenge is None or not self.challenge.is_verified:
            return redirect("accounts:start")
        if self.challenge.is_consumed:
            return redirect("accounts:start")

        self.emirate = (
            active_emirate_for_slug(request.session.get(SESSION_EMIRATE_PENDING))
            or emirate_from_session(request)
            or getattr(request, "emirate", None)
        )
        if self.emirate is None or not self.emirate.is_active:
            # Registration cannot proceed without a supported marketplace.
            return redirect("locations:landing")
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["emirate"] = self.emirate
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["emirate"] = self.emirate
        context["masked_phone"] = self.masked_phone()
        context["flow"] = "details"
        return context

    def form_valid(self, form):
        try:
            user = services.complete_registration(
                challenge=self.challenge,
                emirate_slug=self.emirate.slug,
                service_area_slug=form.cleaned_data["service_area"].slug,
                display_name=form.cleaned_data["display_name"],
                email=form.cleaned_data.get("email", ""),
                request=self.request,
            )
        except DomainError as exc:
            form.add_error(None, exc.message)
            return self.form_invalid(form)

        self.clear_challenge()
        self.request.session.pop(SESSION_EMIRATE_PENDING, None)

        from django.contrib.auth import login as auth_login

        auth_login(self.request, user)
        return redirect("accounts:dashboard")


class LogoutView(View):
    """POST-only logout (never GET, so a link cannot sign a member out)."""

    def post(self, request, *args, **kwargs):
        auth_logout(request)
        return redirect("locations:landing")

    def get(self, request, *args, **kwargs):
        return HttpResponseRedirect(reverse("locations:landing"))


class DashboardView(LoginRequiredMixin, TemplateView):
    """Member home: their marketplace, activity summaries and quick links."""

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
                "auction_marketplace:create",
                kwargs={"emirate_slug": emirate.slug},
            )
        return context


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
