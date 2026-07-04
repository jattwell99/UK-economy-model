"""
Refresh the LAD spine for post-2019 boundary restructures (data half of the
LAD-vintage refresh — see docs/lad_vintage_refresh_scoping_brief.md).

Between 2020 and 2023 several two-tier areas were reorganised into new unitary
authorities. The spine was fixed at the December 2019 LAD set, so those 7 new unitaries
have no Place row and their post-restructure observations silently drop on every ingest.
This command applies the fix, using the SAME versioning the WPC boundary change used:

  - version-IN the 7 new unitaries  (create Place, valid_from = creation date)
  - version-OUT the 28 abolished districts (set valid_to = day before the successor's
    creation) — their historic observations are real data for a real former area and are
    KEPT, never deleted.

It does NOT touch geometry — the map's per-feature-validity layer is a separate, deferred
phase. Until then the new unitaries render as absent on the map (no shape) but appear on
the list / detail / compare surfaces (verified: choropleth iterates GeoJSON features, so a
shapeless place is simply not drawn — no error).

Idempotent: creating a unitary and setting a district's valid_to are both no-ops once done.

    python manage.py refresh_lad_spine
    python manage.py refresh_lad_spine --dry-run

The pure-recode cases (Barnsley/Sheffield, ONS-LE-only) are handled by an alias in
ingest_le_ons, not here — they are the same real place under a new code, not a restructure.
"""

from datetime import date, timedelta

from django.core.management.base import BaseCommand
from django.db import transaction

from core.models import Place, PlaceTier

# (unitary_code, unitary_name, created_on, [abolished district codes])
# The districts' valid_to is created_on - 1 day. All England (nation derived on save).
RESTRUCTURES = [
    ("E06000060", "Buckinghamshire", date(2020, 4, 1),
     ["E07000004", "E07000005", "E07000006", "E07000007"]),
    ("E06000061", "North Northamptonshire", date(2021, 4, 1),
     ["E07000150", "E07000152", "E07000153", "E07000156"]),
    ("E06000062", "West Northamptonshire", date(2021, 4, 1),
     ["E07000151", "E07000154", "E07000155"]),
    ("E06000063", "Cumberland", date(2023, 4, 1),
     ["E07000026", "E07000028", "E07000029"]),
    ("E06000064", "Westmorland and Furness", date(2023, 4, 1),
     ["E07000027", "E07000030", "E07000031"]),
    ("E06000065", "North Yorkshire", date(2023, 4, 1),
     ["E07000163", "E07000164", "E07000165", "E07000166", "E07000167", "E07000168", "E07000169"]),
    ("E06000066", "Somerset", date(2023, 4, 1),
     ["E07000187", "E07000188", "E07000189", "E07000246"]),
]

# Dec-2019 spine valid_from — the districts to version out all share it.
SPINE_VALID_FROM = date(2019, 12, 1)

# Pure recodes: some ONS products (life-expectancy, housing affordability) emit new GSS
# codes for two metropolitan districts whose boundaries did NOT change, while standard ONS
# LAD geography and other sources still use the old codes. The spine keeps the old code;
# ingesters map the new code back via this alias so the data lands on the existing Place.
# Same real place, new identifier — an alias, not a restructure. Shared so the recode set
# is one truth across ingesters.
RECODE_ALIASES = {
    "E08000038": "E08000016",   # Barnsley
    "E08000039": "E08000019",   # Sheffield
}


class Command(BaseCommand):
    help = "Version-in post-2019 unitary LADs and version-out the abolished districts."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **opts):
        dry = opts["dry_run"]
        created_unitaries = 0
        versioned_districts = 0
        missing_districts = []

        with transaction.atomic():
            for ucode, uname, created_on, districts in RESTRUCTURES:
                valid_to = created_on - timedelta(days=1)

                # version-IN the unitary (idempotent on gss_code + valid_from)
                exists = Place.objects.filter(gss_code=ucode, valid_from=created_on).exists()
                if not exists:
                    if not dry:
                        Place.objects.create(
                            gss_code=ucode, name=uname, tier=PlaceTier.LAD,
                            valid_from=created_on,   # valid_to stays null (current)
                        )
                    created_unitaries += 1
                    self.stdout.write(f"  +unitary {ucode} {uname} (valid_from {created_on})")

                # version-OUT the abolished districts (set valid_to if not already)
                for dcode in districts:
                    d = Place.objects.filter(gss_code=dcode, tier=PlaceTier.LAD,
                                             valid_from=SPINE_VALID_FROM).first()
                    if d is None:
                        missing_districts.append(dcode)
                        continue
                    if d.valid_to is None:
                        if not dry:
                            d.valid_to = valid_to
                            d.save(update_fields=["valid_to"])
                        versioned_districts += 1
                        self.stdout.write(f"    -district {dcode} {d.name} -> valid_to {valid_to}")

        prefix = "DRY RUN — " if dry else ""
        self.stdout.write(self.style.SUCCESS(
            f"{prefix}LAD spine refresh: {created_unitaries} unitaries versioned-in, "
            f"{versioned_districts} districts versioned-out."))
        if missing_districts:
            # Loud, not silent: a spine without the expected Dec-2019 districts means
            # geography wasn't seeded yet (or a code changed) — report it.
            self.stdout.write(self.style.WARNING(
                f"{len(missing_districts)} expected districts not found on the spine "
                f"(geography not seeded yet?): {missing_districts}"))
