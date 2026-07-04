"""
Split the seeded life-expectancy-birth indicator into sex-specific codes.

OHID Fingertips publishes life expectancy at birth by sex only — there is no
"Persons" figure, and averaging male/female is statistically wrong. So the single
seeded `life-expectancy-birth` becomes `life-expectancy-birth-female` and
`life-expectancy-birth-male`. The old code was only ever a seed row (no
observations), so on existing databases we create the two new indicators and drop
the stale one. `healthy-life-expectancy-birth` is left seeded but unpopulated — it
has no LAD-level data (upper-tier only), a spine gap tracked in CLAUDE.md.
"""

from django.db import migrations

OLD = "life-expectancy-birth"
NEW = [
    ("life-expectancy-birth-female", "Life expectancy at birth (female)"),
    ("life-expectancy-birth-male", "Life expectancy at birth (male)"),
]


def forward(apps, schema_editor):
    Indicator = apps.get_model("core", "Indicator")
    IndicatorDomain = apps.get_model("core", "IndicatorDomain")
    PlaceObservation = apps.get_model("core", "PlaceObservation")

    old = Indicator.objects.filter(code=OLD).first()
    domain = old.domain if old else IndicatorDomain.objects.filter(code="health").first()
    if domain is None:
        return  # dimensions not seeded yet; seed_v1 will create the new codes directly.

    for code, name in NEW:
        Indicator.objects.get_or_create(
            code=code,
            defaults={
                "name": name,
                "domain": domain,
                "unit": "years",
                "value_type": "RATIO",
                "is_additive": False,
                "subject_scope": "PLACE",
            },
        )

    # Drop the stale single code — but only if nothing points at it (it never had data).
    if old is not None and not PlaceObservation.objects.filter(indicator=old).exists():
        old.delete()


def reverse(apps, schema_editor):
    Indicator = apps.get_model("core", "Indicator")
    IndicatorDomain = apps.get_model("core", "IndicatorDomain")
    PlaceObservation = apps.get_model("core", "PlaceObservation")

    domain = IndicatorDomain.objects.filter(code="health").first()
    if domain is not None and not Indicator.objects.filter(code=OLD).exists():
        Indicator.objects.create(
            code=OLD, name="Life expectancy at birth", domain=domain,
            unit="years", value_type="RATIO", is_additive=False, subject_scope="PLACE",
        )
    for code, _ in NEW:
        ind = Indicator.objects.filter(code=code).first()
        if ind and not PlaceObservation.objects.filter(indicator=ind).exists():
            ind.delete()


class Migration(migrations.Migration):
    dependencies = [("core", "0002_rename_house_price_indicator")]
    operations = [migrations.RunPython(forward, reverse)]
