"""
Read-only planner for the listing/auction decoupling migration.

Run before applying the auctions/deals/conversations migrations::

    python manage.py audit_auction_decoupling
    python manage.py audit_auction_decoupling --json

The command never writes to the database. It introspects the live schema instead of
the current models, so it works both *before* the data-copy migration (when
``Auction.listing`` still exists and the new content columns do not) and *after* it.
It reports which legacy auction-linked listings will be archived and which will be
left untouched because a deal already exists.
"""

from __future__ import annotations

import json

from django.core.management.base import BaseCommand
from django.db import connection

from auctions.models import Auction, AuctionImage
from catalog.models import Listing, ListingImage
from deals.models import Deal


class Command(BaseCommand):
    help = "Dry-run report for the auction/listing decoupling (read-only)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--json",
            action="store_true",
            help="Emit machine-readable JSON instead of a text report.",
        )

    def handle(self, *args, **options):
        report = self._build_report()
        if options["json"]:
            self.stdout.write(json.dumps(report, indent=2, default=str))
            return
        self._write_text(report)

    # -- schema helpers ----------------------------------------------------
    @staticmethod
    def _columns(table: str) -> set[str]:
        with connection.cursor() as cursor:
            description = connection.introspection.get_table_description(cursor, table)
        return {column.name for column in description}

    @staticmethod
    def _table_exists(table: str) -> bool:
        return table in connection.introspection.table_names()

    def _scalar(self, sql: str, params=None) -> int:
        with connection.cursor() as cursor:
            cursor.execute(sql, params or [])
            row = cursor.fetchone()
        return int(row[0]) if row and row[0] is not None else 0

    # -- report ------------------------------------------------------------
    def _build_report(self) -> dict:
        q = connection.ops.quote_name
        auction_table = Auction._meta.db_table
        listing_table = Listing._meta.db_table
        deal_table = Deal._meta.db_table
        image_table = AuctionImage._meta.db_table

        auction_columns = self._columns(auction_table)
        deal_columns = self._columns(deal_table)
        has_listing_fk = "listing_id" in auction_columns
        has_legacy_id = "legacy_listing_id" in auction_columns
        has_new_content = "title" in auction_columns
        has_moderation = "moderation_status" in auction_columns
        has_auction_images = self._table_exists(image_table)

        legacy_ids: set[int] = set()
        if has_listing_fk:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"SELECT listing_id FROM {q(auction_table)} "
                    "WHERE listing_id IS NOT NULL"
                )
                legacy_ids.update(row[0] for row in cursor.fetchall())
        if has_legacy_id:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"SELECT legacy_listing_id FROM {q(auction_table)} "
                    "WHERE legacy_listing_id IS NOT NULL"
                )
                legacy_ids.update(row[0] for row in cursor.fetchall())

        listing_ids = sorted(legacy_ids)
        deal_listing_ids = set(
            Deal.objects.filter(listing_id__in=listing_ids).values_list(
                "listing_id", flat=True
            )
        )
        listings = Listing.objects.filter(pk__in=listing_ids)
        archive = list(
            listings.filter(status=Listing.Status.APPROVED)
            .exclude(pk__in=deal_listing_ids)
            .values_list("pk", "slug", "title")
        )
        protected = list(
            listings.filter(pk__in=deal_listing_ids).values_list("pk", "slug", "title")
        )

        total_auctions = self._scalar(f"SELECT COUNT(*) FROM {q(auction_table)}")
        if has_moderation:
            approved = self._scalar(
                f"SELECT COUNT(*) FROM {q(auction_table)} "
                "WHERE moderation_status = 'approved'"
            )
            pending = self._scalar(
                f"SELECT COUNT(*) FROM {q(auction_table)} "
                "WHERE moderation_status = 'pending'"
            )
        elif has_listing_fk:
            approved = self._scalar(
                f"SELECT COUNT(*) FROM {q(auction_table)} a "
                f"JOIN {q(listing_table)} l ON l.id = a.listing_id "
                "WHERE l.published_at IS NOT NULL"
            )
            pending = self._scalar(
                f"SELECT COUNT(*) FROM {q(auction_table)} a "
                f"JOIN {q(listing_table)} l ON l.id = a.listing_id "
                "WHERE l.status = 'pending'"
            )
        else:
            approved = pending = 0

        if has_new_content:
            missing = {
                "missing_title": self._scalar(
                    f"SELECT COUNT(*) FROM {q(auction_table)} WHERE title = ''"
                ),
                "missing_description": self._scalar(
                    f"SELECT COUNT(*) FROM {q(auction_table)} WHERE description = ''"
                ),
                "missing_category": self._scalar(
                    f"SELECT COUNT(*) FROM {q(auction_table)} WHERE category_id IS NULL"
                ),
                "missing_area": self._scalar(
                    f"SELECT COUNT(*) FROM {q(auction_table)} "
                    "WHERE service_area_id IS NULL"
                ),
                "missing_images": (
                    self._scalar(
                        f"SELECT COUNT(*) FROM {q(auction_table)} a "
                        f"LEFT JOIN {q(image_table)} i ON i.auction_id = a.id "
                        "WHERE i.id IS NULL"
                    )
                    if has_auction_images
                    else total_auctions
                ),
            }
        elif has_listing_fk:
            # Before the copy, missing content means the listing side is incomplete.
            missing = {
                "missing_title": self._scalar(
                    f"SELECT COUNT(*) FROM {q(auction_table)} a "
                    f"JOIN {q(listing_table)} l ON l.id = a.listing_id "
                    "WHERE l.title = ''"
                ),
                "missing_description": self._scalar(
                    f"SELECT COUNT(*) FROM {q(auction_table)} a "
                    f"JOIN {q(listing_table)} l ON l.id = a.listing_id "
                    "WHERE l.description = ''"
                ),
                "missing_category": 0,
                "missing_area": 0,
                "missing_images": self._scalar(
                    f"SELECT COUNT(*) FROM {q(auction_table)} a "
                    f"JOIN {q(listing_table)} l ON l.id = a.listing_id "
                    f"LEFT JOIN {q(ListingImage._meta.db_table)} i "
                    "ON i.listing_id = l.id "
                    "WHERE i.id IS NULL"
                ),
            }
        else:
            missing = {
                "missing_title": 0,
                "missing_description": 0,
                "missing_category": 0,
                "missing_area": 0,
                "missing_images": 0,
            }

        with_awarded_deal = self._scalar(
            f"SELECT COUNT(*) FROM {q(auction_table)} WHERE awarded_deal_id IS NOT NULL"
        )
        auction_linked_deals = (
            self._scalar(
                f"SELECT COUNT(*) FROM {q(deal_table)} WHERE auction_id IS NOT NULL"
            )
            if "auction_id" in deal_columns
            else 0
        )

        return {
            "schema": {
                "legacy_listing_column_present": has_listing_fk,
                "new_content_columns_present": has_new_content,
                "legacy_listing_id_present": has_legacy_id,
                "auction_table": auction_table,
            },
            "auctions": {
                "total": total_auctions,
                "linked_to_listing": len(legacy_ids),
                "approved": approved,
                "pending": pending,
                "with_awarded_deal": with_awarded_deal,
                **missing,
            },
            "listings": {
                "legacy_linked_total": len(listing_ids),
                "will_be_archived": [
                    {"id": pk, "slug": slug, "title": title}
                    for pk, slug, title in archive
                ],
                "left_untouched_has_deal": [
                    {"id": pk, "slug": slug, "title": title}
                    for pk, slug, title in protected
                ],
            },
            "deals": {
                "total": Deal.objects.count(),
                "listing_linked": Deal.objects.filter(listing__isnull=False).count(),
                "auction_linked": auction_linked_deals,
            },
        }

    def _write_text(self, report: dict) -> None:
        schema = report["schema"]
        auctions = report["auctions"]
        listings = report["listings"]
        deals = report["deals"]

        style = self.style
        self.stdout.write(
            style.MIGRATE_HEADING("Auction decoupling dry run (read-only)")
        )
        self.stdout.write(
            "Legacy Auction.listing column present: "
            f"{schema['legacy_listing_column_present']}"
        )
        self.stdout.write(
            f"Standalone content columns present:  {schema['new_content_columns_present']}"
        )
        self.stdout.write("")
        self.stdout.write(style.MIGRATE_HEADING("Auctions"))
        self.stdout.write(f"  total:                 {auctions['total']}")
        self.stdout.write(f"  linked to a listing:   {auctions['linked_to_listing']}")
        self.stdout.write(f"  approved:              {auctions['approved']}")
        self.stdout.write(f"  pending review:        {auctions['pending']}")
        self.stdout.write(f"  with awarded deal:     {auctions['with_awarded_deal']}")
        self.stdout.write(
            "  missing content:       "
            f"title={auctions['missing_title']}, "
            f"description={auctions['missing_description']}, "
            f"category={auctions['missing_category']}, "
            f"area={auctions['missing_area']}, "
            f"images={auctions['missing_images']}"
        )
        self.stdout.write("")
        self.stdout.write(style.MIGRATE_HEADING("Listings"))
        self.stdout.write(
            f"  auction-linked total:  {listings['legacy_linked_total']}"
        )
        self.stdout.write(
            f"  will be archived:      {len(listings['will_be_archived'])}"
        )
        for row in listings["will_be_archived"]:
            self.stdout.write(f"    #{row['id']} {row['slug']} — {row['title']}")
        self.stdout.write(
            f"  left untouched (deal): {len(listings['left_untouched_has_deal'])}"
        )
        for row in listings["left_untouched_has_deal"]:
            self.stdout.write(f"    #{row['id']} {row['slug']} — {row['title']}")
        self.stdout.write("")
        self.stdout.write(style.MIGRATE_HEADING("Deals"))
        self.stdout.write(f"  total:                 {deals['total']}")
        self.stdout.write(f"  listing-linked:        {deals['listing_linked']}")
        self.stdout.write(f"  auction-linked:        {deals['auction_linked']}")
        self.stdout.write("")
        self.stdout.write(style.SUCCESS("Dry run complete; nothing was modified."))
