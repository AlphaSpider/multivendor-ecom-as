"""
Initial geographic configuration: the two launch emirates, their residential areas and
the optional service options with per-area coverage.

Data migrations (rather than a fixture or a management command) make the seed
reproducible on every environment, including CI. Operations staff extend it from Django
Admin afterwards - no deployment needed.
"""

from django.db import migrations

EMIRATES = [
    {"name": "Abu Dhabi", "slug": "abu-dhabi", "display_order": 1},
    {"name": "Ras Al Khaimah", "slug": "ras-al-khaimah", "display_order": 2},
]

# (name, slug) - real neighbourhoods where the launch team intends to operate.
AREAS = {
    "abu-dhabi": [
        ("Al Reem Island", "al-reem-island"),
        ("Al Maryah Island", "al-maryah-island"),
        ("Saadiyat Island", "saadiyat-island"),
        ("Yas Island", "yas-island"),
        ("Al Raha", "al-raha"),
        ("The Corniche", "the-corniche"),
        ("Al Khalidiyah", "al-khalidiyah"),
        ("Al Zahiyah", "al-zahiyah"),
        ("Al Bateen", "al-bateen"),
        ("Al Mushrif", "al-mushrif"),
        ("Khalifa City", "khalifa-city"),
        ("Mohammed Bin Zayed City", "mohammed-bin-zayed-city"),
        ("Masdar City", "masdar-city"),
        ("Al Shamkha", "al-shamkha"),
        ("Mussafah", "mussafah"),
        ("Al Shahama", "al-shahama"),
    ],
    "ras-al-khaimah": [
        ("Al Hamra Village", "al-hamra-village"),
        ("Al Marjan Island", "al-marjan-island"),
        ("Mina Al Arab", "mina-al-arab"),
        ("Al Nakheel", "al-nakheel"),
        ("Al Qawasim Corniche", "al-qawasim-corniche"),
        ("Al Dhait", "al-dhait"),
        ("Khuzam", "khuzam"),
        ("Al Mamourah", "al-mamourah"),
        ("Dafan Al Nakheel", "dafan-al-nakheel"),
        ("Julfar", "julfar"),
        ("Julphar", "julphar"),
        ("Al Rams", "al-rams"),
        ("Ghalilah", "ghalilah"),
        ("Al Jazeera Al Hamra", "al-jazeera-al-hamra"),
        ("Seih Al Araibi", "seih-al-arabi"),
        ("Sha'am", "shaam"),
    ],
}

SERVICE_OPTIONS = [
    {
        "code": "delivery",
        "name": "Delivery / transportation",
        "description": "Help moving the furniture from the seller to your home.",
        "display_order": 1,
    },
    {
        "code": "assembly",
        "name": "Assembly",
        "description": "Help putting flat-pack or dismantled furniture together.",
        "display_order": 2,
    },
    {
        "code": "disassembly",
        "name": "Disassembly",
        "description": "Help taking furniture apart before it is moved.",
        "display_order": 3,
    },
]

# Delivery is offered everywhere we operate; the hands-on services only in the areas
# where the operations team has confirmed coverage.
HANDS_ON_AREAS = {
    "abu-dhabi": {
        "al-reem-island",
        "al-maryah-island",
        "saadiyat-island",
        "yas-island",
        "al-raha",
        "the-corniche",
        "al-khalidiyah",
        "al-zahiyah",
        "al-bateen",
        "al-mushrif",
        "khalifa-city",
        "mohammed-bin-zayed-city",
        "masdar-city",
    },
    "ras-al-khaimah": {
        "al-hamra-village",
        "al-marjan-island",
        "mina-al-arab",
        "al-nakheel",
        "al-qawasim-corniche",
        "al-dhait",
        "khuzam",
        "al-mamourah",
    },
}


def seed_geography(apps, schema_editor):
    Emirate = apps.get_model("locations", "Emirate")
    ServiceArea = apps.get_model("locations", "ServiceArea")
    ServiceOption = apps.get_model("locations", "ServiceOption")
    ServiceAreaCoverage = apps.get_model("locations", "ServiceAreaCoverage")

    emirate_by_slug = {}
    for data in EMIRATES:
        emirate, _ = Emirate.objects.get_or_create(
            slug=data["slug"],
            defaults={
                "name": data["name"],
                "display_order": data["display_order"],
                "is_active": True,
            },
        )
        emirate_by_slug[data["slug"]] = emirate

    area_by_slug = {}
    for emirate_slug, areas in AREAS.items():
        emirate = emirate_by_slug[emirate_slug]
        for order, (name, slug) in enumerate(areas, start=1):
            area, _ = ServiceArea.objects.get_or_create(
                emirate=emirate,
                slug=slug,
                defaults={"name": name, "display_order": order, "is_active": True},
            )
            area_by_slug[(emirate_slug, slug)] = area

    option_by_code = {}
    for data in SERVICE_OPTIONS:
        option, _ = ServiceOption.objects.get_or_create(
            code=data["code"],
            defaults={
                "name": data["name"],
                "description": data["description"],
                "display_order": data["display_order"],
                "is_active": True,
            },
        )
        option_by_code[data["code"]] = option

    for (emirate_slug, area_slug), area in area_by_slug.items():
        # Delivery: everywhere.
        ServiceAreaCoverage.objects.get_or_create(
            service_option=option_by_code["delivery"],
            service_area=area,
            defaults={"is_available": True},
        )
        # Assembly / disassembly: confirmed areas only. Absence of a row means "not
        # offered" (coverage is opt-in), so no explicit False rows are needed.
        if area_slug in HANDS_ON_AREAS.get(emirate_slug, set()):
            for code in ("assembly", "disassembly"):
                ServiceAreaCoverage.objects.get_or_create(
                    service_option=option_by_code[code],
                    service_area=area,
                    defaults={"is_available": True},
                )


def unseed_geography(apps, schema_editor):
    Emirate = apps.get_model("locations", "Emirate")
    ServiceArea = apps.get_model("locations", "ServiceArea")
    ServiceOption = apps.get_model("locations", "ServiceOption")

    # Protective FKs mean order matters: coverage first, then areas/options/emirates.
    ServiceArea.objects.filter(emirate__slug__in=[e["slug"] for e in EMIRATES]).delete()
    ServiceOption.objects.filter(
        code__in=[option["code"] for option in SERVICE_OPTIONS]
    ).delete()
    Emirate.objects.filter(slug__in=[e["slug"] for e in EMIRATES]).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("locations", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed_geography, unseed_geography),
    ]
