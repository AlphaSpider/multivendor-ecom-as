"""
Decouple deals from the catalog: a deal now points at exactly one source item,
either a fixed-price listing or a standalone auction.

Legacy dealer rows stay listing-linked and are not modified. New constraints make the
XOR and the one-active-deal-per-item guarantees explicit for both source types.
"""

from __future__ import annotations

import django.db.models.deletion
from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):
    dependencies = [
        ("auctions", "0002_standalone_auction_content"),
        ("deals", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="deal",
            name="auction",
            field=models.ForeignKey(
                blank=True,
                help_text="Set when the deal was created from an auction award.",
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="deals",
                to="auctions.auction",
            ),
        ),
        migrations.AlterField(
            model_name="deal",
            name="listing",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="deals",
                to="catalog.listing",
            ),
        ),
        migrations.RemoveConstraint(
            model_name="deal",
            name="deal_one_active_per_listing",
        ),
        migrations.RemoveConstraint(
            model_name="deal",
            name="deal_one_open_per_buyer_listing",
        ),
        migrations.AddConstraint(
            model_name="deal",
            constraint=models.CheckConstraint(
                condition=(
                    Q(("listing__isnull", False), ("auction__isnull", True))
                    | Q(("listing__isnull", True), ("auction__isnull", False))
                ),
                name="deal_exactly_one_source",
            ),
        ),
        migrations.AddConstraint(
            model_name="deal",
            constraint=models.UniqueConstraint(
                condition=Q(
                    ("listing__isnull", False),
                    ("status__in", ["accepted", "cancellation_requested"]),
                ),
                fields=("listing",),
                name="deal_one_active_per_listing",
            ),
        ),
        migrations.AddConstraint(
            model_name="deal",
            constraint=models.UniqueConstraint(
                condition=Q(
                    ("listing__isnull", False), ("status", "negotiating")
                ),
                fields=("listing", "buyer"),
                name="deal_one_open_per_buyer_listing",
            ),
        ),
        migrations.AddConstraint(
            model_name="deal",
            constraint=models.UniqueConstraint(
                condition=Q(
                    ("auction__isnull", False),
                    ("status__in", ["accepted", "cancellation_requested"]),
                ),
                fields=("auction",),
                name="deal_one_active_per_auction",
            ),
        ),
        migrations.AddIndex(
            model_name="deal",
            index=models.Index(
                fields=["auction", "status"], name="deal_auction_status_idx"
            ),
        ),
    ]
