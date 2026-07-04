"""
Deprivation (Scotland) indicator for the Scottish Index of Multiple Deprivation.

SIMD is rank-based with no published composite score, so only a decile-share metric
is modelled (never a synthesised score). Deprivation is per-nation and never merged
UK-wide, so this is its own Scotland-scoped code alongside the England IoD codes.
Adds the indicator on existing databases; seed_v1 creates it directly on fresh ones.
"""

from django.db import migrations

CODE = "simd-most-deprived-decile-share-scotland"
NAME = "SIMD: share of Data Zones in most-deprived national decile (Scotland)"


def forward(apps, schema_editor):
    Indicator = apps.get_model("core", "Indicator")
    IndicatorDomain = apps.get_model("core", "IndicatorDomain")

    domain = IndicatorDomain.objects.filter(code="community").first()
    if domain is None:
        return  # dimensions not seeded yet; seed_v1 will create the code directly.

    Indicator.objects.get_or_create(
        code=CODE,
        defaults={"name": NAME, "domain": domain, "unit": "%",
                  "value_type": "RATE", "is_additive": False, "subject_scope": "PLACE"},
    )


def reverse(apps, schema_editor):
    Indicator = apps.get_model("core", "Indicator")
    PlaceObservation = apps.get_model("core", "PlaceObservation")
    ind = Indicator.objects.filter(code=CODE).first()
    if ind and not PlaceObservation.objects.filter(indicator=ind).exists():
        ind.delete()


class Migration(migrations.Migration):
    dependencies = [("core", "0004_imd_england_metrics")]
    operations = [migrations.RunPython(forward, reverse)]
