"""
Notification tests: the database-backed queue, idempotency, retry/backoff, the worker
command, manual retry and the mock/live backend boundary.
"""

from __future__ import annotations

from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from core.exceptions import ServiceUnavailableError
from core.models import AuditEvent
from locations.models import Emirate
from notifications import services
from notifications.admin import NotificationAdmin
from notifications.backends.base import DeliveryResult
from notifications.models import Notification


class NotificationTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.emirate = Emirate.objects.get(slug="abu-dhabi")

    def setUp(self):
        # The mock backend keeps an in-memory outbox for assertions.
        from notifications.backends.base import MockNotificationBackend

        MockNotificationBackend.outbox.clear()

    def queue(self, **overrides) -> Notification:
        values = {
            "channel": Notification.Channel.WHATSAPP,
            "kind": Notification.Kind.ACCOUNT,
            "recipient": "+971501234567",
            "subject": "Test subject",
            "body": "Test body",
        }
        values.update(overrides)
        return services.queue_notification(**values)

    def outbox(self) -> list[dict]:
        from notifications.backends.base import MockNotificationBackend

        return MockNotificationBackend.outbox


@override_settings(NOTIFICATIONS_MODE="mock")
class QueueDeliveryTests(NotificationTestBase):
    def test_queue_delivers_immediately_with_the_mock_backend(self):
        notification = self.queue()

        notification.refresh_from_db()
        self.assertEqual(notification.status, Notification.Status.SENT)
        self.assertEqual(notification.provider, "mock")
        self.assertEqual(
            notification.provider_message_id, f"mock-{notification.pk}"
        )
        self.assertIsNotNone(notification.sent_at)
        self.assertEqual(notification.last_error, "")
        self.assertEqual(notification.attempts, 0)

        self.assertEqual(len(self.outbox()), 1)
        self.assertEqual(self.outbox()[0]["recipient"], "+971501234567")
        self.assertEqual(self.outbox()[0]["body"], "Test body")

    def test_idempotency_key_prevents_duplicate_rows_and_sends(self):
        first = self.queue(idempotency_key="ops:deal:1:accepted")
        second = self.queue(
            idempotency_key="ops:deal:1:accepted", body="Should be ignored"
        )

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(Notification.objects.count(), 1)
        self.assertEqual(len(self.outbox()), 1)
        self.assertEqual(Notification.objects.get().body, "Test body")

    def test_entity_and_context_are_recorded_for_operators(self):
        notification = self.queue(
            entity=self.emirate,
            emirate=self.emirate,
            user=None,
        )

        notification.refresh_from_db()
        self.assertEqual(notification.entity_type, "locations.emirate")
        self.assertEqual(notification.entity_id, str(self.emirate.pk))
        self.assertEqual(notification.emirate, self.emirate)

    def test_send_now_false_leaves_the_row_pending(self):
        notification = self.queue(send_now=False)

        notification.refresh_from_db()
        self.assertEqual(notification.status, Notification.Status.PENDING)
        self.assertEqual(notification.attempts, 0)
        self.assertEqual(self.outbox(), [])

    def test_delivering_an_already_sent_row_is_a_no_op(self):
        notification = self.queue()
        notification.refresh_from_db()
        sent_at = notification.sent_at

        returned = services.deliver(notification)

        returned.refresh_from_db()
        self.assertEqual(returned.status, Notification.Status.SENT)
        self.assertEqual(returned.sent_at, sent_at)
        self.assertEqual(len(self.outbox()), 1)

    def test_cancelled_row_is_not_delivered(self):
        notification = self.queue(send_now=False)
        notification.cancel()

        returned = services.deliver(notification)

        returned.refresh_from_db()
        self.assertEqual(returned.status, Notification.Status.CANCELLED)
        self.assertEqual(self.outbox(), [])
        self.assertFalse(
            Notification.objects.due(now=timezone.now())
            .filter(pk=notification.pk)
            .exists()
        )

    def test_send_otp_builds_the_expected_message(self):
        notification = services.send_otp(
            phone="+971501234567",
            code="654321",
            ttl_seconds=300,
            idempotency_key="otp:test",
        )

        notification.refresh_from_db()
        self.assertEqual(notification.channel, Notification.Channel.WHATSAPP)
        self.assertEqual(notification.kind, Notification.Kind.OTP)
        self.assertIn("654321", notification.body)
        self.assertIn("5 minute", notification.body)
        self.assertEqual(notification.status, Notification.Status.SENT)
        self.assertEqual(notification.idempotency_key, "otp:test")

    def test_due_queryset_includes_only_pending_rows_ready_now(self):
        due = self.queue(send_now=False)
        future = self.queue(send_now=False)
        sent = self.queue()
        Notification.objects.filter(pk=future.pk).update(
            next_attempt_at=timezone.now() + timedelta(hours=1)
        )

        due_ids = set(
            Notification.objects.due(now=timezone.now()).values_list(
                "pk", flat=True
            )
        )

        self.assertEqual(due_ids, {due.pk})
        self.assertNotIn(future.pk, due_ids)
        self.assertNotIn(sent.pk, due_ids)


