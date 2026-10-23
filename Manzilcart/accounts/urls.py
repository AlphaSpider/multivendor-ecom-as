"""
Account URLs.

Auth routes live under ``/accounts/`` and the authenticated member area under ``/my/``.
They share one ``app_name`` namespace so views can reverse ``accounts:dashboard`` from
anywhere. Personal pages are deliberately *not* emirate-prefixed: a member's
conversations and deals can legitimately span more than one marketplace.
"""

from django.urls import path

from accounts import views
from catalog.views import MyListingsView

app_name = "accounts"

urlpatterns = [
    # Verification / sign-in flow.
    path("accounts/start/", views.PhoneEntryView.as_view(), name="start"),
    path("accounts/login/", views.PhoneEntryView.as_view(), name="login"),
    path("accounts/verify/", views.VerifyCodeView.as_view(), name="verify"),
    path("accounts/resend/", views.ResendCodeView.as_view(), name="resend"),
    path("accounts/details/", views.RegistrationDetailsView.as_view(), name="details"),
    path("accounts/logout/", views.LogoutView.as_view(), name="logout"),
    # Member area.
    path("my/", views.DashboardView.as_view(), name="dashboard"),
    path("my/listings/", MyListingsView.as_view(), name="listings"),
    path("my/profile/", views.ProfileView.as_view(), name="profile"),
    path("my/settings/", views.SettingsView.as_view(), name="settings"),
]
