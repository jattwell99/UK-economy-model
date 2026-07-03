"""
Rename the seeded housing indicator median-house-price -> average-house-price.

The UK House Price Index publishes a mix-adjusted *average*, not a median, so the
original seed name was wrong for this source (see docs/phase3_breadth_brief.md).
This keeps existing databases (which already have the old indicator with no
observations) aligned with the updated seed, rather than leaving a stale row.
"""

from django.db import migrations

OLD = "median-house-price"
NEW = "average-house-price"
NEW_NAME = "Average house price (UK HPI)"
OLD_NAME = "Median house price"


def forward(apps, schema_editor):
    Indicator = apps.get_model("core", "Indicator")
    PlaceObservation = apps.get_model("core", "PlaceObservation")
    old = Indicator.objects.filter(code=OLD).first()
    if old is None:
        return
    if Indicator.objects.filter(code=NEW).exists():
        # New one already seeded; drop the stale old one if nothing points at it.
        if not PlaceObservation.objects.filter(indicator=old).exists():
            old.delete()
    else:
        old.code = NEW
        old.name = NEW_NAME
        old.save(update_fields=["code", "name"])


def reverse(apps, schema_editor):
    Indicator = apps.get_model("core", "Indicator")
    new = Indicator.objects.filter(code=NEW).first()
    if new and not Indicator.objects.filter(code=OLD).exists():
        new.code = OLD
        new.name = OLD_NAME
        new.save(update_fields=["code", "name"])


class Migration(migrations.Migration):
    dependencies = [("core", "0001_initial")]
    operations = [migrations.RunPython(forward, reverse)]
