"""
Ingest Welsh deprivation (WIMD) at the LA tier from the LSOA-level results.

Wales only: the Welsh Index of Multiple Deprivation is its own statistical system —
deprivation is modelled per nation and NEVER merged into a UK-wide indicator, and never
compared across nations. Only a decile-share metric is built.

    python manage.py ingest_wimd                    # bundled seed_data/wimd ODS
    python manage.py ingest_wimd --path other.ods
    python manage.py ingest_wimd --dry-run

WIMD publishes SCORES as well as ranks, but WG's own guidance states the scores are a
construction stage, not a product, and that "it is not valid to aggregate the scores to
larger geographies by taking an average of the values for the small areas" (they are
exponentially transformed). So — unlike England's IoD — NO population-weighted score
metric is built; decile-share only, the same discipline as SIMD/NIMDM.

  wimd-most-deprived-decile-share-wales
      % of a local authority's LSOAs in Wales's most-deprived decile.
      RATE, non-additive.

The decile is TAKEN DIRECTLY from WG's published "WIMD 2019 overall decile" column (the
Deciles_quintiles_quartiles sheet) — NOT derived from rank, so there is no rounding
question. Decile 1 is the most-deprived 10% (191 of the 1,909 LSOAs). Validated against
WG's own published figure: Newport, the most-deprived-decile-share leader, reproduces
exactly (23/95 = 24.2%). LSOAs are aggregated through to LA; raw LSOA ranks/deciles are
never stored. Local authority is a NAME in the file, matched to the 22 W06 spine Places by
name (all 22 match exactly — no alias).

The file is BUNDLED (seed_data/wimd/): gov.wales is WAF-blocked from datacentre IPs, so
the deploy cannot fetch it (the elections precedent). It is ODS, which openpyxl cannot
read — but ODS is a zip of XML, so a small stdlib reader parses it with no new dependency.
"""

import zipfile
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from django.conf import settings
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

DEFAULT_PATH = Path(settings.BASE_DIR) / "seed_data" / "wimd" / "wimd2019-ranks.ods"
DECILE_SHEET = "Deciles_quintiles_quartiles"
INDICATOR_CODE = "wimd-most-deprived-decile-share-wales"
SOURCE_NAME = "Welsh Index of Multiple Deprivation"
SOURCE_PUBLISHER = "Welsh Government"

_NS = {
    "table": "urn:oasis:names:tc:opendocument:xmlns:table:1.0",
    "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0",
    "office": "urn:oasis:names:tc:opendocument:xmlns:office:1.0",
}
_T = f"{{{_NS['table']}}}"
_O = f"{{{_NS['office']}}}"


def _cell_text(cell):
    """A cell's value: office:value for numbers, else the concatenated text runs."""
    v = cell.get(f"{_O}value")
    if v is not None:
        return v
    runs = "".join("".join(p.itertext()) for p in cell.findall(f"{{{_NS['text']}}}p"))
    return runs or None


def _ods_rows(path, sheet_name):
    """Yield rows (lists of cell strings) from one sheet of an ODS file (stdlib only)."""
    with zipfile.ZipFile(path) as z:
        root = ET.fromstring(z.read("content.xml"))
    table = None
    for t in root.iter(f"{_T}table"):
        if t.get(f"{_T}name") == sheet_name:
            table = t
            break
    if table is None:
        raise CommandError(f"ODS has no sheet {sheet_name!r}.")
    for tr in table.findall(f"{_T}table-row"):
        rrep = int(tr.get(f"{_T}number-rows-repeated", "1"))
        row = []
        for tc in tr.findall(f"{_T}table-cell"):
            crep = int(tc.get(f"{_T}number-columns-repeated", "1"))
            row.extend([_cell_text(tc)] * min(crep, 100))
        while row and row[-1] is None:
            row.pop()
        # Data rows are never legitimately repeated; only blank filler rows are.
        for _ in range(min(rrep, 1)):
            yield row


