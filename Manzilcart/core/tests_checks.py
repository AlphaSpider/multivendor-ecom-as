"""Deployment checks must be registered and must fire for unsafe production setups."""

from __future__ import annotations

from django.test import SimpleTestCase, override_settings

from core.checks import (
    email_configuration_check,
    notification_mode_check,
    production_cache_layer_check,
)

CONSOLE_MAILER = {
    "default": {"BACKEND": "django.core.mail.backends.console.EmailBackend"}
}
SMTP_MAILER = {
    "default": {"BACKEND": "django.core.mail.backends.smtp.EmailBackend"}
}


class DeploymentCheckTests(SimpleTestCase):
    @override_settings(
        IS_PRODUCTION=False,
        NOTIFICATIONS_MODE="mock",
        REDIS_URL="",
        MAILERS=CONSOLE_MAILER,
    )
    def test_checks_are_silent_in_development(self):
        self.assertEqual(production_cache_layer_check(None), [])
        self.assertEqual(notification_mode_check(None), [])
        self.assertEqual(email_configuration_check(None), [])

    @override_settings(IS_PRODUCTION=True, REDIS_URL="")
    def test_production_without_redis_warns(self):
        issues = production_cache_layer_check(None)

        self.assertEqual([issue.id for issue in issues], ["manzilcart.W001"])

    @override_settings(IS_PRODUCTION=True, REDIS_URL="redis://localhost:6379/0")
    def test_production_with_redis_is_clean(self):
        self.assertEqual(production_cache_layer_check(None), [])

    @override_settings(IS_PRODUCTION=True, NOTIFICATIONS_MODE="mock")
    def test_production_with_mock_notifications_warns(self):
        issues = notification_mode_check(None)

        self.assertEqual([issue.id for issue in issues], ["manzilcart.W002"])

    @override_settings(IS_PRODUCTION=True, NOTIFICATIONS_MODE="live")
    def test_production_with_live_notifications_is_clean(self):
        self.assertEqual(notification_mode_check(None), [])

    @override_settings(IS_PRODUCTION=True, MAILERS=CONSOLE_MAILER)
    def test_production_with_a_console_mailer_warns(self):
        issues = email_configuration_check(None)

        self.assertEqual([issue.id for issue in issues], ["manzilcart.W003"])

    @override_settings(IS_PRODUCTION=True, MAILERS=SMTP_MAILER)
    def test_production_with_a_real_mailer_is_clean(self):
        self.assertEqual(email_configuration_check(None), [])
