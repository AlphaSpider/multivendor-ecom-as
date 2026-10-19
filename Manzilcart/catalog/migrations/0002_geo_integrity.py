"""
Database-level guarantee that a listing's (emirate, service_area) pair is consistent.

This is the constraint that stops a crafted POST from publishing a listing in Abu Dhabi
while tagging it with a Ras Al Khaimah neighbourhood.
"""

from django.db import migrations

from core.db.geo import apply_geo_fk, remove_geo_fk

TABLE = "catalog_listing"


def forwards(apps, schema_editor):
    apply_geo_fk(schema_editor, TABLE)


def backwards(apps, schema_editor):
    remove_geo_fk(schema_editor, TABLE)


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0001_initial"),
        ("locations", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
