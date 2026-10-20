"""
Make auctions standalone: add their own item content, moderation and photos, and
copy those values from the legacy listing before the foreign key is dropped.

Stage 1 of 2. The old ``Auction.listing`` column is still present and untouched here;
``0003`` archives the now-duplicated listings and drops the column.
"""

from __future__ import annotations

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
from django.db.models import Q

import auctions.models


def copy_listing_content(apps, schema_editor):
    Auction = apps.get_model("auctions", "Auction")
    AuctionImage = apps.get_model("auctions", "AuctionImage")

    queryset = Auction.objects.select_related("listing").filter(listing__isnull=False)
    for auction in queryset.iterator():
        listing = auction.listing
        auction.title = listing.title
        auction.description = listing.description
        auction.category_id = listing.category_id
        auction.condition = listing.condition
        auction.service_area_id = listing.service_area_id
        auction.seller_id = listing.seller_id
        auction.emirate_id = listing.emirate_id
        auction.legacy_listing_id = listing.pk

        if listing.published_at is not None:
            auction.moderation_status = "approved"
            auction.approved_at = listing.approved_at or listing.published_at
            auction.approved_by_id = listing.approved_by_id
            auction.submitted_at = listing.submitted_at
        elif listing.status == "pending":
            auction.moderation_status = "pending"
            auction.submitted_at = listing.submitted_at
        elif listing.status == "rejected":
            auction.moderation_status = "rejected"
            auction.moderation_note = listing.moderation_note
        else:
            auction.moderation_status = "draft"

        auction.save(
            update_fields=[
                "title",
                "description",
                "category",
                "condition",
                "service_area",
                "seller",
                "emirate",
                "legacy_listing_id",
                "moderation_status",
                "approved_at",
                "approved_by",
                "submitted_at",
                "moderation_note",
            ]
        )

        for image in listing.images.all().order_by("display_order", "id"):
            AuctionImage.objects.get_or_create(
                auction=auction,
                image=image.image.name,
                defaults={
                    "caption": image.caption,
                    "is_cover": image.is_cover,
                    "display_order": image.display_order,
                    "width": image.width,
                    "height": image.height,
                    "byte_size": image.byte_size,
                },
            )


def restore_from_auction_images(apps, schema_editor):
    """Reverse helper: delete auction photos that were copied from listings."""
    AuctionImage = apps.get_model("auctions", "AuctionImage")
    AuctionImage.objects.filter(auction__legacy_listing_id__isnull=False).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("auctions", "0001_initial"),
        ("catalog", "0004_remove_listing_listing_published_at_matches_status_and_more"),
        ("locations", "0002_seed_geography"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # -- new content fields, nullable so existing rows can be copied into them ----
        migrations.AddField(
            model_name="auction",
            name="title",
            field=models.CharField(max_length=140, null=True),
        ),
        migrations.AddField(
            model_name="auction",
            name="description",
            field=models.TextField(max_length=4000, null=True),
        ),
        migrations.AddField(
            model_name="auction",
            name="category",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="auctions",
                to="catalog.category",
            ),
        ),
        migrations.AddField(
            model_name="auction",
            name="condition",
            field=models.CharField(
                choices=[
                    ("new", "New"),
                    ("like_new", "Like new"),
                    ("good", "Good"),
                    ("fair", "Fair"),
                ],
                db_index=True,
                max_length=16,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="auction",
            name="service_area",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="auctions",
                to="locations.servicearea",
            ),
        ),
        # -- moderation (separate axis from lifecycle status) -----------------------
        migrations.AddField(
            model_name="auction",
            name="moderation_status",
            field=models.CharField(
                choices=[
                    ("draft", "Draft"),
                    ("pending", "Pending review"),
                    ("approved", "Approved / public"),
                    ("rejected", "Rejected"),
                ],
                db_index=True,
                default="draft",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="auction",
            name="moderation_note",
            field=models.TextField(
                blank=True,
                default="",
                help_text=(
                    "Reason for the last moderation decision (shown to the seller)."
                ),
            ),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="auction",
            name="submitted_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="auction",
            name="approved_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="auction",
            name="approved_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="approved_auctions",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="auction",
            name="legacy_listing_id",
            field=models.PositiveBigIntegerField(
                blank=True, editable=False, null=True
            ),
        ),
        # -- standalone photos ------------------------------------------------------
        migrations.CreateModel(
            name="AuctionImage",
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
                    "image",
                    models.ImageField(
                        max_length=255, upload_to=auctions.models.auction_image_upload_to
                    ),
                ),
                ("caption", models.CharField(blank=True, max_length=140)),
                ("is_cover", models.BooleanField(default=False)),
                ("display_order", models.PositiveSmallIntegerField(default=0)),
                (
                    "width",
                    models.PositiveIntegerField(default=0, editable=False),
                ),
                (
                    "height",
                    models.PositiveIntegerField(default=0, editable=False),
                ),
                (
                    "byte_size",
                    models.PositiveIntegerField(default=0, editable=False),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "auction",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="images",
                        to="auctions.auction",
                    ),
                ),
            ],
            options={
                "verbose_name": "auction image",
                "verbose_name_plural": "auction images",
                "ordering": ["display_order", "id"],
            },
        ),
        migrations.AddConstraint(
            model_name="auctionimage",
            constraint=models.UniqueConstraint(
                condition=Q(("is_cover", True)),
                fields=("auction",),
                name="auctionimage_one_cover_per_auction",
            ),
        ),
        # -- copy legacy data before enforcing NOT NULL -----------------------------
        migrations.RunPython(copy_listing_content, restore_from_auction_images),
        migrations.AlterField(
            model_name="auction",
            name="title",
            field=models.CharField(max_length=140),
        ),
        migrations.AlterField(
            model_name="auction",
            name="description",
            field=models.TextField(max_length=4000),
        ),
        migrations.AlterField(
            model_name="auction",
            name="category",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="auctions",
                to="catalog.category",
            ),
        ),
        migrations.AlterField(
            model_name="auction",
            name="condition",
            field=models.CharField(
                choices=[
                    ("new", "New"),
                    ("like_new", "Like new"),
                    ("good", "Good"),
                    ("fair", "Fair"),
                ],
                db_index=True,
                max_length=16,
            ),
        ),
        migrations.AlterField(
            model_name="auction",
            name="service_area",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="auctions",
                to="locations.servicearea",
            ),
        ),
        # -- remove listing-scoped uniqueness, add moderation index -----------------
        migrations.RemoveConstraint(
            model_name="auction",
            name="auction_one_active_per_listing",
        ),
        migrations.AddIndex(
            model_name="auction",
            index=models.Index(
                fields=["moderation_status", "status", "start_at"],
                name="auction_mod_status_idx",
            ),
        ),
        # -- new event types --------------------------------------------------------
        migrations.AlterField(
            model_name="auctionevent",
            name="event_type",
            field=models.CharField(
                choices=[
                    ("created", "Auction created"),
                    ("submitted", "Submitted for review"),
                    ("approved", "Auction approved"),
                    ("rejected", "Auction rejected"),
                    ("started", "Auction opened for bids"),
                    ("bid_placed", "Bid placed"),
                    ("bid_locked", "Bid locked"),
                    ("selection_offered", "Confirmation requested"),
                    ("selection_confirmed", "Bidder confirmed"),
                    ("selection_declined", "Bidder declined"),
                    ("selection_timed_out", "Bidder timed out"),
                    ("awarded", "Auction awarded"),
                    ("unsold", "Auction unsold"),
                    ("cancelled", "Auction cancelled"),
                ],
                db_index=True,
                max_length=32,
            ),
        ),
    ]
