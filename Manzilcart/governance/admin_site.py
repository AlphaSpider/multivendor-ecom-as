"""
ManzilCart admin site.

The subclass reuses the ModelAdmin registry that other apps built against
``django.contrib.admin.site``: :meth:`governance.apps.GovernanceConfig.ready`
swaps the *class* of that instance, preserving every registration while adding
the governance pages and navigation below. This keeps one admin URL namespace
(``admin:``) for everything.

Authentication: ManzilCart accounts are OTP-only (there is no usable password),
so the stock admin username/password login can never succeed for staff. The
site therefore ships its own OTP login - staff enter their mobile number, get
a WhatsApp one-time code, and are signed in exactly like on the storefront.
A collapsible password panel remains for superuser accounts created with
``createsuperuser``.
"""

from __future__ import annotations

from django.contrib import admin, messages
from django.contrib.admin.forms import AdminAuthenticationForm
from django.contrib.auth import login as auth_login
from django.utils.decorators import method_decorator
from django.utils.translation import gettext, gettext_lazy as _
from django.views.decorators.cache import never_cache

GOVERNANCE_NAV = [
    {
        "name": _("Command centre"),
        "url_name": "admin:governance_dashboard",
    },
    {
        "name": _("Moderation inbox"),
        "url_name": "admin:governance_moderation_inbox",
    },
    {
        "name": _("Bid monitor"),
        "url_name": "admin:governance_bid_monitor",
    },
    {
        "name": _("Vendors"),
        "url_name": "admin:governance_vendors",
    },
    {
        "name": _("Deals & disputes"),
        "url_name": "admin:governance_financials",
    },
]

SESSION_CHALLENGE_ID = "manzilcart.admin.otp_challenge_id"
SESSION_NEXT = "manzilcart.admin.otp_next"


