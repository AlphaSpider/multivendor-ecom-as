"""Shared seller eligibility checks for listing and auction creation."""

from __future__ import annotations

from django.shortcuts import render

SELLER_ELIGIBILITY_MESSAGE = (
    "Your account is not ready to sell yet. Verify your mobile number and make sure "
    "your account is active, then try again."
)
SELLER_NOT_ELIGIBLE_TEMPLATE = "accounts/seller_not_eligible.html"


def seller_eligibility_error(user) -> str | None:
    """Return a user-facing reason when ``user`` may not create a marketplace item."""
    if not getattr(user, "is_active", False):
        return "Your account is not active. Contact support if you think this is a mistake."
    if not getattr(user, "is_mobile_verified", False):
        return SELLER_ELIGIBILITY_MESSAGE
    return None


class SellerEligibleMixin:
    """
    Render the same friendly message for both listing and auction creation.

    The mixin is intentionally placed *after* ``LoginRequiredMixin`` in each view's
    bases, so an anonymous visitor is redirected to login instead of seeing a 403.
    """

    def dispatch(self, request, *args, **kwargs):
        error = seller_eligibility_error(request.user)
        if error:
            return render(
                request,
                SELLER_NOT_ELIGIBLE_TEMPLATE,
                {"message": error},
                status=403,
            )
        return super().dispatch(request, *args, **kwargs)
