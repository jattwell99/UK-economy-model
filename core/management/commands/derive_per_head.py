"""
Derive gva-per-head from stored GVA total and population.

Place at: core/management/commands/derive_per_head.py

per-head (£) = GVA total (£m) * 1,000,000 / population, matched on place + year.

This is a DERIVED indicator, not a source ingest. Both inputs are at LAD level
already, so it's a clean same-geography division (no apportionment). The result
is stored as PlaceObservation rows against a dedicated "Derived" source, with a
vintage string that names both input editions — so provenance stays traceable
even though the value is computed.

    python manage.py derive_per_head
    python manage.py derive_per_head --gva-vintage 2019-12-19 --pop-vintage 2020-06-mye

If a place-year has multiple vintages of an input, the latest (max vintage
string) is used unless pinned with the flags above.

Design note: the alternative is to compute per-head at query time and never
store it. Materialising it here makes it visible and trendable in Django admin,
which suits the "explore the data" V1 -- at the cost of the dual-source vintage.
"""

from collections import defaultdict
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.models import Indicator, PeriodType, PlaceObservation, Source

GVA_CODE = "gva-balanced-total"          # £m, current basic prices
POP_CODE = "population"                  # count
PERHEAD_CODE = "gva-per-head"            # £
DERIVED_SOURCE = "Derived — Currence engine"


class Command(BaseCommand):
    help = "Derive gva-per-head PlaceObservations from stored GVA total and population."

    def add_arguments(self, parser):
        parser.add_argument("--gva-vintage", default=None,
                            help="Pin the GVA input vintage (default: latest per place-year).")
        parser.add_argument("--pop-vintage", default=None,
                            help="Pin the population input vintage (default: latest per place-year).")
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **opts):
        try:
            gva_ind = Indicator.objects.get(code=GVA_CODE)
            pop_ind = Indicator.objects.get(code=POP_CODE)
            ph_ind = Indicator.objects.get(code=PERHEAD_CODE)
        except Indicator.DoesNotExist as e:
            raise CommandError(f"Missing indicator ({e}). Run the GVA and population "
                               f"ingests (and seed_v1) first.")

        gva = self._index(gva_ind, opts["gva_vintage"])
        pop = self._index(pop_ind, opts["pop_vintage"])

        keys = set(gva) & set(pop)
        self.stdout.write(f"GVA place-years: {len(gva)}, population place-years: {len(pop)}, "
                          f"overlap: {len(keys)}")

        if opts["dry_run"]:
            self.stdout.write(self.style.SUCCESS("Dry run — nothing written."))
            return

        source, _ = Source.objects.get_or_create(
            name=DERIVED_SOURCE, publisher="Currence engine",
        )
        written = updated = skipped = 0

        with transaction.atomic():
            for (place_id, year) in keys:
                gva_m, gva_v = gva[(place_id, year)]
                pop_n, pop_v = pop[(place_id, year)]
                if not pop_n:
                    skipped += 1
                    continue
                per_head = (gva_m * Decimal(1_000_000) / pop_n).quantize(
                    Decimal("1"), rounding=ROUND_HALF_UP)
                _, created = PlaceObservation.objects.update_or_create(
                    indicator=ph_ind, place_id=place_id,
                    period_start=date(year, 1, 1), period_end=date(year, 12, 31),
                    source=source, vintage=f"gva:{gva_v}/pop:{pop_v}",
                    defaults={"value": per_head, "period_type": PeriodType.CALENDAR_YEAR,
                              "unit": "£"},
                )
                written += int(created)
                updated += int(not created)

        self.stdout.write(self.style.SUCCESS(
            f"Per-head: {written} created, {updated} updated, {skipped} skipped (zero pop)."
        ))

    @staticmethod
    def _index(indicator, pinned_vintage):
        """Return {(place_id, year): (Decimal value, vintage)} taking the latest
        vintage per place-year unless pinned."""
        qs = PlaceObservation.objects.filter(indicator=indicator)
        if pinned_vintage:
            qs = qs.filter(vintage=pinned_vintage)
        best = {}
        for place_id, ps, value, vintage in qs.values_list(
                "place_id", "period_start", "value", "vintage"):
            key = (place_id, ps.year)
            if key not in best or vintage > best[key][1]:
                best[key] = (value, vintage)
        return best
