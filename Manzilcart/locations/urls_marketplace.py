"""
Routes mounted under ``/<emirate_slug>/``.

Every view here inherits ``EmirateRequiredMixin``, so the slug is validated against
the active emirate set before anything else happens.
"""

from django.urls import path

from locations import views

app_name = "marketplace"

urlpatterns = [
    path("", views.MarketplaceHomeView.as_view(), name="home"),
    path("areas.json", views.service_area_options, name="service_area_options"),
]
