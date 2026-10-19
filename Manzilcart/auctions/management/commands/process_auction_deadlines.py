"""
Auction deadline worker.

Run every minute from cron / systemd timer / Windows Task Scheduler::

    python manage.py process_auction_deadlines

It performs three idempotent jobs:

1. open scheduled auctions whose start time has arrived;
2. close live auctions whose end time passed without a seller lock (no automatic
   winner is created - the specification requires an explicit seller action);
3. expire overdue bidder confirmations and offer the auction to the next highest
   eligible bidder.

Every state change re-locks the auction/selection row inside
``auctions.services`` and re-checks its status, so two workers can safely process
the same id. Notification rows also carry idempotency keys.
"""

from __future__ import annotations

import logging

from django.core.management.base import BaseCommand
from django.utils import timezone

from auctions import services
from auctions.models import Auction, AuctionSelection

logger = logging.getLogger("manzilcart.auctions.worker")


class Command(BaseCommand):
    help = "Open, close and advance auctions whose deadlines have passed."

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit",
            type=int,
            default=500,
            help="Maximum rows to process per job in this run (default: 500).",
        )

    def handle(self, *args, **options):
        limit = max(1, options["limit"])
        now = timezone.now()

        opened = self._open_scheduled(now, limit)
        closed = self._close_expired(now, limit)
        expired = self._expire_selections(now, limit)

        self.stdout.write(
            self.style.SUCCESS(
                "Auction deadlines processed: "
                f"{opened} opened, {closed} closed unsold, {expired} confirmation(s) expired."
            )
        )

    # -- jobs ---------------------------------------------------------------

    def _open_scheduled(self, now, limit: int) -> int:
        ids = list(
            Auction.objects.filter(
                status=Auction.Status.SCHEDULED, start_at__lte=now
            )
            .order_by("start_at", "id")
            .values_list("pk", flat=True)[:limit]
        )
        count = 0
        for auction_id in ids:
            try:
                auction = services.open_scheduled_auction(auction_id, now=now)
            except Exception:  # noqa: BLE001 - one bad row must not stop the batch
                logger.exception("Could not open auction %s", auction_id)
                continue
            if auction is not None and auction.status == Auction.Status.LIVE:
                count += 1
        return count

    def _close_expired(self, now, limit: int) -> int:
        ids = list(
            Auction.objects.filter(
                status=Auction.Status.LIVE, end_at__lte=now
            )
            .order_by("end_at", "id")
            .values_list("pk", flat=True)[:limit]
        )
        count = 0
        for auction_id in ids:
            try:
                auction = services.close_expired_auction(auction_id, now=now)
            except Exception:  # noqa: BLE001
                logger.exception("Could not close auction %s", auction_id)
                continue
            if auction is not None and auction.status == Auction.Status.UNSOLD:
                count += 1
        return count

    def _expire_selections(self, now, limit: int) -> int:
        ids = list(
            AuctionSelection.objects.filter(
                status=AuctionSelection.Status.PENDING,
                confirmation_deadline__lte=now,
            )
            .order_by("confirmation_deadline", "id")
            .values_list("pk", flat=True)[:limit]
        )
        count = 0
        for selection_id in ids:
            try:
                services.expire_selection(selection_id, now=now)
            except Exception:  # noqa: BLE001
                logger.exception("Could not expire auction selection %s", selection_id)
                continue
            # ``None`` means the auction had no eligible bidder left and is now unsold;
            # a returned selection means the fallback invitation was issued.
            count += 1
        return count
