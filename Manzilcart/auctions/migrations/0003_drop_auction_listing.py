"""
Stage 2: archive legacy listings that only existed to back an auction, then drop
``Auction.listing``.

Listings that have any deal are intentionally left untouched: they are real
fixed-price transactions and must not be changed by this migration. Existing deals
are never modified here.

The reverse path re-adds a nullable listing column, copies ``legacy_listing_id`` back
into it and un-archives the affected listings. Auctions created after this migration
have no legacy listing, so a rollback after new standalone auctions exist requires an
operator decision; that limitation is deliberate rather than silently inventing
listings.
"""

from __future__ import annotations

import django.db.models.deletion
from django.db import migrations, models
from django.utils import timezone


def archive_auction_listings(apps, schema_editor):
    Auction = apps.get_model("auctions", "Auction")
    Listing = apps.get_model("catalog", "Listing")
    Deal = apps.get_model("deals", "Deal")

    listing_ids = list(
        Auction.objects.exclude(legacy_listing_id__isnull=True).values_list(
            "legacy_listing_id", flat=True
        )
    )
    if not listing_ids:
        return
    deal_listing_ids = set(
        Deal.objects.filter(listing_id__in=listing_ids).values_list(
            "listing_id", flat=True
        )
    )
    Listing.objects.filter(
        pk__in=listing_ids, status="approved"
    ).exclude(pk__in=deal_listing_ids).update(status="archived", published_at=None)


def restore_auction_listings(apps, schema_editor):
    Auction = apps.get_model("auctions", "Auction")
    Listing = apps.get_model("catalog", "Listing")
    Deal = apps.get_model("deals", "Deal")

    rows = list(
        Auction.objects.exclude(legacy_listing_id__isnull=True).values_list(
            "pk", "legacy_listing_id"
        )
    )
    for auction_id, listing_id in rows:
        Auction.objects.filter(pk=auction_id).update(listing_id=listing_id)

    listing_ids = [listing_id for _, listing_id in rows]
    deal_listing_ids = set(
        Deal.objects.filter(listing_id__in=listing_ids).values_list(
            "listing_id", flat=True
        )
    )
    Listing.objects.filter(pk__in=listing_ids, status="archived").exclude(
        pk__in=deal_listing_ids
    ).update(status="approved", published_at=timezone.now())


class Migration(migrations.Migration):
    dependencies = [
        ("auctions", "0002_standalone_auction_content"),
        ("deals", "0001_initial"),
    ]

    operations = [
        # Make the column nullable so the reverse of RemoveField can add it back
        # without failing on tables that already contain rows.
        migrations.AlterField(
            model_name="auction",
            name="listing",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="auctions",
                to="catalog.listing",
            ),
        ),
        migrations.RunPython(archive_auction_listings, restore_auction_listings),
        migrations.RemoveField(
            model_name="auction",
            name="listing",
        ),
    ]
