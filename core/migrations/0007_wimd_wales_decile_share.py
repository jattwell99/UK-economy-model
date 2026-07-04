"""
Deprivation (Wales) indicator for the Welsh Index of Multiple Deprivation.

WIMD publishes scores as well as ranks, but WG's guidance states averaging the scores
across areas is invalid (they are exponentially-transformed construction artifacts), so
only a decile-share metric is modelled — no score. Deprivation is per-nation and never
merged UK-wide, so this is its own Wales-scoped code alongside the England/Scotland/NI
codes. Adds the indicator on existing databases; seed_v1 creates it on fresh ones.

This completes four-nations deprivation (IMD / SIMD / NIMDM / WIMD).
"""

from django.db import migrations

CODE = "wimd-most-deprived-decile-share-wales"
NAME = "WIMD: share of LSOAs in most-deprived national decile (Wales)"


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
    dependencies = [("core", "0006_nimdm_northern_ireland_decile_share")]
    operations = [migrations.RunPython(forward, reverse)]
