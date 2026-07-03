"""
Ingest the UK House Price Index (average price) at LAD into PlaceObservation.

UK-wide, HM Land Registry (on behalf of ONS / Registers of Scotland / LPS NI),
OGL. Monthly at local-authority level (NI quarterly). The UK HPI publishes a
mix-adjusted AVERAGE price, so this feeds `average-house-price` (RATIO,
non-additive — a roll-up must refuse it).

Source CSV (the "Average price" file). Provide one of:
    --path PATH            local CSV file
    --url URL              explicit CSV URL
    --edition YYYY-MM      builds the Land Registry average-prices URL for that edition

    python manage.py ingest_hpi --edition 2026-04
    python manage.py ingest_hpi --path Average-prices-2026-04.csv --dry-run

Columns are matched case-insensitively and tolerate CamelCase or under_scores:
    Date, RegionName/Region_Name, AreaCode/Area_Code (GSS), AveragePrice/Average_Price

One PlaceObservation per LAD-month: period_type=MONTH, period_start = 1st of the
month, period_end = month end, source + vintage (the HPI edition). Region/country
rows and post-2019 boundary codes won't match a LAD Place — those are reported,
not dropped. A new edition loaded later becomes new vintage rows.
"""

import calendar
import csv
import io
import re
import urllib.request
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.models import (
    Indicator,
    IndicatorDomain,
    PeriodType,
    Place,
    PlaceObservation,
    PlaceTier,
    Source,
    SubjectScope,
    ValueType,
)

INDICATOR_CODE = "average-house-price"
SOURCE_NAME = "UK House Price Index"
SOURCE_PUBLISHER = "HM Land Registry"
LR_AVG_URL = (
    "http://publicdata.landregistry.gov.uk/market-trend-data/"
    "house-price-index-data/Average-prices-{edition}.csv"
)


def _month_bounds(d):
    last = calendar.monthrange(d.year, d.month)[1]
    return date(d.year, d.month, 1), date(d.year, d.month, last)


def _parse_date(value):
    s = str(value).strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y-%m"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _find_col(fieldnames, *candidates):
    """Case/punctuation-insensitive column lookup (AreaCode == Area_Code)."""
    norm = {re.sub(r"[^a-z0-9]", "", c.lower()): c for c in fieldnames}
    for cand in candidates:
        key = re.sub(r"[^a-z0-9]", "", cand.lower())
        if key in norm:
            return norm[key]
    return None


def read_hpi_rows(fh):
    """Yield (gss_code, region_name, date, Decimal average_price)."""
    reader = csv.DictReader(fh)
    fns = reader.fieldnames or []
    date_c = _find_col(fns, "Date")
    code_c = _find_col(fns, "AreaCode", "Area_Code")
    name_c = _find_col(fns, "RegionName", "Region_Name")
    price_c = _find_col(fns, "AveragePrice", "Average_Price")
    if not (date_c and code_c and price_c):
        raise CommandError(
            f"HPI CSV missing expected columns (need Date, AreaCode, AveragePrice); "
            f"found {fns}"
        )
    for row in reader:
        code = (row.get(code_c) or "").strip()
        d = _parse_date(row.get(date_c))
        raw = (row.get(price_c) or "").strip()
        if not code or d is None or not raw:
            continue
        try:
            val = Decimal(raw)
        except InvalidOperation:
            continue
        if val <= 0:  # some area/date cells are blank/zero
            continue
        name = (row.get(name_c) or "").strip() if name_c else ""
        yield code, name, d, val


