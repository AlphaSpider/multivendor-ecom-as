"""
Account URLs.

Auth routes live under ``/accounts/`` and the authenticated member area under ``/my/``.
They share one ``app_name`` namespace so views can reverse ``accounts:dashboard`` from
anywhere. Personal pages are deliberately *not* emirate-prefixed: a member's
conversations and deals can legitimately span more than one marketplace.
"""

from django.urls import path
from django.views.generic import RedirectView

from accounts import views
from catalog.views import MyListingsView, MyWishlistView

app_name = "accounts"

urlpatterns = [
    # Sign-up / sign-in flow.
    path("accounts/start/", views.LoginStartView.as_view(), name="start"),
    path("accounts/register/", views.RegisterView.as_view(), name="register"),
    path("accounts/login/", views.LoginStartView.as_view(), name="login"),
    path(
        "accounts/password/",
        views.PasswordLoginView.as_view(),
        name="password_login",
    ),
    path("accounts/verify/", views.VerifyCodeView.as_view(), name="verify"),
    path("accounts/resend/", views.ResendCodeView.as_view(), name="resend"),
    path(
        "accounts/details/",
        RedirectView.as_view(pattern_name="accounts:register", permanent=False),
        name="details",
    ),
    path("accounts/logout/", views.LogoutView.as_view(), name="logout"),
    # Member area.
    path("my/", views.DashboardView.as_view(), name="dashboard"),
    path("my/listings/", MyListingsView.as_view(), name="listings"),
    path("my/wishlist/", MyWishlistView.as_view(), name="wishlist"),
    path("my/profile/", views.ProfileView.as_view(), name="profile"),
    path("my/settings/", views.SettingsView.as_view(), name="settings"),
]
