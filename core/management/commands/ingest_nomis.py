"""
Ingest labour-market indicators from the live Nomis API (ONS).

One command, four datasets driven by the config table below. Geography is the
current LAD boundary set (TYPE424); the response's GEOGRAPHY_CODE is the GSS code
matched to Place. Suppressed / empty values are skipped (not stored as 0/null).
Full history is pulled (no time filter). A re-pull writes new vintage rows.

    python manage.py ingest_nomis                      # all four
    python manage.py ingest_nomis --only claimant-count
    python manage.py ingest_nomis --dry-run

NOMIS_API_KEY (env) is passed as &uid= when set (higher rate limits); the API also
works keyless. The four indicators must already be seeded.

Selectors were resolved live from each dataset's concept definitions:
  claimant-count         NM_162_1  gender=0 age=0 measure=1 measures=20100   MONTH
  employment-rate-16-64  NM_17_5   variable=45 measures=20599                rolling yr
  median-weekly-pay      NM_30_1   sex=7 item=2 pay=1 measures=20100          year
  jobs-density           NM_57_1   item=3 measures=20100                       year
(NB: the employment RATE lives in NM_17_5 (percentages), not NM_17_1 which is levels.)
"""

import calendar
import csv
import io
import os
import urllib.parse
import urllib.request
from datetime import date
from decimal import Decimal, InvalidOperation

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.models import (
    Indicator,
    PeriodType,
    Place,
    PlaceObservation,
    PlaceTier,
    Source,
)

GEOGRAPHY = "TYPE424"  # local authorities: district/unitary (current boundaries)
API = "https://www.nomisweb.co.uk/api/v01/dataset/{id}.data.csv"
SOURCE_NAME = "Nomis"
SOURCE_PUBLISHER = "Office for National Statistics"

# indicator code -> Nomis dataset + selectors + period handling
DATASETS = {
    "claimant-count": {
        "id": "NM_162_1",
        "params": {"gender": 0, "age": 0, "measure": 1, "measures": 20100},
        "period": "month", "additive": True,
    },
    "employment-rate-16-64": {
        "id": "NM_17_5",
        "params": {"variable": 45, "measures": 20599},
        "period": "rolling", "additive": False,
    },
    "median-weekly-pay": {
        "id": "NM_30_1",
        "params": {"sex": 7, "item": 2, "pay": 1, "measures": 20100},
        "period": "year", "additive": False,
    },
    "jobs-density": {
        "id": "NM_57_1",
        "params": {"item": 3, "measures": 20100},
        "period": "year", "additive": False,
    },
}

_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def _month_end(y, m):
    return date(y, m, calendar.monthrange(y, m)[1])


def _period(mode, row):
    """(period_start, period_end, period_type) for a row, per the dataset's period mode."""
    if mode == "month":                       # DATE like "2026-05"
        y, m = (int(x) for x in str(row["DATE"]).split("-")[:2])
        return date(y, m, 1), _month_end(y, m), PeriodType.MONTH
    if mode == "year":                        # DATE like "2025"
        y = int(str(row["DATE"])[:4])
        return date(y, 1, 1), date(y, 12, 31), PeriodType.CALENDAR_YEAR
    if mode == "rolling":                     # DATE_NAME like "Jan 2025-Dec 2025"
        a, b = str(row["DATE_NAME"]).split("-")
        am, ay = a.split()
        bm, by = b.split()
        ps = date(int(ay), _MONTHS[am.lower()], 1)
        pe = _month_end(int(by), _MONTHS[bm.lower()])
        # A Jan–Dec rolling year is a calendar year; anything else is reported as-is.
        pt = (PeriodType.CALENDAR_YEAR
              if ps.month == 1 and pe.month == 12 and ps.year == pe.year
              else PeriodType.POINT)
        return ps, pe, pt
    raise CommandError(f"unknown period mode {mode!r}")


