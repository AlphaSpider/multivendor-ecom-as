"""Audit actions for standalone auction moderation."""

from __future__ import annotations

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0001_initial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="auditevent",
            name="action",
            field=models.CharField(
                choices=[
                    ("listing.submitted", "Listing submitted for review"),
                    ("listing.approved", "Listing approved"),
                    ("listing.rejected", "Listing rejected"),
                    ("listing.archived", "Listing archived"),
                    ("listing.sold", "Listing marked sold"),
                    ("emirate.created", "Emirate created"),
                    ("emirate.updated", "Emirate updated"),
                    ("service_area.created", "Service area created"),
                    ("service_area.updated", "Service area updated"),
                    ("account.created", "Account created"),
                    ("account.phone_verified", "Mobile number verified"),
                    ("account.login_succeeded", "Login succeeded"),
                    ("account.login_failed", "Login failed"),
                    ("account.profile_updated", "Profile updated"),
                    ("deal.accepted", "Deal accepted"),
                    ("deal.cancellation_requested", "Cancellation requested"),
                    ("deal.cancellation_approved", "Cancellation approved"),
                    ("deal.cancellation_rejected", "Cancellation rejected"),
                    ("deal.cancellation_escalated", "Cancellation escalated"),
                    ("deal.cancelled", "Deal cancelled"),
                    ("deal.completed", "Deal completed"),
                    ("auction.created", "Auction created"),
                    ("auction.submitted", "Auction submitted for review"),
                    ("auction.approved", "Auction approved"),
                    ("auction.rejected", "Auction rejected"),
                    ("auction.bid_locked", "Bid locked by seller"),
                    ("auction.awarded", "Auction awarded"),
                    ("auction.unsold", "Auction closed unsold"),
                    ("auction.cancelled", "Auction cancelled"),
                    ("notification.failed", "Notification failed"),
                    ("notification.retried", "Notification retried"),
                    ("ops.alert_sent", "Operations alert sent"),
                    ("admin.action", "Administrative action"),
                ],
                db_index=True,
                max_length=64,
            ),
        ),
    ]
