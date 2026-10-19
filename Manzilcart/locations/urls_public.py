"""Public (non emirate-scoped) location routes: landing page and switching."""

from django.urls import path

from locations import views

app_name = "locations"

urlpatterns = [
    path("", views.EmirateLandingView.as_view(), name="landing"),
    path(
        "marketplace/switch/",
        views.switch_marketplace_view,
        name="switch_marketplace",
    ),
]