class Command(BaseCommand):
    help = "Ingest labour-market indicators (claimant count, employment rate, pay, jobs density) from Nomis."

    def add_arguments(self, parser):
        parser.add_argument("--only", default=None, help="One indicator code (default: all four).")
        parser.add_argument("--vintage", default=None, help="Override vintage (default: nomis-<pull date>).")
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **opts):
        key = os.environ.get("NOMIS_API_KEY", "")
        vintage = opts["vintage"] or f"nomis-{date.today().isoformat()}"
        codes = [opts["only"]] if opts["only"] else list(DATASETS)
        source, _ = Source.objects.get_or_create(name=SOURCE_NAME, publisher=SOURCE_PUBLISHER)
        place_by_code = {
            p.gss_code: p
            for p in Place.objects.filter(tier=PlaceTier.LAD).order_by("valid_from")
        }

        for code in codes:
            cfg = DATASETS.get(code)
            if cfg is None:
                raise CommandError(f"Unknown indicator {code!r}. Choices: {list(DATASETS)}")
            try:
                indicator = Indicator.objects.get(code=code)
            except Indicator.DoesNotExist:
                raise CommandError(f"Indicator {code!r} not seeded — run seed_v1 --dimensions first.")
            if indicator.is_additive != cfg["additive"]:
                self.stdout.write(self.style.WARNING(
                    f"{code}: indicator.is_additive={indicator.is_additive} but config expects "
                    f"{cfg['additive']} — check the seed."))

            rows = self._fetch(cfg, key)
            objs, unmatched, skipped = [], {}, 0
            lads, periods = set(), set()
            for r in rows:
                raw = (r.get("OBS_VALUE") or "").strip()
                if not raw:                      # suppressed / missing
                    skipped += 1
                    continue
                place = place_by_code.get((r.get("GEOGRAPHY_CODE") or "").strip())
                if place is None:
                    unmatched[r.get("GEOGRAPHY_CODE")] = r.get("GEOGRAPHY_NAME")
                    continue
                try:
                    val = Decimal(raw)
                except InvalidOperation:
                    skipped += 1
                    continue
                ps, pe, pt = _period(cfg["period"], r)
                objs.append(PlaceObservation(
                    indicator=indicator, place=place,
                    period_start=ps, period_end=pe, period_type=pt,
                    value=val, unit="", source=source, vintage=vintage,
                ))
                lads.add(place.gss_code)
                periods.add((ps, pe))

            if opts["dry_run"]:
                self.stdout.write(
                    f"{code}: {len(objs)} obs, {len(lads)} LADs, {len(periods)} periods, "
                    f"{skipped} skipped, {len(unmatched)} unmatched (vintage {vintage})")
                continue

            before = PlaceObservation.objects.filter(
                indicator=indicator, source=source, vintage=vintage).count()
            with transaction.atomic():
                for i in range(0, len(objs), 5000):
                    PlaceObservation.objects.bulk_create(objs[i:i + 5000], ignore_conflicts=True)
            after = PlaceObservation.objects.filter(
                indicator=indicator, source=source, vintage=vintage).count()

            self.stdout.write(self.style.SUCCESS(
                f"{code}: {after - before} created ({len(objs)} rows, {len(lads)} LADs, "
                f"{len(periods)} periods), {skipped} skipped, vintage {vintage}."))
            if unmatched:
                self.stdout.write(self.style.WARNING(
                    f"  {len(unmatched)} unmatched codes (post-2019 unitaries). "
                    f"e.g. {list(unmatched.items())[:5]}"))

    # Nomis caps a single response at 25,000 rows, so we must page to get the
    # full history (otherwise a large monthly series is silently truncated to its
    # earliest slice).
    PAGE = 25000

    def _fetch(self, cfg, key):
        base = {"geography": GEOGRAPHY, **cfg["params"],
                "select": "date,date_name,geography_code,geography_name,obs_value,obs_status",
                "recordlimit": self.PAGE}
        if key:
            base["uid"] = key
        rows, offset = [], 0
        while True:
            params = dict(base, RecordOffset=offset)
            url = API.format(id=cfg["id"]) + "?" + urllib.parse.urlencode(params)
            with urllib.request.urlopen(
                    urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}),
                    timeout=180) as resp:
                text = resp.read().decode("utf-8")
            if not text.strip():
                if offset == 0:
                    raise CommandError(
                        f"Nomis returned an empty body for {cfg['id']} "
                        f"(rate limit? set NOMIS_API_KEY for higher limits).")
                break
            page = list(csv.DictReader(io.StringIO(text)))
            rows.extend(page)
            if len(page) < self.PAGE:
                break
            offset += self.PAGE
        return rows
