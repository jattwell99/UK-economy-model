"""
Deploy-time data bootstrap — idempotent PER DATASET.

Runs once per container start (see docker-entrypoint.sh), before the web server, so
workers never race to seed. For each dataset it does a cheap existence check and
loads it *only if missing*, leaning on each ingester's own idempotency. It never
wipes or overwrites existing data — a populated database simply gains whatever
datasets it's missing (e.g. a new source added in a later release), which is how
HPI and future sources reach an already-live database.

Superuser creation is deliberately OUTSIDE every data guard, so it can never be
skipped because "data already exists".

Bundled inputs live in seed_data/; HPI is fetched over HTTPS at load time
(gov.uk egress is fine — only database ports were ever blocked).
"""

import os
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand

from core.models import Indicator, Place, PlaceObservation, PlaceTier

GVA_DIR = Path(settings.BASE_DIR) / "seed_data" / "gva"
GDHI_DIR = Path(settings.BASE_DIR) / "seed_data" / "gdhi"
POP_FILE = GVA_DIR / "populationestimatesbylocalauthority.xlsx"
POP_VINTAGE = "2020-06-mye"
# 2025 ONS editions (staleness refresh): GVA now runs to 2023, population to 2023.
# Both editions' files sit in GVA_DIR; a single ingest_gva run loads both by
# auto-detected vintage. Guards key on the NEW vintage so an already-seeded live DB
# gains the fresh edition (old rows update in place; new years append).
GVA_VINTAGE_2025 = "2025-04-17"
POP_FILE_2025 = GVA_DIR / "populationestimatesbylocalauthority-2025.xlsx"
POP_VINTAGE_2025 = "2025-04-mye"
PERHEAD_VINTAGE_2025 = f"gva:{GVA_VINTAGE_2025}/pop:{POP_VINTAGE_2025}"
HPI_EDITION = os.environ.get("HPI_EDITION", "2026-04")
# Life expectancy at birth for all four nations from the single ONS "LE for local
# areas of the UK" release (fetched live). Lands as a NEW vintage beside the
# Fingertips England vintage; latest-vintage-per-period then shows ONS everywhere.
ONS_LE_VINTAGE = "ons-le-2025-12-10"
ELECTIONS_DIR = Path(settings.BASE_DIR) / "seed_data" / "elections"
ELECTIONS_FILE = ELECTIONS_DIR / "HoC-GE2024-results-by-constituency.csv"
# Historic general elections on the 2010-review (old) boundary set.
OLD_WPC_FROM = "2010-05-06"   # first used at the 2010 GE …
OLD_WPC_TO = "2024-07-03"     # … superseded the day before the 2023-review set.
HISTORIC_ELECTIONS = [
    ("GE2019", "2019-12-12", "HoC-GE2019-results-by-constituency.csv"),
    ("GE2017", "2017-06-08", "HoC-GE2017-results-by-constituency.csv"),
    ("GE2015", "2015-05-07", "HoC-GE2015-results-by-constituency.csv"),
]


def _has_obs(code):
    return PlaceObservation.objects.filter(indicator__code=code).exists()


def _has_obs_vintage(code, vintage):
    return PlaceObservation.objects.filter(indicator__code=code, vintage=vintage).exists()


