"""
Deprivation (England) indicators for the English IoD.

The seeded `imd-most-deprived-decile-share` becomes the England-specific
`imd-most-deprived-decile-share-england` (deprivation is per-nation, never merged),
and a second cardinal metric `imd-average-score-england` (population-weighted mean of
LSOA IMD scores) is added alongside it. The old code was seed-only (no observations),
so on existing databases it is renamed and the score indicator created.
"""

from django.db import migrations

OLD = "imd-most-deprived-decile-share"
SHARE = "imd-most-deprived-decile-share-england"
SHARE_NAME = "IMD: share of LSOAs in most-deprived national decile (England)"
SCORE = "imd-average-score-england"
SCORE_NAME = "IMD: population-weighted average score (England)"


def forward(apps, schema_editor):
    Indicator = apps.get_model("core", "Indicator")
    IndicatorDomain = apps.get_model("core", "IndicatorDomain")
    PlaceObservation = apps.get_model("core", "PlaceObservation")

    old = Indicator.objects.filter(code=OLD).first()
    domain = old.domain if old else IndicatorDomain.objects.filter(code="community").first()
    if domain is None:
        return  # dimensions not seeded yet; seed_v1 will create both codes directly.

    # Rename the decile-share code (reuse the row if it has no observations).
    if old is not None and not Indicator.objects.filter(code=SHARE).exists():
        if not PlaceObservation.objects.filter(indicator=old).exists():
            old.code = SHARE
            old.name = SHARE_NAME
            old.save(update_fields=["code", "name"])
    else:
        Indicator.objects.get_or_create(
            code=SHARE,
            defaults={"name": SHARE_NAME, "domain": domain, "unit": "%",
                      "value_type": "RATE", "is_additive": False, "subject_scope": "PLACE"},
        )

    Indicator.objects.get_or_create(
        code=SCORE,
        defaults={"name": SCORE_NAME, "domain": domain, "unit": "score",
                  "value_type": "INDEX", "is_additive": False, "subject_scope": "PLACE"},
    )


def reverse(apps, schema_editor):
    Indicator = apps.get_model("core", "Indicator")
    PlaceObservation = apps.get_model("core", "PlaceObservation")

    share = Indicator.objects.filter(code=SHARE).first()
    if share and not Indicator.objects.filter(code=OLD).exists():
        if not PlaceObservation.objects.filter(indicator=share).exists():
            share.code = OLD
            share.name = "Share of LSOAs in most-deprived decile"
            share.save(update_fields=["code", "name"])
    score = Indicator.objects.filter(code=SCORE).first()
    if score and not PlaceObservation.objects.filter(indicator=score).exists():
        score.delete()


class Migration(migrations.Migration):
    dependencies = [("core", "0003_split_life_expectancy_by_sex")]
    operations = [migrations.RunPython(forward, reverse)]
