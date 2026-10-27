from django.urls import path

from governance import views

app_name = "governance"

urlpatterns = [
    path("my/support/", views.SupportThreadListView.as_view(), name="support_threads"),
    path("my/support/<int:pk>/", views.SupportThreadDetailView.as_view(), name="support_thread"),
]