class Command(BaseCommand):
    help = "Idempotently ensure each dataset is loaded (per-dataset; never overwrites)."

    def _ensure(self, label, present, loader):
        if present:
            self.stdout.write(f"bootstrap_seed: {label} present — skip.")
            return
        self.stdout.write(f"bootstrap_seed: loading {label} …")
        try:
            loader()
        except Exception as exc:  # keep other datasets loading; retry next deploy
            self.stderr.write(
                f"bootstrap_seed: {label} FAILED: {exc!r} — will retry on next deploy."
            )

    def handle(self, *args, **opts):
        # Dimensions first (indicators/sources the ingesters reference).
        self._ensure(
            "dimensions", Indicator.objects.exists(),
            lambda: call_command("seed_v1", dimensions=True),
        )
        # Geography (LAD Places the observations attach to). Fetches from ONS.
        self._ensure(
            "geography", Place.objects.filter(tier=PlaceTier.LAD).exists(),
            lambda: call_command("seed_v1", geography=True),
        )
        # GVA total (bundled workbooks: 2019 + 2025 editions; one ingest_gva run loads
        # both by auto-detected vintage). Guard on the 2025 edition so a live DB gains it.
        self._ensure(
            "GVA (2025 edition)", _has_obs_vintage("gva-balanced-total", GVA_VINTAGE_2025),
            lambda: GVA_DIR.exists() and call_command("ingest_gva", path=str(GVA_DIR)),
        )
        # Population — 2020 and 2025 editions as distinct vintages beside each other.
        self._ensure(
            "population (2020 edition)", _has_obs_vintage("population", POP_VINTAGE),
            lambda: POP_FILE.exists() and call_command(
                "ingest_population", path=str(POP_FILE), vintage=POP_VINTAGE),
        )
        self._ensure(
            "population (2025 edition)", _has_obs_vintage("population", POP_VINTAGE_2025),
            lambda: POP_FILE_2025.exists() and call_command(
                "ingest_population", path=str(POP_FILE_2025), vintage=POP_VINTAGE_2025),
        )
        # GVA per head (derived; re-derive when the newest dual-input edition is missing —
        # latest-vintage-per-place-year means it extends to wherever GVA+pop now overlap).
        self._ensure(
            "gva-per-head (2025 edition)", _has_obs_vintage("gva-per-head", PERHEAD_VINTAGE_2025),
            lambda: call_command("derive_per_head"),
        )
        # GDHI total + per head (bundled workbooks; per head is ONS's own figure).
        self._ensure(
            "GDHI", _has_obs("gdhi-total"),
            lambda: GDHI_DIR.exists() and call_command("ingest_gdhi", path=str(GDHI_DIR)),
        )
        # Housing — UK House Price Index (fetched over HTTPS).
        self._ensure(
            "HPI (average-house-price)", _has_obs("average-house-price"),
            lambda: call_command("ingest_hpi", edition=HPI_EDITION),
        )
        # Labour market — Nomis API (each indicator independent; _ensure guards a
        # Nomis outage / missing NOMIS_API_KEY so it retries on the next deploy).
        for code in ("claimant-count", "employment-rate-16-64",
                     "median-weekly-pay", "jobs-density"):
            self._ensure(
                f"Nomis {code}", _has_obs(code),
                lambda c=code: call_command("ingest_nomis", only=c),
            )
        # Health — life expectancy at birth (England, LAD) from OHID Fingertips.
        # Fetched live (Fingertips is reachable); published by sex, so two indicators.
        self._ensure(
            "Fingertips life expectancy (England)", _has_obs("life-expectancy-birth-male"),
            lambda: call_command("ingest_fingertips"),
        )
        # Health — life expectancy at birth for ALL FOUR nations from the single ONS
        # UK release (fetched live). Guard on the ONS vintage so an already-seeded live
        # DB (England-only Fingertips) gains W/S/N + the ONS England series on deploy.
        self._ensure(
            "ONS life expectancy (UK, all nations)",
            _has_obs_vintage("life-expectancy-birth-male", ONS_LE_VINTAGE),
            lambda: call_command("ingest_le_ons"),
        )
        # Deprivation — English IoD 2019 at LAD (fetched live from gov.uk). Two metrics
        # (decile-share + population-weighted score) load together from File 7.
        self._ensure(
            "IoD deprivation (England)", _has_obs("imd-average-score-england"),
            lambda: call_command("ingest_imd"),
        )
        # Deprivation — Scottish SIMD 2020v2 at LAD (fetched live from gov.scot). Rank-based,
        # so decile-share only (no synthesised score). Scotland-only, never merged UK-wide.
        self._ensure(
            "SIMD deprivation (Scotland)",
            _has_obs("simd-most-deprived-decile-share-scotland"),
            lambda: call_command("ingest_simd"),
        )
        # Civic — 2024 general election at the WPC tier (bundled HoC CSV, so the
        # deploy doesn't need parliament.uk egress). New (2023-review) boundaries.
        self._ensure(
            "elections (2024 GE)", _has_obs_vintage("turnout", "GE2024"),
            lambda: ELECTIONS_FILE.exists() and call_command(
                "ingest_elections", path=str(ELECTIONS_FILE)),
        )
        # Civic — historic GEs (2019/2017/2015) on the OLD (2010-review) boundary set.
        # Each creates the old-boundary WPC Places (idempotent) and attaches results to
        # them by election-date window; the 2024 seats are untouched.
        for vintage, edate, fname in HISTORIC_ELECTIONS:
            path = ELECTIONS_DIR / fname
            self._ensure(
                f"elections ({vintage})", _has_obs_vintage("turnout", vintage),
                lambda p=path, v=vintage, d=edate: p.exists() and call_command(
                    "ingest_elections", path=str(p), election_date=d, vintage=v,
                    boundary_valid_from=OLD_WPC_FROM, boundary_valid_to=OLD_WPC_TO),
            )

        # Admin user — always ensured when a password is provided, independent of any
        # data guard (idempotent create-or-update).
        if os.environ.get("DJANGO_SUPERUSER_PASSWORD"):
            call_command("seed_admin")
            self.stdout.write("bootstrap_seed: ensured admin user.")

        self.stdout.write(self.style.SUCCESS("bootstrap_seed: done."))
