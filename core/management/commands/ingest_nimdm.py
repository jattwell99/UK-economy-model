"""
Ingest Northern Irish deprivation (NIMDM) at the LGD tier from the SOA-level results.

Northern Ireland only: the NI Multiple Deprivation Measure is its own statistical system —
deprivation is modelled per nation and NEVER merged into a UK-wide indicator, and never
compared across nations. NIMDM is RANK-based with NO published composite score, so only a
decile-share metric is derived (a synthesised score from domain ranks would be editorial
invention — explicitly out of scope, same discipline as SIMD/IMD).

    python manage.py ingest_nimdm                     # bundled seed_data/nimdm CSV
    python manage.py ingest_nimdm --path other.csv
    python manage.py ingest_nimdm --dry-run

The file is BUNDLED (seed_data/nimdm/nimdm2017-soa.csv) rather than fetched live: NISRA
publishes only a legacy multi-sheet .xls (needs an extra reader dependency and fragile
parsing), and the clean flat CSV — same NISRA data via the official Open Data NI portal —
blocks default clients (HTTP 403) and its egress reachability from the deploy is
unverifiable. The file is tiny, so bundling it (the elections precedent) makes the deploy
reliable with no new dependency.

One LGD metric is derived from the SOA rows (SOAs are NOT Place rows — we only aggregate
through them; raw SOA ranks are never stored):

  nimdm-most-deprived-decile-share-northern-ireland
      % of a district's SOAs in NI's most-deprived decile (within-NI decile).
      RATE, non-additive.

The decile is DERIVED from the overall MDM rank — the file has no decile column. NISRA
defines decile 1 as the most-deprived 10% of SOAs = ranks 1-89 (890 SOAs, 890/10 = 89
exactly, so no rounding ambiguity). VALIDATED against NISRA's own published figures before
shipping: the 10 most-deprived SOAs split 5 Belfast / 5 Derry City & Strabane, reproduced
exactly (see CLAUDE.md).

Roll-up is by CODE: the file's `LGD2014code` IS the N09 GSS code, so it joins straight to
the spine's 11 N09 Places — no name matching or alias (unlike SIMD's council names).
"""

import csv
import math
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

DEFAULT_PATH = Path(settings.BASE_DIR) / "seed_data" / "nimdm" / "nimdm2017-soa.csv"
COL_LGD = "LGD2014code"
COL_SOA = "SOA2001"
COL_RANK = "MDM_rank"
INDICATOR_CODE = "nimdm-most-deprived-decile-share-northern-ireland"
SOURCE_NAME = "Northern Ireland Multiple Deprivation Measure"
SOURCE_PUBLISHER = "Northern Ireland Statistics and Research Agency"


def parse_nimdm(path):
    """Yield (soa_code, lgd_code, rank) from the NIMDM SOA results CSV."""
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        for col in (COL_LGD, COL_SOA, COL_RANK):
            if col not in reader.fieldnames:
                raise CommandError(f"NIMDM CSV missing column {col!r} (edition changed?).")
        for row in reader:
            code = (row.get(COL_LGD) or "").strip()
            if not code:
                continue
            yield (row.get(COL_SOA) or "").strip(), code, int(row[COL_RANK])


class Command(BaseCommand):
    help = "Ingest NIMDM most-deprived-decile-share at the LGD tier (SOA -> LGD)."

    def add_arguments(self, parser):
        parser.add_argument("--path", default=str(DEFAULT_PATH),
                            help="NIMDM SOA CSV (defaults to the bundled seed_data copy).")
        parser.add_argument("--edition-date", default="2017-11-23", help="POINT period for this edition.")
        parser.add_argument("--vintage", default="NIMDM2017")
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **opts):
        edition = datetime.strptime(opts["edition_date"], "%Y-%m-%d").date()
        vintage = opts["vintage"]

        try:
            indicator = Indicator.objects.get(code=INDICATOR_CODE)
        except Indicator.DoesNotExist:
            raise CommandError(f"Indicator {INDICATOR_CODE!r} not seeded — run migrations / seed_v1.")
        source, _ = Source.objects.get_or_create(name=SOURCE_NAME, publisher=SOURCE_PUBLISHER)

        rows = list(parse_nimdm(opts["path"]))
        if not rows:
            raise CommandError("NIMDM CSV parsed to zero rows.")

        # National most-deprived decile = the ceil(N/10) lowest-ranked SOAs (NISRA's
        # "most deprived 10%"; N=890 divides evenly so decile 1 = ranks 1-89). Derived
        # from rank — the file has no decile column.
        n = len(rows)
        decile1_cut = math.ceil(n / 10)

        # Aggregate SOAs -> LGD (raw ranks never stored, only the counts).
        agg = defaultdict(lambda: [0, 0])   # lgd_code -> [n_soa, n_in_decile1]
        for _soa, lgd, rank in rows:
            a = agg[lgd]
            a[0] += 1
            if rank <= decile1_cut:
                a[1] += 1

        # LGD2014code IS the N09 GSS code -> join straight to the spine (no name matching).
        place_by_code = {
            p.gss_code: p
            for p in Place.objects.filter(tier=PlaceTier.LAD, gss_code__startswith="N09")
        }
        objs, unmatched = [], []
        for lgd, (total, d1) in agg.items():
            place = place_by_code.get(lgd)
            if place is None:
                unmatched.append(lgd)
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
                f"NIMDM dry run: {n} SOAs, decile-1 cut rank<={decile1_cut}, "
                f"{len(agg)} LGDs, {len(objs)} matched, {len(unmatched)} unmatched "
                f"(vintage {vintage}, {edition}).")
            if unmatched:
                self.stdout.write(f"  unmatched: {unmatched}")
            return

        if unmatched:
            # All 11 LGDs are expected to resolve (LGD2014code == N09 GSS code). An
            # unmatched one means code drift — fail loudly, don't drop a whole district.
            raise CommandError(f"{len(unmatched)} LGD code(s) had no matching N09 Place: "
                               f"{unmatched}.")

        before = PlaceObservation.objects.filter(
            indicator=indicator, source=source, vintage=vintage).count()
        with transaction.atomic():
            PlaceObservation.objects.bulk_create(objs, ignore_conflicts=True)
        after = PlaceObservation.objects.filter(
            indicator=indicator, source=source, vintage=vintage).count()

        self.stdout.write(self.style.SUCCESS(
            f"NIMDM (Northern Ireland): {after - before} created ({len(objs)} LGDs, "
            f"decile-1 cut rank<={decile1_cut} of {n} SOAs), vintage {vintage}, {edition}."))