@override_settings(NOTIFICATIONS_MODE="mock")
class FailureHandlingTests(NotificationTestBase):
    def test_retryable_provider_failure_is_recorded_and_backed_off(self):
        notification = self.queue(send_now=False)

        with patch(
            "notifications.services.get_backend",
            side_effect=ServiceUnavailableError("provider down"),
        ):
            returned = services.deliver(notification)

        returned.refresh_from_db()
        self.assertEqual(returned.status, Notification.Status.PENDING)
        self.assertEqual(returned.attempts, 1)
        self.assertEqual(returned.last_error, "provider down")
        self.assertGreater(returned.next_attempt_at, timezone.now())
        self.assertTrue(returned.can_retry)

    def test_failure_after_the_last_attempt_marks_the_row_failed(self):
        notification = self.queue(send_now=False, max_attempts=1)

        with patch(
            "notifications.services.get_backend",
            side_effect=ServiceUnavailableError("provider down"),
        ):
            services.deliver(notification)

        notification.refresh_from_db()
        self.assertEqual(notification.status, Notification.Status.FAILED)
        self.assertEqual(notification.attempts, 1)
        self.assertFalse(notification.can_retry)

    def test_unexpected_backend_exception_never_propagates(self):
        notification = self.queue(send_now=False)

        with patch(
            "notifications.services.get_backend",
            side_effect=RuntimeError("boom"),
        ):
            returned = services.deliver(notification)

        self.assertEqual(returned.pk, notification.pk)
        returned.refresh_from_db()
        self.assertEqual(returned.status, Notification.Status.PENDING)
        self.assertEqual(returned.attempts, 1)
        self.assertEqual(returned.last_error, "boom")

    def test_non_retryable_rejection_burns_the_attempt_budget(self):
        class RejectingBackend:
            def send(self, notification):
                return DeliveryResult(
                    accepted=False,
                    provider="fake",
                    detail="invalid recipient",
                    retryable=False,
                )

        notification = self.queue(send_now=False, max_attempts=5)

        with patch(
            "notifications.services.get_backend",
            return_value=RejectingBackend(),
        ):
            services.deliver(notification)

        notification.refresh_from_db()
        self.assertEqual(notification.status, Notification.Status.FAILED)
        self.assertEqual(notification.attempts, notification.max_attempts)
        self.assertEqual(notification.last_error, "invalid recipient")
        self.assertFalse(notification.can_retry)

    @override_settings(NOTIFICATION_BACKOFF_BASE_SECONDS=60)
    def test_backoff_is_exponential_and_capped(self):
        self.assertEqual(services.backoff_seconds(1), 60)
        self.assertEqual(services.backoff_seconds(2), 120)
        self.assertEqual(services.backoff_seconds(3), 240)
        self.assertEqual(services.backoff_seconds(10), 3600)

    @override_settings(
        NOTIFICATIONS_MODE="live",
        WHATSAPP_ACCESS_TOKEN="",
        WHATSAPP_PHONE_NUMBER_ID="",
    )
    def test_live_mode_without_credentials_fails_loudly(self):
        notification = self.queue(send_now=True)

        notification.refresh_from_db()
        self.assertEqual(notification.status, Notification.Status.PENDING)
        self.assertEqual(notification.attempts, 1)
        self.assertIn("not configured", notification.last_error)

    def test_manual_retry_requeues_sends_and_is_audited(self):
        notification = self.queue(send_now=False, max_attempts=1)

        with patch(
            "notifications.services.get_backend",
            side_effect=ServiceUnavailableError("provider down"),
        ):
            services.deliver(notification)

        notification.refresh_from_db()
        self.assertEqual(notification.status, Notification.Status.FAILED)

        returned = services.retry_notification(notification)

        returned.refresh_from_db()
        self.assertEqual(returned.status, Notification.Status.SENT)
        self.assertEqual(returned.attempts, 0)
        self.assertEqual(len(self.outbox()), 1)
        self.assertTrue(
            AuditEvent.objects.filter(
                action=AuditEvent.Action.NOTIFICATION_RETRIED,
                entity_id=str(notification.pk),
            ).exists()
        )


