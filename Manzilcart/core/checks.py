"""
Deployment checks (``manage.py check --deploy`` and every normal ``check`` run).

These catch configuration combinations that are valid Python but unsafe or
non-functional in production - most importantly a missing Redis instance, which
would silently break shared rate limiting and WebSocket fan-out across workers.
"""

from __future__ import annotations

from django.conf import settings
from django.core.checks import Warning, register


@register()
def production_cache_layer_check(app_configs, **kwargs):
    """Warn when production runs without Redis."""
    issues = []
    if not getattr(settings, "IS_PRODUCTION", False):
        return issues
    if not settings.REDIS_URL:
        issues.append(
            Warning(
                "Redis is not configured (REDIS_URL is empty).",
                hint=(
                    "In production the cache and channel layer must be shared between "
                    "processes. Set REDIS_URL, otherwise rate limits can be bypassed "
                    "by hitting a different worker and chat messages are not fanned out."
                ),
                id="manzilcart.W001",
            )
        )
    return issues


@register()
def notification_mode_check(app_configs, **kwargs):
    """Notifications run in mock mode: fine for staging, not for production."""
    issues = []
    if not getattr(settings, "IS_PRODUCTION", False):
        return issues
    if settings.NOTIFICATIONS_MODE != "live":
        issues.append(
            Warning(
                "NOTIFICATIONS_MODE is not set to 'live' in production.",
                hint=(
                    "Mobile OTPs, deal notifications and operations alerts will be "
                    "recorded but never delivered. Configure the provider "
                    "credentials and set NOTIFICATIONS_MODE=live before launch."
                ),
                id="manzilcart.W002",
            )
        )
    return issues


@register()
def email_configuration_check(app_configs, **kwargs):
    """Production must have a working email provider for auction confirmations."""
    issues = []
    if not getattr(settings, "IS_PRODUCTION", False):
        return issues

    # Django 6.1 uses the MAILERS alias mapping, so there is no EMAIL_HOST setting to
    # inspect. Treat console/locmem/dummy backends as "not configured".
    mailers = getattr(settings, "MAILERS", {}) or {}
    development_backends = {
        "django.core.mail.backends.console.EmailBackend",
        "django.core.mail.backends.locmem.EmailBackend",
        "django.core.mail.backends.dummy.EmailBackend",
    }
    configured = [
        config.get("BACKEND")
        for config in mailers.values()
        if config.get("BACKEND") not in development_backends
    ]
    if not configured:
        issues.append(
            Warning(
                "Email is not configured for production (MAILERS use a development backend).",
                hint=(
                    "Auction bid confirmation and fallback invitations are delivered "
                    "by email. Set EMAIL_HOST and the SMTP/SES credentials."
                ),
                id="manzilcart.W003",
            )
        )
    return issues
