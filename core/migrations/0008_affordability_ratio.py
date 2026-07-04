"""
Affordability indicator: ONS house-price-to-residence-based-earnings ratio.

ONS publishes this median-over-median ratio for England & Wales only (Scotland and NI
have their own separate sources), so it is one E&W-scoped indicator — comparable across
E&W places, not a per-nation split. Adds the indicator on existing databases; seed_v1
creates it directly on fresh ones.
"""

from django.db import migrations

CODE = "house-price-to-earnings-ratio-residence"
NAME = "House price to residence-based earnings ratio"


def forward(apps, schema_editor):
    Indicator = apps.get_model("core", "Indicator")
    IndicatorDomain = apps.get_model("core", "IndicatorDomain")

    domain = IndicatorDomain.objects.filter(code="housing").first()
    if domain is None:
        return  # dimensions not seeded yet; seed_v1 will create the code directly.

    Indicator.objects.get_or_create(
        code=CODE,
        defaults={"name": NAME, "domain": domain, "unit": "ratio",
                  "value_type": "RATIO", "is_additive": False, "subject_scope": "PLACE"},
    )


def reverse(apps, schema_editor):
    Indicator = apps.get_model("core", "Indicator")
    PlaceObservation = apps.get_model("core", "PlaceObservation")
    ind = Indicator.objects.filter(code=CODE).first()
    if ind and not PlaceObservation.objects.filter(indicator=ind).exists():
        ind.delete()


class Migration(migrations.Migration):
    dependencies = [("core", "0007_wimd_wales_decile_share")]
    operations = [migrations.RunPython(forward, reverse)]