def parse_wimd(path):
    """Yield (lsoa_code, la_name, decile) from the published deciles sheet."""
    rows = list(_ods_rows(path, DECILE_SHEET))
    header = code_j = la_j = dec_j = None
    for row in rows:
        cells = [str(c).strip() if c else "" for c in row]
        if "LSOA code" in cells and any("decile" in c.lower() for c in cells):
            header = cells
            code_j = cells.index("LSOA code")
            la_j = next(i for i, c in enumerate(cells) if c.startswith("Local Authority name"))
            dec_j = next(i for i, c in enumerate(cells) if "decile" in c.lower())
            break
    if header is None:
        raise CommandError("WIMD deciles sheet: header row not found.")
    seen_header = False
    for row in rows:
        if not seen_header:
            if row and str(row[code_j]).strip() == "LSOA code":
                seen_header = True
            continue
        if len(row) <= max(code_j, la_j, dec_j):
            continue
        code = str(row[code_j]).strip() if row[code_j] else ""
        if not code.startswith("W01"):
            continue
        yield code, str(row[la_j]).strip(), int(float(row[dec_j]))


class Command(BaseCommand):
    help = "Ingest WIMD most-deprived-decile-share at the LA tier (LSOA -> LA)."

    def add_arguments(self, parser):
        parser.add_argument("--path", default=str(DEFAULT_PATH),
                            help="WIMD ranks ODS (defaults to the bundled seed_data copy).")
        parser.add_argument("--edition-date", default="2019-11-27", help="POINT period for this edition.")
        parser.add_argument("--vintage", default="WIMD2019")
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **opts):
        edition = datetime.strptime(opts["edition_date"], "%Y-%m-%d").date()
        vintage = opts["vintage"]

        try:
            indicator = Indicator.objects.get(code=INDICATOR_CODE)
        except Indicator.DoesNotExist:
            raise CommandError(f"Indicator {INDICATOR_CODE!r} not seeded — run migrations / seed_v1.")
        source, _ = Source.objects.get_or_create(name=SOURCE_NAME, publisher=SOURCE_PUBLISHER)

        rows = list(parse_wimd(opts["path"]))
        if not rows:
            raise CommandError("WIMD deciles sheet parsed to zero LSOA rows.")

        # Aggregate LSOAs -> LA using WG's PUBLISHED decile (decile 1 = most deprived 10%).
        # Raw deciles are never stored — only the per-LA counts.
        agg = defaultdict(lambda: [0, 0])   # la_name -> [n_lsoa, n_in_decile1]
        for _code, la, decile in rows:
            a = agg[la]
            a[0] += 1
            if decile == 1:
                a[1] += 1

        place_by_name = {
            p.name: p
            for p in Place.objects.filter(tier=PlaceTier.LAD, gss_code__startswith="W06")
        }
        objs, unmatched = [], []
        for la, (total, d1) in agg.items():
            place = place_by_name.get(la)
            if place is None:
                unmatched.append(la)
                continue
            share = round(d1 / total * 100, 2)
            objs.append(PlaceObservation(
                indicator=indicator, place=place,
                period_start=edition, period_end=edition, period_type=PeriodType.POINT,
                value=share, unit="", source=source, vintage=vintage,
                status=ObservationStatus.FINAL,
            ))

        if opts["dry_run"]:
            self.stdout.write(
                f"WIMD dry run: {len(rows)} LSOAs, {len(agg)} LAs, {len(objs)} matched, "
                f"{len(unmatched)} unmatched (vintage {vintage}, {edition}).")
            if unmatched:
                self.stdout.write(f"  unmatched: {unmatched}")
            return

        if unmatched:
            # All 22 LAs are expected to resolve by name. An unmatched one means the file's
            # naming drifted — fail loudly rather than silently drop a whole authority.
            raise CommandError(f"{len(unmatched)} local authority name(s) did not match a "
                               f"W06 Place: {unmatched}. Add an alias if the name changed.")

        before = PlaceObservation.objects.filter(
            indicator=indicator, source=source, vintage=vintage).count()
        with transaction.atomic():
            PlaceObservation.objects.bulk_create(objs, ignore_conflicts=True)
        after = PlaceObservation.objects.filter(
            indicator=indicator, source=source, vintage=vintage).count()

        self.stdout.write(self.style.SUCCESS(
            f"WIMD (Wales): {after - before} created ({len(objs)} LAs, "
            f"published decile 1 of {len(rows)} LSOAs), vintage {vintage}, {edition}."))
