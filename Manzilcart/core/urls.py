"""Project-level URL routes that are not part of the public marketplace."""

from django.urls import path

from core import views

app_name = "core"

urlpatterns = [
    path("healthz", views.healthz, name="healthz"),
    path("robots.txt", views.robots_txt, name="robots_txt"),
]
