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
POP_FILE = GVA_DIR / "populationestimatesbylocalauthority.xlsx"
POP_VINTAGE = "2020-06-mye"
HPI_EDITION = os.environ.get("HPI_EDITION", "2026-04")


def _has_obs(code):
    return PlaceObservation.objects.filter(indicator__code=code).exists()


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
        # GVA total (bundled workbooks).
        self._ensure(
            "GVA", _has_obs("gva-balanced-total"),
            lambda: GVA_DIR.exists() and call_command("ingest_gva", path=str(GVA_DIR)),
        )
        # Population (bundled workbook).
        self._ensure(
            "population", _has_obs("population"),
            lambda: POP_FILE.exists() and call_command(
                "ingest_population", path=str(POP_FILE), vintage=POP_VINTAGE),
        )
        # GVA per head (derived from the two above).
        self._ensure(
            "gva-per-head", _has_obs("gva-per-head"),
            lambda: call_command("derive_per_head"),
        )
        # Housing — UK House Price Index (fetched over HTTPS).
        self._ensure(
            "HPI (average-house-price)", _has_obs("average-house-price"),
            lambda: call_command("ingest_hpi", edition=HPI_EDITION),
        )

        # Admin user — always ensured when a password is provided, independent of any
        # data guard (idempotent create-or-update).
        if os.environ.get("DJANGO_SUPERUSER_PASSWORD"):
            call_command("seed_admin")
            self.stdout.write("bootstrap_seed: ensured admin user.")

        self.stdout.write(self.style.SUCCESS("bootstrap_seed: done."))
