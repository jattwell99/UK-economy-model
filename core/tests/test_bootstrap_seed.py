"""
bootstrap_seed: per-dataset idempotency + superuser always runs.

call_command is patched so no network/files are touched; we only assert *which*
loaders would run given what's already in the database.
"""

from datetime import date
from decimal import Decimal
from unittest import mock

from django.core.management import call_command
from django.test import TestCase

from core.models import PlaceTier, ValueType

from .factories import make_domain, make_indicator, make_observation, make_place, make_source


def _loaders_called(mock_cc):
    """Return the set of (command, key-detail) tuples call_command was invoked with."""
    calls = set()
    for c in mock_cc.call_args_list:
        name = c.args[0]
        detail = c.kwargs.get("dimensions") and "dimensions" or \
            c.kwargs.get("geography") and "geography" or ""
        calls.add((name, detail))
    return calls


class BootstrapGatingTests(TestCase):
    @mock.patch.dict("os.environ", {"DJANGO_SUPERUSER_PASSWORD": "pw"})
    @mock.patch("core.management.commands.bootstrap_seed.call_command")
    def test_empty_db_loads_everything(self, cc):
        call_command("bootstrap_seed")
        names = [c.args[0] for c in cc.call_args_list]
        self.assertIn("seed_v1", names)          # dimensions + geography
        self.assertIn("ingest_gva", names)
        self.assertIn("ingest_population", names)
        self.assertIn("derive_per_head", names)
        self.assertIn("ingest_hpi", names)
        self.assertIn("seed_admin", names)       # superuser ensured

    @mock.patch.dict("os.environ", {"DJANGO_SUPERUSER_PASSWORD": "pw"})
    @mock.patch("core.management.commands.bootstrap_seed.call_command")
    def test_populated_db_loads_nothing_but_admin(self, cc):
        # Simulate a fully-populated database.
        econ = make_domain("economy", "Economy")
        src = make_source()
        lad = make_place("E08000003", "Manchester", tier=PlaceTier.LAD)
        for code, vt, add in [
            ("gva-balanced-total", ValueType.CURRENCY, True),
            ("population", ValueType.COUNT, True),
            ("gva-per-head", ValueType.RATIO, False),
            ("average-house-price", ValueType.RATIO, False),
        ]:
            ind = make_indicator(econ, code=code, is_additive=add, value_type=vt)
            make_observation(ind, lad, src, value=Decimal("1"),
                             period_start=date(2020, 1, 1), period_end=date(2020, 12, 31),
                             vintage=code)

        call_command("bootstrap_seed")
        names = [c.args[0] for c in cc.call_args_list]
        # No data loaders ran…
        for loader in ("seed_v1", "ingest_gva", "ingest_population", "derive_per_head", "ingest_hpi"):
            self.assertNotIn(loader, names, f"{loader} should have been skipped")
        # …but the admin was still ensured.
        self.assertIn("seed_admin", names)

    @mock.patch.dict("os.environ", {}, clear=True)
    @mock.patch("core.management.commands.bootstrap_seed.call_command")
    def test_no_superuser_password_means_no_seed_admin(self, cc):
        call_command("bootstrap_seed")
        names = [c.args[0] for c in cc.call_args_list]
        self.assertNotIn("seed_admin", names)
