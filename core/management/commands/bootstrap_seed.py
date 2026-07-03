"""
One-shot data bootstrap for a fresh deployment.

Loads dimensions, geography and the bundled ONS workbooks (seed_data/gva/) so a
deploy is self-populating — no shell access to the container required. It is
idempotent: if the database already has observations it does nothing, so it's
safe to run on every boot (see docker-entrypoint.sh).

If DJANGO_SUPERUSER_PASSWORD is set, it also creates the admin user.
"""

import os
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand

from core.models import PlaceObservation

GVA_DIR = Path(settings.BASE_DIR) / "seed_data" / "gva"
POP_FILE = GVA_DIR / "populationestimatesbylocalauthority.xlsx"
POP_VINTAGE = "2020-06-mye"


class Command(BaseCommand):
    help = "Idempotently load dimensions, geography and bundled ONS data on a fresh DB."

    def handle(self, *args, **opts):
        # Data load: only on a fresh DB (idempotent no-op once observations exist).
        if PlaceObservation.objects.exists():
            self.stdout.write("bootstrap_seed: data already present — skipping data load.")
        else:
            self.stdout.write("bootstrap_seed: empty database — loading data (one-off)…")
            call_command("seed_v1", dimensions=True, geography=True)
            if GVA_DIR.exists():
                call_command("ingest_gva", path=str(GVA_DIR))
                if POP_FILE.exists():
                    call_command("ingest_population", path=str(POP_FILE), vintage=POP_VINTAGE)
                call_command("derive_per_head")
            else:
                self.stdout.write(self.style.WARNING(
                    f"bootstrap_seed: {GVA_DIR} not found — dimensions/geography only."
                ))

        # Admin: ensure it every boot when a password is provided (independent of the
        # data load, so it works even after the data is already seeded). Idempotent —
        # seed_admin creates or updates the user.
        if os.environ.get("DJANGO_SUPERUSER_PASSWORD"):
            call_command("seed_admin")
            self.stdout.write("bootstrap_seed: ensured admin user.")

        self.stdout.write(self.style.SUCCESS("bootstrap_seed: done."))
