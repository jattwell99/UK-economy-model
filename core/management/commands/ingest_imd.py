"""
Ingest English deprivation (IoD) at the LAD tier from the LSOA-level file.

England only: the English Indices of Deprivation are their own statistical system —
deprivation is modelled per nation and NEVER merged into a UK-wide indicator. The
2019 edition uses 2011 LSOAs and 2019 LADs (matching our Dec-2019 spine). The 2025
update loads later as a separate vintage.

    python manage.py ingest_imd                       # fetch IoD 2019 "File 7" from gov.uk
    python manage.py ingest_imd --path File_7.csv     # or a local copy
    python manage.py ingest_imd --dry-run

Two LAD metrics are derived from the LSOA rows (LSOAs are NOT Place rows — we only
aggregate through them; raw LSOA ranks are never stored):

  imd-most-deprived-decile-share-england
      % of a LAD's LSOAs that fall in the national most-deprived decile (Decile == 1).
      Ranking-derived (RATE, non-additive).
  imd-average-score-england
      Population-weighted mean of LSOA IMD SCORES (weights = Total population mid-2015).
      Cardinal (INDEX, non-additive).

"File 7 — All IoD2019 Scores, Ranks, Deciles and Population Denominators" carries the
LSOA code, the LAD code+name, the IMD Score, the IMD Decile, and the population
denominator in one file, so no separate LSOA->LAD lookup or population file is needed.

period: POINT at the edition date. source: English Indices of Deprivation (seeded).
vintage: the edition (IoD2019). Unmatched LAD codes are reported, not dropped.
"""

import csv
import io
import urllib.request
from datetime import date, datetime
from decimal import Decimal

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

IOD2019_FILE7_URL = (
    "https://assets.publishing.service.gov.uk/government/uploads/system/uploads/"
    "attachment_data/file/845345/"
    "File_7_-_All_IoD2019_Scores__Ranks__Deciles_and_Population_Denominators_3.csv"
)
SOURCE_NAME = "English Indices of Deprivation"
SOURCE_PUBLISHER = "Ministry of Housing, Communities and Local Government"
SHARE_CODE = "imd-most-deprived-decile-share-england"
SCORE_CODE = "imd-average-score-england"

COL_LAD = "Local Authority District code (2019)"
COL_SCORE = "Index of Multiple Deprivation (IMD) Score"
COL_DECILE = "Index of Multiple Deprivation (IMD) Decile (where 1 is most deprived 10% of LSOAs)"
COL_POP = "Total population: mid 2015 (excluding prisoners)"


class Command(BaseCommand):
    help = "Ingest English IoD deprivation metrics at the LAD tier (LSOA file -> LAD)."

    def add_arguments(self, parser):
        parser.add_argument("--path", default=None, help="Local IoD file (else fetch from gov.uk).")
        parser.add_argument("--url", default=IOD2019_FILE7_URL)
        parser.add_argument("--edition-date", default="2019-09-26", help="POINT period for this edition.")
        parser.add_argument("--vintage", default="IoD2019")
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **opts):
        edition = datetime.strptime(opts["edition_date"], "%Y-%m-%d").date()
        vintage = opts["vintage"]

        indicators = {}
        for code in (SHARE_CODE, SCORE_CODE):
            try:
                indicators[code] = Indicator.objects.get(code=code)
            except Indicator.DoesNotExist:
                raise CommandError(f"Indicator {code!r} not seeded — run migrations / seed_v1.")
        source, _ = Source.objects.get_or_create(name=SOURCE_NAME, publisher=SOURCE_PUBLISHER)

        rows = self._read(opts["path"], opts["url"])

        # Aggregate LSOA rows to LAD.
        agg = {}  # lad_code -> [n_lsoa, n_decile1, sum(score*pop), sum(pop)]
        for r in rows:
            code = (r.get(COL_LAD) or "").strip()
            if not code:
                continue
            a = agg.setdefault(code, [0, 0, 0.0, 0.0])
            a[0] += 1
            if int(r[COL_DECILE]) == 1:
                a[1] += 1
            pop = float(r[COL_POP])
            a[2] += float(r[COL_SCORE]) * pop
            a[3] += pop

        # Match to English LAD Places and build both metric observations.
        place_by_code = {
            p.gss_code: p for p in Place.objects.filter(tier=PlaceTier.LAD).order_by("valid_from")
        }
        objs, unmatched = [], {}
        for code, (n, d1, sw, w) in agg.items():
            place = place_by_code.get(code)
            if place is None:
                unmatched[code] = n
                continue
            share = (Decimal(d1) / n * 100).quantize(Decimal("0.01"))
            score = (Decimal(str(sw / w))).quantize(Decimal("0.01")) if w else None
            metrics = {SHARE_CODE: share}
            if score is not None:
                metrics[SCORE_CODE] = score
            for icode, value in metrics.items():
                objs.append(PlaceObservation(
                    indicator=indicators[icode], place=place,
                    period_start=edition, period_end=edition, period_type=PeriodType.POINT,
                    value=value, unit="", source=source, vintage=vintage,
                    status=ObservationStatus.FINAL,
                ))

        if opts["dry_run"]:
            self.stdout.write(
                f"IMD dry run: {len(agg)} LADs, {len(objs)} observations, "
                f"{len(unmatched)} unmatched (vintage {vintage}, {edition}).")
            return

        before = PlaceObservation.objects.filter(
            indicator__in=indicators.values(), source=source, vintage=vintage).count()
        with transaction.atomic():
            for i in range(0, len(objs), 5000):
                PlaceObservation.objects.bulk_create(objs[i:i + 5000], ignore_conflicts=True)
        after = PlaceObservation.objects.filter(
            indicator__in=indicators.values(), source=source, vintage=vintage).count()

        self.stdout.write(self.style.SUCCESS(
            f"IMD (England): {after - before} created ({len(agg)} LADs x 2 metrics), "
            f"vintage {vintage}, {edition}."))
        if unmatched:
            self.stdout.write(self.style.WARNING(
                f"{len(unmatched)} LAD codes had no matching Place. Examples:"))
            for code, n in list(unmatched.items())[:8]:
                self.stdout.write(f"  {code}  ({n} LSOAs)")

    def _read(self, path, url):
        if path:
            with open(path, newline="", encoding="utf-8-sig") as fh:
                return list(csv.DictReader(fh))
        with urllib.request.urlopen(
                urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}),
                timeout=180) as resp:
            text = resp.read().decode("utf-8-sig")
        rows = list(csv.DictReader(io.StringIO(text)))
        if not rows or COL_LAD not in rows[0]:
            raise CommandError(f"IoD file at {url} missing expected columns (edition changed?).")
        return rows