@override_settings(NOTIFICATIONS_MODE="mock")
class WorkerCommandTests(NotificationTestBase):
    def test_worker_sends_due_rows(self):
        notification = self.queue(send_now=False)
        Notification.objects.filter(pk=notification.pk).update(
            next_attempt_at=timezone.now() - timedelta(seconds=1)
        )
        stdout = StringIO()

        from django.core.management import call_command

        call_command("process_notifications", stdout=stdout)

        notification.refresh_from_db()
        self.assertEqual(notification.status, Notification.Status.SENT)
        self.assertIn("1 sent", stdout.getvalue())
        self.assertEqual(len(self.outbox()), 1)

    def test_worker_skips_rows_that_are_not_due(self):
        notification = self.queue(send_now=False)
        Notification.objects.filter(pk=notification.pk).update(
            next_attempt_at=timezone.now() + timedelta(hours=1)
        )
        stdout = StringIO()

        from django.core.management import call_command

        call_command("process_notifications", stdout=stdout)

        notification.refresh_from_db()
        self.assertEqual(notification.status, Notification.Status.PENDING)
        self.assertIn("0 sent", stdout.getvalue())
        self.assertEqual(self.outbox(), [])

    def test_worker_respects_the_batch_limit(self):
        first = self.queue(send_now=False, body="first")
        second = self.queue(send_now=False, body="second")
        Notification.objects.filter(pk__in=[first.pk, second.pk]).update(
            next_attempt_at=timezone.now() - timedelta(seconds=1)
        )
        stdout = StringIO()

        from django.core.management import call_command

        call_command("process_notifications", limit=1, stdout=stdout)

        self.assertEqual(
            Notification.objects.filter(status=Notification.Status.SENT).count(), 1
        )
        self.assertEqual(
            Notification.objects.filter(status=Notification.Status.PENDING).count(),
            1,
        )
        self.assertEqual(len(self.outbox()), 1)
        self.assertIn("1 sent", stdout.getvalue())


class NotificationAdminTests(NotificationTestBase):
    def test_delivery_log_cannot_be_added_edited_or_deleted(self):
        from django.contrib.admin.sites import AdminSite
        from django.test import RequestFactory

        request = RequestFactory().get("/admin/")
        request.user = type(
            "Staff", (), {"is_active": True, "is_staff": True, "is_superuser": True}
        )()
        model_admin = NotificationAdmin(Notification, AdminSite())

        self.assertFalse(model_admin.has_add_permission(request))
        self.assertFalse(model_admin.has_delete_permission(request))
        # Every field is read-only, so even staff cannot rewrite delivery evidence.
        for field in ("channel", "recipient", "body_preview", "status", "attempts"):
            self.assertIn(field, model_admin.readonly_fields)
