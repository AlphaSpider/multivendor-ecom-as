"""
Retry worker for outbound notifications.

Run every minute from cron / systemd timer / Windows Task Scheduler::

    python manage.py process_notifications

Idempotency and concurrency
---------------------------
Rows are claimed with ``SELECT ... FOR UPDATE SKIP LOCKED``, so running two workers (or
the same worker twice) cannot deliver the same notification in the same pass. Delivery
counts that were already attempted keep their attempt counter, so a crash mid-send
cannot lead to an unbounded retry loop.
"""

from __future__ import annotations

import logging
import time

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from notifications.models import Notification
from notifications.services import deliver

logger = logging.getLogger("manzilcart.notifications.worker")


class Command(BaseCommand):
    help = "Attempt delivery of pending notifications that are due."

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit",
            type=int,
            default=settings.NOTIFICATION_BATCH_SIZE,
            help="Maximum number of notifications to process in this run.",
        )
        parser.add_argument(
            "--once",
            action="store_true",
            help="Process a single batch and exit (default behaviour).",
        )
        parser.add_argument(
            "--max-seconds",
            type=int,
            default=0,
            help="Keep polling for this many seconds (0 = single batch).",
        )
        parser.add_argument(
            "--sleep",
            type=float,
            default=2.0,
            help="Seconds to sleep between polls when --max-seconds is used.",
        )

    def handle(self, *args, **options):
        limit = options["limit"]
        deadline = (
            time.monotonic() + options["max_seconds"]
            if options["max_seconds"]
            else None
        )

        total_sent = total_failed = 0
        while True:
            sent, failed = self._process_batch(limit)
            total_sent += sent
            total_failed += failed

            if deadline is None:
                break
            if time.monotonic() >= deadline:
                break
            if sent == 0 and failed == 0:
                time.sleep(options["sleep"])

        self.stdout.write(
            self.style.SUCCESS(
                f"Notifications processed: {total_sent} sent, {total_failed} still pending/failed."
            )
        )

    def _process_batch(self, limit: int) -> tuple[int, int]:
        sent = failed = 0
        now = timezone.now()

        with transaction.atomic():
            rows = list(
                Notification.objects.due(now=now)
                .select_for_update(skip_locked=True)
                .order_by("next_attempt_at", "id")[:limit]
            )

            for notification in rows:
                # Re-check under the lock: another worker may have completed it.
                if notification.status != Notification.Status.PENDING:
                    continue
                if notification.next_attempt_at > now:
                    continue
                result = deliver(notification)
                if result is None:
                    continue
                if result.status == Notification.Status.SENT:
                    sent += 1
                else:
                    failed += 1

        return sent, failed
