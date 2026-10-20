"""
Allow conversations about standalone auctions while keeping one thread per
(buyer, item). A conversation is either listing-linked or auction-linked, never both.
"""

from __future__ import annotations

import django.db.models.deletion
from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):
    dependencies = [
        ("auctions", "0002_standalone_auction_content"),
        ("conversations", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="conversation",
            name="auction",
            field=models.ForeignKey(
                blank=True,
                help_text="Set for a thread about a standalone auction.",
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="conversations",
                to="auctions.auction",
            ),
        ),
        migrations.AlterField(
            model_name="conversation",
            name="listing",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="conversations",
                to="catalog.listing",
            ),
        ),
        migrations.RemoveConstraint(
            model_name="conversation",
            name="conversation_unique_listing_buyer",
        ),
        migrations.AddConstraint(
            model_name="conversation",
            constraint=models.UniqueConstraint(
                condition=Q(("listing__isnull", False)),
                fields=("listing", "buyer"),
                name="conversation_unique_listing_buyer",
            ),
        ),
        migrations.AddConstraint(
            model_name="conversation",
            constraint=models.UniqueConstraint(
                condition=Q(("auction__isnull", False)),
                fields=("auction", "buyer"),
                name="conversation_unique_auction_buyer",
            ),
        ),
        migrations.AddConstraint(
            model_name="conversation",
            constraint=models.CheckConstraint(
                condition=(
                    Q(("listing__isnull", False), ("auction__isnull", True))
                    | Q(("listing__isnull", True), ("auction__isnull", False))
                ),
                name="conversation_exactly_one_source",
            ),
        ),
    ]
