"""
Root URL configuration.

Ordering matters:

1. ``/admin/`` and operational endpoints first.
2. Auth and member pages (fixed prefixes).
3. Media files in development.
4. **Last**, the emirate-scoped marketplace ``/<emirate_slug>/...``.

The marketplace catch-all is last on purpose: it must never shadow ``/admin/`` or
``/accounts/``. The slug itself is validated against the active emirate set by
``EmirateRequiredMixin``, so unknown or deactivated marketplaces return a friendly 404.
"""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("core.urls")),
    path("", include("accounts.urls")),
    path("", include("conversations.urls")),
    path("", include("deals.urls")),
    path("", include("auctions.urls")),
    path("", include("locations.urls_public")),
    path("", include("governance.urls")),
]

if settings.DEBUG:
    # Development only. In production Whitenoise serves static files and object
    # storage (or the web server) serves media.
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

urlpatterns += [
    path("<slug:emirate_slug>/listings/", include("catalog.urls_marketplace")),
    path("<slug:emirate_slug>/auctions/", include("auctions.urls_marketplace")),
    path("<slug:emirate_slug>/", include("locations.urls_marketplace")),
]
