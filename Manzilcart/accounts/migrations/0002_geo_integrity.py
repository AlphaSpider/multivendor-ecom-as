"""
Database-level guarantee that a member's (emirate, service_area) pair is consistent.

Django cannot express this constraint (no foreign keys to composite keys), so the
composite FK is added with raw SQL. See ``core/db/geo.py`` for the reasoning. The
constraint overlaps with the CheckConstraint in ``accounts.User`` (both fields set or
both empty); together they make an inconsistent pair impossible.
"""

from django.db import migrations

from core.db.geo import apply_geo_fk, remove_geo_fk

TABLE = "accounts_user"


def forwards(apps, schema_editor):
    apply_geo_fk(schema_editor, TABLE)


def backwards(apps, schema_editor):
    remove_geo_fk(schema_editor, TABLE)


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0001_initial"),
        ("locations", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
