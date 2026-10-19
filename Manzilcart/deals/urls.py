"""Personal deal routes, mounted at the site root (not emirate-prefixed)."""

from django.urls import path

from deals import views

app_name = "deals"

urlpatterns = [
    path("my/deals/", views.DealListView.as_view(), name="list"),
    path(
        "my/deals/start/<int:listing_id>/",
        views.StartDealView.as_view(),
        name="start",
    ),
    path("my/deals/<int:pk>/", views.DealDetailView.as_view(), name="detail"),
    path("my/deals/<int:pk>/action/", views.DealActionView.as_view(), name="action"),
]
