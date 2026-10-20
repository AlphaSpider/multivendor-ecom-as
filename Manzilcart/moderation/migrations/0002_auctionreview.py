"""Append-only auction moderation decisions, mirroring ``ListingReview``."""

from __future__ import annotations

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("auctions", "0003_drop_auction_listing"),
        ("moderation", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="AuctionReview",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "decision",
                    models.CharField(
                        choices=[("approve", "Approved"), ("reject", "Rejected")],
                        max_length=16,
                    ),
                ),
                (
                    "reason",
                    models.TextField(
                        blank=True,
                        help_text=(
                            "Shown to the seller when an auction is rejected. "
                            "Required for rejections."
                        ),
                    ),
                ),
                ("previous_status", models.CharField(blank=True, max_length=16)),
                ("new_status", models.CharField(blank=True, max_length=16)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                (
                    "auction",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="reviews",
                        to="auctions.auction",
                    ),
                ),
                (
                    "reviewer",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="auction_reviews",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "auction review",
                "verbose_name_plural": "auction reviews",
                "ordering": ["-created_at", "-id"],
                "indexes": [
                    models.Index(
                        fields=["auction", "-created_at"],
                        name="auctionreview_auction_idx",
                    )
                ],
            },
        ),
    ]
