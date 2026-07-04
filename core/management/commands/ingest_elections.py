"""
Ingest UK general-election results at the Westminster constituency (WPC) tier.

Source: House of Commons Library "General election 2024: results by constituency"
CSV (one row per constituency). This is the first data on the WPC tier.

    python manage.py ingest_elections --path seed_data/elections/HoC-GE2024-results-by-constituency.csv
    python manage.py ingest_elections --path <file> --dry-run

Writes three already-seeded indicators as PlaceObservation on the WPC Places,
matched by the ONS ID (PCON code) -> Place.gss_code (latest boundary version):

  turnout                  = (valid + invalid votes) / electorate * 100   (RATE, %)
  winning-party-vote-share = winner votes / valid votes * 100             (RATE, %)
  majority                 = Majority column                              (COUNT, additive)

turnout uses ALL ballots cast (valid + rejected), which matches the published
turnout figure. winner votes come from the party column named by "First party";
for non-party winners (Ind / Spk / TUV) they come from "Of which other winner".

period: a single event, so POINT at the election date (default 2024-07-04).
source: House of Commons Library (seeded). vintage: the results edition (GE2024).
Unmatched PCON codes are reported, not dropped.
"""

import csv
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

SOURCE_NAME = "House of Commons Library — elections"
SOURCE_PUBLISHER = "House of Commons Library"
PARTY_COLS = ["Con", "Lab", "LD", "RUK", "Green", "SNP", "PC", "DUP", "SF", "SDLP", "UUP", "APNI"]


def _int(row, key):
    raw = (row.get(key) or "").strip().replace(",", "")
    return int(raw) if raw not in ("", "-") else 0


def _winner_votes(row):
    party = (row.get("First party") or "").strip()
    if party in PARTY_COLS:
        return _int(row, party)
    return _int(row, "Of which other winner")


def parse_results(path):
    """Yield (pcon_code, name, {turnout, share, majority}, winner_party)."""
    with open(path, newline="", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            code = (row.get("ONS ID") or "").strip()
            if not code:
                continue
            electorate = _int(row, "Electorate")
            valid = _int(row, "Valid votes")
            invalid = _int(row, "Invalid votes")
            majority = _int(row, "Majority")
            winner = _winner_votes(row)
            values = {}
            if electorate > 0:
                values["turnout"] = (Decimal(valid + invalid) / electorate * 100).quantize(Decimal("0.01"))
            if valid > 0:
                values["winning-party-vote-share"] = (Decimal(winner) / valid * 100).quantize(Decimal("0.01"))
            values["majority"] = Decimal(majority)
            yield code, (row.get("Constituency name") or "").strip(), values, (row.get("First party") or "").strip()


class Command(BaseCommand):
    help = "Ingest general-election results at the WPC tier (HoC Library CSV)."

    def add_arguments(self, parser):
        parser.add_argument("--path", required=True, help="HoC results-by-constituency CSV.")
        parser.add_argument("--election-date", default="2024-07-04", help="Election date (POINT period).")
        parser.add_argument("--vintage", default="GE2024", help="Results edition.")
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **opts):
        election_date = datetime.strptime(opts["election_date"], "%Y-%m-%d").date()
        vintage = opts["vintage"]

        indicators = {}
        for code in ("turnout", "winning-party-vote-share", "majority"):
            try:
                indicators[code] = Indicator.objects.get(code=code)
            except Indicator.DoesNotExist:
                raise CommandError(f"Indicator {code!r} not seeded — run seed_v1 --dimensions first.")
        source, _ = Source.objects.get_or_create(name=SOURCE_NAME, publisher=SOURCE_PUBLISHER)

        # WPC places by code (latest boundary version wins).
        place_by_code = {
            p.gss_code: p
            for p in Place.objects.filter(tier=PlaceTier.WPC).order_by("valid_from")
        }

        objs, unmatched, seats = [], {}, 0
        for code, name, values, winner in parse_results(opts["path"]):
            place = place_by_code.get(code)
            if place is None:
                unmatched[code] = name
                continue
            seats += 1
            for icode, value in values.items():
                objs.append(PlaceObservation(
                    indicator=indicators[icode], place=place,
                    period_start=election_date, period_end=election_date,
                    period_type=PeriodType.POINT,
                    value=value, unit="", source=source, vintage=vintage,
                    status=ObservationStatus.FINAL,
                ))

        if opts["dry_run"]:
            self.stdout.write(
                f"Elections dry run: {seats} constituencies, {len(objs)} observations, "
                f"{len(unmatched)} unmatched (vintage {vintage}, {election_date}).")
            return

        before = PlaceObservation.objects.filter(
            indicator__in=indicators.values(), source=source, vintage=vintage).count()
        with transaction.atomic():
            for i in range(0, len(objs), 5000):
                PlaceObservation.objects.bulk_create(objs[i:i + 5000], ignore_conflicts=True)
        after = PlaceObservation.objects.filter(
            indicator__in=indicators.values(), source=source, vintage=vintage).count()

        self.stdout.write(self.style.SUCCESS(
            f"Elections: {after - before} created ({seats} constituencies x 3 indicators), "
            f"vintage {vintage}, {election_date}."))
        if unmatched:
            self.stdout.write(self.style.WARNING(
                f"{len(unmatched)} PCON codes had no matching WPC Place. Examples:"))
            for code, name in list(unmatched.items())[:8]:
                self.stdout.write(f"  {code}  {name}")