class ManzilcartAdminSite(admin.AdminSite):
    site_header = "ManzilCart governance"
    site_title = "ManzilCart admin"
    index_title = "Marketplace operations"

    def get_urls(self):
        from django.urls import path

        from governance import admin_views

        governance = [
            path("", self.admin_view(admin_views.dashboard), name="governance_dashboard"),
            path(
                "moderation-inbox/",
                self.admin_view(admin_views.moderation_inbox),
                name="governance_moderation_inbox",
            ),
            path(
                "bid-monitor/",
                self.admin_view(admin_views.bid_monitor),
                name="governance_bid_monitor",
            ),
            path(
                "bid-monitor/<int:auction_id>/",
                self.admin_view(admin_views.bid_auction),
                name="governance_bid_auction",
            ),
            path(
                "vendors/",
                self.admin_view(admin_views.vendors),
                name="governance_vendors",
            ),
            path(
                "financials/",
                self.admin_view(admin_views.financials),
                name="governance_financials",
            ),
        ]
        # NOTE: custom URLs must be registered BEFORE the default admin URLs;
        # the default pattern list ends with a globbing app_index fallback that
        # would swallow "bid-monitor/" etc. as a (missing) app page.
        governance_and_login = governance + [
            # Own OTP-first login page; the stock password route stays mounted
            # (same URL name resolves to this view, which falls back to a
            # password form for command-line superuser accounts).
            path("login/", self.login, name="login"),
        ]
        return governance_and_login + super().get_urls()

    def each_context(self, request):
        # ModelAdmin URLs are resolved lazily; import here avoids an
        # import cycle at module load.
        from django.urls import reverse_lazy

        context = super().each_context(request)
        context["governance_nav"] = [
            {**item, "url": reverse_lazy(item["url_name"])} for item in GOVERNANCE_NAV
        ]
        context["site_header"] = self.site_header
        context["site_title"] = self.site_title
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated:
            context["governance_unread_threads"] = _unread_threads_cached(request)
        return context

    # -- OTP-first authentication --------------------------------------------
    @method_decorator(never_cache)
    def login(self, request, extra_context=None):
        """
        One page, two steps: a WhatsApp one-time code replaces the password
        store, with the stock admin password form as a command-line fallback.
        """
        from django.contrib import messages as dj_messages
        from django.contrib.auth import get_backends
        from django.http import HttpResponseRedirect
        from django.shortcuts import render
        from django.urls import reverse
        from django.utils.http import url_has_allowed_host_and_scheme

        from accounts import services as accounts_services
        from accounts.models import User
        from core.exceptions import DomainError, RateLimitedError

        def response_target(url: str) -> HttpResponseRedirect:
            return HttpResponseRedirect(url)

        step = request.POST.get("step") or request.GET.get("step") or "phone"
        next_url = request.POST.get("next") or request.GET.get("next") or ""
        password_form = AdminAuthenticationForm(request)

        def safe_next(candidate: str | None, default: str) -> str:
            if candidate and url_has_allowed_host_and_scheme(
                candidate, allowed_hosts={request.get_host()}
            ):
                return candidate
            return default

        if request.method == "GET" and self._is_signed_in(request):
            return HttpResponseRedirect(safe_next(next_url, reverse("admin:index")))

        if request.method == "POST":
            if step == "phone":
                try:
                    result = accounts_services.start_verification(
                        raw_phone=request.POST.get("phone", ""), request=request
                    )
                except (DomainError, RateLimitedError) as exc:
                    dj_messages.error(request, str(exc))
                else:
                    request.session[SESSION_CHALLENGE_ID] = str(result.challenge.pk)
                    request.session[SESSION_NEXT] = safe_next(
                        next_url, reverse("admin:index")
                    )
                    return response_target(
                        reverse("admin:login") + f"?step=code&phone={result.challenge.phone}"
                    )
            elif step == "password":
                password_form = AdminAuthenticationForm(request, data=request.POST)
                if password_form.is_valid():
                    user = password_form.get_user()
                    auth_login(
                        request, user, backend=password_form.user_cache.backend
                    )
                    request.session.cycle_key()
                    return response_target(safe_next(next_url, reverse("admin:index")))
            elif step == "code" and request.POST.get("action") == "resend":
                challenge = self._session_challenge(request)
                try:
                    accounts_services.resend_verification(
                        challenge=challenge, request=request
                    )
                except (DomainError, RateLimitedError) as exc:
                    dj_messages.error(request, str(exc))
                else:
                    dj_messages.success(request, gettext_now())
                return response_target(reverse("admin:login") + "?step=code")
            elif step == "code":
                challenge = self._session_challenge(request)
                try:
                    verified = accounts_services.verify_challenge_code(
                        challenge=challenge,
                        submitted_code=(request.POST.get("code") or "").strip(),
                    )
                except (DomainError, RateLimitedError) as exc:
                    dj_messages.error(request, str(exc))
                    return response_target(reverse("admin:login") + "?step=code")

                user = User.objects.filter(phone=verified.phone).first()
                if user is None:
                    self._reject(request, verified, dj_messages, gettext(
                        "No ManzilCart account uses this number yet. "
                        "Please register on the marketplace first."
                    ))
                    return response_target(reverse("admin:login") + "?step=phone")
                if not user.is_active:
                    self._reject(request, verified, dj_messages, gettext(
                        "This account is not available. Please contact support."
                    ))
                    return response_target(reverse("admin:login") + "?step=phone")
                if not (user.is_staff and user.is_active):
                    self._reject(request, verified, dj_messages, gettext(
                        "This account does not have admin access. "
                        "Use the marketplace app to sell."
                    ))
                    return response_target(reverse("admin:login") + "?step=phone")

                accounts_services.sign_in_with_verified_challenge(
                    challenge=verified, request=request
                )
                target = request.session.pop(SESSION_NEXT, None)
                return response_target(safe_next(target, reverse("admin:index")))

        context = {
            **self.each_context(request),
            "title": _("Log in"),
            "governance_nav": [],  # keep the chrome clean until signed in
            "step": step,
            "next": next_url,
            "stored_phone": request.GET.get("phone") or "",
            "password_form": password_form,
            "show_password": step == "password",
            "challenge_alive": self._session_challenge(request) is not None,
            "backends_available": bool(get_backends()),
        }
        return render(request, "admin/governance/otp_login.html", context)

    @staticmethod
    def _reject(request, verified_challenge, dj_messages, text: str) -> None:
        # The code was proven; consume the challenge so it cannot be replayed
        # and show a plain, non-membership-leaking error.
        verified_challenge.mark_consumed()
        dj_messages.error(request, text)

    @staticmethod
    def gettext_now():  # tiny helper keeps the branch short
        from django.utils.translation import gettext

        return gettext("A new code was sent to your phone.")

    def _session_challenge(self, request):
        from accounts.models import OTPChallenge

        raw = request.session.get(SESSION_CHALLENGE_ID)
        if not raw:
            return None
        challenge = OTPChallenge.objects.filter(pk=raw).first()
        if (
            challenge is None
            or challenge.is_consumed
            or challenge.is_verified
            or challenge.is_expired
        ):
            return None
        return challenge

    def _is_signed_in(self, request) -> bool:
        user = getattr(request, "user", None)
        return bool(user and user.is_authenticated and user.is_active and user.is_staff)


def _unread_threads_cached(request):
    """Small per-request memo so every admin page does not recount messages."""
    from django.core.cache import cache

    key = "governance:unread_threads"
    value = cache.get(key)
    if value is None:
        from governance.models import ListingChatThread

        value = ListingChatThread.unread_staff_threads()
        cache.set(key, value, 30)
    assert request is not None  # keep the signature honest
    return value
