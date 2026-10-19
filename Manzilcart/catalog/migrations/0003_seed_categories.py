"""
Launch furniture categories.

Flat top-level categories only. The model supports a second level (for example
"Sofas" -> "Corner sofas") and operations can add children in Django Admin when the
catalogue justifies it; seeding unnecessary depth now would only complicate navigation.
"""

from django.db import migrations

CATEGORIES = [
    ("Sofas & Seating", "sofas-seating", 1),
    ("Beds & Mattresses", "beds-mattresses", 2),
    ("Tables & Desks", "tables-desks", 3),
    ("Storage & Shelving", "storage-shelving", 4),
    ("Chairs", "chairs", 5),
    ("Outdoor Furniture", "outdoor-furniture", 6),
    ("Home Decor", "home-decor", 7),
    ("Kids & Nursery", "kids-nursery", 8),
    ("Office Furniture", "office-furniture", 9),
    ("Other", "other", 99),
]


def seed_categories(apps, schema_editor):
    Category = apps.get_model("catalog", "Category")
    for name, slug, order in CATEGORIES:
        Category.objects.get_or_create(
            slug=slug,
            defaults={"name": name, "display_order": order, "is_active": True},
        )


def unseed_categories(apps, schema_editor):
    Category = apps.get_model("catalog", "Category")
    Category.objects.filter(slug__in=[slug for _name, slug, _order in CATEGORIES]).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0002_geo_integrity"),
    ]

    operations = [
        migrations.RunPython(seed_categories, unseed_categories),
    ]