class Command(BaseCommand):
    help = "Ingest UK House Price Index (average price) at LAD into PlaceObservation."

    def add_arguments(self, parser):
        parser.add_argument("--path", default=None, help="Local CSV file.")
        parser.add_argument("--url", default=None, help="Explicit CSV URL.")
        parser.add_argument("--edition", default=None,
                            help="YYYY-MM; builds the Land Registry average-prices URL.")
        parser.add_argument("--vintage", default=None,
                            help="Override the vintage (defaults to the edition / URL date).")
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **opts):
        text, origin = self._load_csv_text(opts)
        vintage = (
            opts["vintage"] or opts["edition"] or self._infer_vintage(origin) or "unknown"
        )
        rows = list(read_hpi_rows(io.StringIO(text)))
        if not rows:
            raise CommandError("No usable rows parsed from the HPI CSV.")

        if opts["dry_run"]:
            areas = {r[0] for r in rows}
            months = {(r[2].year, r[2].month) for r in rows}
            self.stdout.write(
                f"{len(rows)} rows, {len(areas)} areas, {len(months)} months, "
                f"vintage={vintage} (source: {origin})"
            )
            return

        indicator = self._indicator()
        source, _ = Source.objects.get_or_create(
            name=SOURCE_NAME, publisher=SOURCE_PUBLISHER,
        )
        place_by_code = {
            p.gss_code: p
            for p in Place.objects.filter(tier=PlaceTier.LAD).order_by("valid_from")
        }

        objs, unmatched, matched_lads, matched_months = [], {}, set(), set()
        for code, name, d, val in rows:
            place = place_by_code.get(code)
            if place is None:
                unmatched[code] = name
                continue
            ps, pe = _month_bounds(d)
            objs.append(PlaceObservation(
                indicator=indicator, place=place,
                period_start=ps, period_end=pe, period_type=PeriodType.MONTH,
                value=val, unit="", source=source, vintage=vintage,
            ))
            matched_lads.add(code)
            matched_months.add((ps.year, ps.month))

        # Bulk insert; the uq_place_obs constraint makes this idempotent per vintage.
        before = PlaceObservation.objects.filter(
            indicator=indicator, source=source, vintage=vintage,
        ).count()
        with transaction.atomic():
            for i in range(0, len(objs), 5000):
                PlaceObservation.objects.bulk_create(objs[i:i + 5000], ignore_conflicts=True)
        after = PlaceObservation.objects.filter(
            indicator=indicator, source=source, vintage=vintage,
        ).count()

        self.stdout.write(self.style.SUCCESS(
            f"HPI: {after - before} created ({len(objs)} rows for {len(matched_lads)} LADs "
            f"across {len(matched_months)} months), vintage {vintage}."
        ))
        if unmatched:
            self.stdout.write(self.style.WARNING(
                f"{len(unmatched)} area codes had no matching LAD Place "
                f"(regions/countries + boundary churn — expected). Examples:"
            ))
            for code, name in list(unmatched.items())[:8]:
                self.stdout.write(f"  {code}  {name}")

    # -- helpers -----------------------------------------------------------

    def _load_csv_text(self, opts):
        if opts["path"]:
            with open(opts["path"], encoding="utf-8-sig") as fh:
                return fh.read(), opts["path"]
        url = opts["url"] or (
            LR_AVG_URL.format(edition=opts["edition"]) if opts["edition"] else None
        )
        if not url:
            raise CommandError("Provide one of --path, --url, or --edition YYYY-MM.")
        self.stdout.write(f"Fetching {url} …")
        with urllib.request.urlopen(url, timeout=180) as resp:
            return resp.read().decode("utf-8-sig"), url

    @staticmethod
    def _infer_vintage(origin):
        m = re.search(r"(\d{4})[-_](\d{2})", origin or "")
        return f"{m.group(1)}-{m.group(2)}" if m else None

    @staticmethod
    def _indicator():
        try:
            return Indicator.objects.get(code=INDICATOR_CODE)
        except Indicator.DoesNotExist:
            domain, _ = IndicatorDomain.objects.get_or_create(
                code="housing", defaults={"name": "Housing"},
            )
            return Indicator.objects.create(
                code=INDICATOR_CODE, name="Average house price (UK HPI)",
                domain=domain, unit="£", value_type=ValueType.RATIO,
                is_additive=False, subject_scope=SubjectScope.PLACE,
            )
