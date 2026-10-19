"""Operational endpoints: health check and robots.txt."""

from __future__ import annotations

from django.db import connection
from django.http import HttpResponse, JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET


@require_GET
@never_cache
def healthz(request):
    """
    Liveness/readiness probe for the load balancer and monitoring.

    Returns 200 only when the database answers. Details are intentionally terse: a
    public endpoint must not leak versions, paths or configuration.
    """
    checks: dict[str, str] = {}
    healthy = True

    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        checks["database"] = "ok"
    except Exception:  # noqa: BLE001 - surface only a coarse status
        checks["database"] = "error"
        healthy = False

    try:
        from django.core.cache import cache

        cache.set("healthz", "1", timeout=5)
        checks["cache"] = "ok" if cache.get("healthz") == "1" else "error"
    except Exception:  # noqa: BLE001
        checks["cache"] = "error"

    return JsonResponse(
        {"status": "ok" if healthy else "unavailable", "checks": checks},
        status=200 if healthy else 503,
    )


@require_GET
def robots_txt(request):
    """Disallow crawling of private areas; the marketplace itself is crawlable."""
    lines = [
        "User-agent: *",
        "Disallow: /admin/",
        "Disallow: /my/",
        "Disallow: /accounts/",
        "Disallow: /ws/",
    ]
    return HttpResponse("\n".join(lines), content_type="text/plain")
