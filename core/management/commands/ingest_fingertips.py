"""
Ingest life expectancy at birth (England, LAD tier) from the OHID Fingertips API.

Fingertips (fingertips.phe.org.uk) is reachable from the deploy, so this fetches
live — no bundled file needed. England only: these are English observations of a
UK-wide-methodology indicator, so they slot onto the existing indicator codes, and
other nations join later from their own bodies (NRS / NISRA / PHW).

    python manage.py ingest_fingertips
    python manage.py ingest_fingertips --dry-run

Key shape decisions (from the discovery spike — see CLAUDE.md):
- Life expectancy at birth (Fingertips indicator 90366) is published BY SEX only —
  there is no "Persons" figure. So Male -> life-expectancy-birth-male and Female ->
  life-expectancy-birth-female (never a fabricated average).
- Period: the 3-year POOLED series ("2001 - 03"), not the volatile single years.
  Stored period_start = first year, period_end = last year (CALENDAR_YEAR span).
- Area type 301 ("Districts & UAs 2020/21") is the closest LAD set to our Dec-2019
  spine; the ~4 old Buckinghamshire districts are gone (Bucks is a 2020 unitary) and
  are reported as unmatched, exactly like the post-2019 unitaries in ingest_nomis.
- Category Type is filtered to the headline value (blank) — Fingertips also carries
  a within-area LSOA-deprivation-decile breakdown we do not want here.

Healthy life expectancy at birth (90362) is deliberately NOT ingested: Fingertips
publishes it at upper-tier (County/UTLA) only, and we do not model that geography
yet. It stays seeded but unpopulated.
"""

import csv
import io
import urllib.parse
import urllib.request
from datetime import date
from decimal import Decimal, InvalidOperation

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.models import (
    Indicator,
    ObservationStatus,
    PeriodType,
    Place,
    PlaceObservation,
    PlaceTier,
    Source,
)

CSV_URL = "https://fingertips.phe.org.uk/api/all_data/csv/by_indicator_id"
LE_INDICATOR_ID = 90366        # Life expectancy at birth
LAD_AREA_TYPE = 301            # Districts & UAs (2020/21)
ENGLAND_AREA_TYPE = 15
ENGLAND_CODE = "E92000001"
SOURCE_NAME = "OHID Fingertips"
SOURCE_PUBLISHER = "Office for Health Improvement and Disparities"

# Fingertips "Sex" value -> our seeded indicator code.
SEX_TO_CODE = {
    "Male": "life-expectancy-birth-male",
    "Female": "life-expectancy-birth-female",
}


def _parse_pooled_period(text):
    """'2001 - 03' -> (date(2001,1,1), date(2003,12,31)); None for single years."""
    if "-" not in text:
        return None
    left, right = (p.strip() for p in text.split("-", 1))
    if not (left.isdigit() and right.isdigit()):
        return None
    start = int(left)
    end = int(right) if len(right) == 4 else start - (start % 100) + int(right)
    return date(start, 1, 1), date(end, 12, 31)


class Command(BaseCommand):
    help = "Ingest life expectancy at birth (England, LAD) from OHID Fingertips."

    def add_arguments(self, parser):
        parser.add_argument("--vintage", default=None,
                            help="Defaults to fingertips-<today> (pull date).")
        parser.add_argument("--indicator-id", type=int, default=LE_INDICATOR_ID)
        parser.add_argument("--area-type", type=int, default=LAD_AREA_TYPE)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **opts):
        vintage = opts["vintage"] or f"fingertips-{date.today():%Y-%m-%d}"

        indicators = {}
        for code in SEX_TO_CODE.values():
            try:
                indicators[code] = Indicator.objects.get(code=code)
            except Indicator.DoesNotExist:
                raise CommandError(f"Indicator {code!r} not seeded — run migrations / seed_v1.")
        source, _ = Source.objects.get_or_create(name=SOURCE_NAME, publisher=SOURCE_PUBLISHER)

        rows = self._fetch(opts["indicator_id"], opts["area_type"])

        # Current LAD places by code (England only — the file is already England).
        place_by_code = {
            p.gss_code: p for p in Place.objects.filter(tier=PlaceTier.LAD).order_by("valid_from")
        }

        objs, unmatched, kept, skipped = [], {}, 0, 0
        for r in rows:
            if (r.get("Category Type") or "").strip():
                continue  # skip the within-area LSOA deprivation breakdown
            code = SEX_TO_CODE.get((r.get("Sex") or "").strip())
            if code is None:
                continue
            period = _parse_pooled_period((r.get("Time period") or "").strip())
            if period is None:
                continue  # 3-year pooled only
            try:
                value = Decimal(str(r.get("Value") or "").strip())
            except (InvalidOperation, ValueError):
                skipped += 1
                continue
            area = (r.get("Area Code") or "").strip()
            place = place_by_code.get(area)
            if place is None:
                if area.startswith("E") and area != ENGLAND_CODE:
                    unmatched[area] = (r.get("Area Name") or "").strip()
                continue
            kept += 1
            objs.append(PlaceObservation(
                indicator=indicators[code], place=place,
                period_start=period[0], period_end=period[1],
                period_type=PeriodType.CALENDAR_YEAR,
                value=value, unit="", source=source, vintage=vintage,
                status=ObservationStatus.FINAL,
            ))

        if opts["dry_run"]:
            self.stdout.write(
                f"Fingertips dry run: {kept} observations, {skipped} unparseable, "
                f"{len(unmatched)} unmatched areas (vintage {vintage}).")
            return

        before = PlaceObservation.objects.filter(
            indicator__in=indicators.values(), source=source, vintage=vintage).count()
        with transaction.atomic():
            for i in range(0, len(objs), 5000):
                PlaceObservation.objects.bulk_create(objs[i:i + 5000], ignore_conflicts=True)
        after = PlaceObservation.objects.filter(
            indicator__in=indicators.values(), source=source, vintage=vintage).count()

        self.stdout.write(self.style.SUCCESS(
            f"Fingertips life expectancy: {after - before} created "
            f"({kept} rows, male+female, 3-year pooled), vintage {vintage}."))
        if unmatched:
            self.stdout.write(self.style.WARNING(
                f"{len(unmatched)} English areas had no matching LAD Place "
                f"(geography drift, e.g. post-2019 unitaries). Examples:"))
            for code, name in list(unmatched.items())[:8]:
                self.stdout.write(f"  {code}  {name}")

    def _fetch(self, indicator_id, area_type):
        params = {
            "indicator_ids": indicator_id,
            "child_area_type_id": area_type,
            "parent_area_type_id": ENGLAND_AREA_TYPE,
            "parent_area_code": ENGLAND_CODE,
        }
        url = CSV_URL + "?" + urllib.parse.urlencode(params)
        with urllib.request.urlopen(
                urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}),
                timeout=180) as resp:
            text = resp.read().decode("utf-8")
        if not text.strip() or not text.lstrip().lower().startswith("indicator"):
            raise CommandError(
                f"Fingertips returned no CSV for indicator {indicator_id} "
                f"at area type {area_type} (endpoint or IDs changed?).")
        return list(csv.DictReader(io.StringIO(text)))
