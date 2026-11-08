"""Composite geographic FK for auction applications.

A member application stores both ``emirate`` and ``service_area``. PostgreSQL is
the only place that can enforce the pair; see ``core.db.geo``.
"""

from django.db import migrations

from core.db.geo import apply_geo_fk, remove_geo_fk

TABLE = "auctions_auctionapplication"


def forwards(apps, schema_editor):
    apply_geo_fk(schema_editor, TABLE)


def backwards(apps, schema_editor):
    remove_geo_fk(schema_editor, TABLE)


class Migration(migrations.Migration):
    dependencies = [
        ("auctions", "0007_auctionapplication_auctionapplicationimage_and_more"),
        ("locations", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
