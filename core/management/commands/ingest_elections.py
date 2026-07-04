"""
Ingest UK general-election results at the Westminster constituency (WPC) tier.

Source: House of Commons Library "General election <year>: results by constituency"
CSVs (one row per constituency). 2024 was the first WPC data; 2015/2017/2019 add the
historic series on the PREVIOUS (2010-review) boundary set.

    # 2024 (new boundaries — Places pre-seeded by seed_v1)
    python manage.py ingest_elections --path seed_data/elections/HoC-GE2024-results-by-constituency.csv

    # historic (old boundaries — create the versioned Place batch as we load)
    python manage.py ingest_elections \
        --path seed_data/elections/HoC-GE2019-results-by-constituency.csv \
        --election-date 2019-12-12 --vintage GE2019 \
        --boundary-valid-from 2010-05-06 --boundary-valid-to 2024-07-03

Writes three already-seeded indicators as PlaceObservation on the WPC Places:

  turnout                  = (valid + invalid votes) / electorate * 100   (RATE, %)
  winning-party-vote-share = winner votes / valid votes * 100             (RATE, %)
  majority                 = Majority column                              (COUNT, additive)

turnout uses ALL ballots cast (valid + rejected), matching the published figure;
every HoC file here carries an "Invalid votes" column, so the basis is identical
across years. winner votes are located, in order: (1) the party column named by
"First party"; (2) the "Of which other winner" column when present (non-party
winners: Ind / Spk); (3) as a fallback for files without that column (2015),
reconstructed as Majority + runner-up votes (the "Second party" column). The set of
party columns is read from each file's header (it varies by year: UKIP, BRX, RUK).

Boundary versioning: results attach to the Place whose validity window
[valid_from, valid_to] contains the election date, NOT "latest version wins" — the
2010-review set (2015/17/19) and the 2023-review set (2024) coexist, and a handful of
Scottish codes appear in both. With --boundary-valid-from the old-boundary Places are
created (idempotently) from the file before results are attached.

period: a single event, so POINT at the election date. source: House of Commons
Library (seeded). vintage: the results edition (GE2015 / GE2017 / GE2019 / GE2024).
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

# Columns that are not per-party vote counts, so they bound the party-column block.
FIRST_PARTY_COL = "Majority"          # party columns start after this …
LAST_PARTY_STOP = "All other candidates"  # … and stop before this.
FAR_FUTURE = date(9999, 12, 31)       # stand-in for an open (valid_to IS NULL) window.


def _int(row, key):
    raw = (row.get(key) or "").strip().replace(",", "")
    return int(raw) if raw not in ("", "-") else 0


def _party_columns(fieldnames):
    """The per-party vote columns for THIS file (adapts to UKIP / BRX / RUK by year)."""
    names = list(fieldnames)
    try:
        start = names.index(FIRST_PARTY_COL) + 1
    except ValueError:
        return []
    stop = names.index(LAST_PARTY_STOP) if LAST_PARTY_STOP in names else len(names)
    return names[start:stop]


def _winner_votes(row, party_cols):
    """Locate the winning candidate's vote count across the differing file layouts."""
    party = (row.get("First party") or "").strip()
    if party in party_cols:                       # (1) a tracked party won
        return _int(row, party)
    if "Of which other winner" in row:            # (2) non-party winner, column present
        owin = _int(row, "Of which other winner")
        if owin:
            return owin
    # (3) file without that column (2015): winner = Majority + runner-up votes.
    second = (row.get("Second party") or "").strip()
    if second in party_cols:
        return _int(row, "Majority") + _int(row, second)
    return 0


def parse_results(path):
    """Yield (pcon_code, name, {turnout, share, majority}, winner_party)."""
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        party_cols = _party_columns(reader.fieldnames)
        for row in reader:
            code = (row.get("ONS ID") or "").strip()
            if not code:
                continue
            electorate = _int(row, "Electorate")
            valid = _int(row, "Valid votes")
            invalid = _int(row, "Invalid votes")
            majority = _int(row, "Majority")
            winner = _winner_votes(row, party_cols)
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
        parser.add_argument("--boundary-valid-from", default=None,
                            help="If set, create the WPC Places at this boundary version "
                                 "(YYYY-MM-DD) from the file before attaching results.")
        parser.add_argument("--boundary-valid-to", default=None,
                            help="valid_to for the created boundary version (YYYY-MM-DD).")
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **opts):
        election_date = datetime.strptime(opts["election_date"], "%Y-%m-%d").date()
        vintage = opts["vintage"]
        b_from = (datetime.strptime(opts["boundary_valid_from"], "%Y-%m-%d").date()
                  if opts["boundary_valid_from"] else None)
        b_to = (datetime.strptime(opts["boundary_valid_to"], "%Y-%m-%d").date()
                if opts["boundary_valid_to"] else None)

        indicators = {}
        for code in ("turnout", "winning-party-vote-share", "majority"):
            try:
                indicators[code] = Indicator.objects.get(code=code)
            except Indicator.DoesNotExist:
                raise CommandError(f"Indicator {code!r} not seeded — run seed_v1 --dimensions first.")
        source, _ = Source.objects.get_or_create(name=SOURCE_NAME, publisher=SOURCE_PUBLISHER)

        parsed = list(parse_results(opts["path"]))

        # Optionally materialise the old-boundary Place batch (idempotent) before matching.
        if b_from is not None and not opts["dry_run"]:
            created_places = 0
            for code, name, _values, _winner in parsed:
                _, made = Place.objects.get_or_create(
                    gss_code=code, valid_from=b_from,
                    defaults={"name": name, "tier": PlaceTier.WPC, "valid_to": b_to},
                )
                created_places += int(made)
            self.stdout.write(
                f"Boundary {b_from}..{b_to or 'current'}: {created_places} WPC Places created "
                f"({len(parsed) - created_places} already present).")

        # Election-date-window resolver: pick the Place whose [valid_from, valid_to]
        # contains the election date (NOT "latest version wins"). Handles the old/new
        # sets coexisting and the Scottish codes shared across both.
        def resolve(code):
            best = None
            for p in Place.objects.filter(tier=PlaceTier.WPC, gss_code=code):
                if p.valid_from <= election_date <= (p.valid_to or FAR_FUTURE):
                    if best is None or p.valid_from > best.valid_from:
                        best = p
            return best

        objs, unmatched, seats = [], {}, 0
        for code, name, values, winner in parsed:
            place = resolve(code)
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
