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

from core.management.commands.bootstrap_seed import (
    GVA_VINTAGE_2025,
    PERHEAD_VINTAGE_2025,
    POP_VINTAGE,
    POP_VINTAGE_2025,
)
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

    def _seed_economy(self, gva_v, pop_v, perhead_v):
        econ = make_domain("economy", "Economy")
        src = make_source()
        lad = make_place("E08000003", "Manchester", tier=PlaceTier.LAD)
        specs = [
            ("gva-balanced-total", ValueType.CURRENCY, True, gva_v),
            ("population", ValueType.COUNT, True, pop_v),
            ("gva-per-head", ValueType.RATIO, False, perhead_v),
            ("average-house-price", ValueType.RATIO, False, "hpi"),
        ]
        for code, vt, add, vintage in specs:
            ind = make_indicator(econ, code=code, is_additive=add, value_type=vt)
            make_observation(ind, lad, src, value=Decimal("1"),
                             period_start=date(2020, 1, 1), period_end=date(2020, 12, 31),
                             vintage=vintage)

    @mock.patch.dict("os.environ", {"DJANGO_SUPERUSER_PASSWORD": "pw"})
    @mock.patch("core.management.commands.bootstrap_seed.call_command")
    def test_current_edition_db_loads_nothing_but_admin(self, cc):
        # A DB holding BOTH population editions and the 2025 GVA/per-head re-runs nothing.
        self._seed_economy(GVA_VINTAGE_2025, POP_VINTAGE_2025, PERHEAD_VINTAGE_2025)
        from core.models import Indicator
        make_observation(Indicator.objects.get(code="population"),
                         make_place("E08000001", "Bolton", tier=PlaceTier.LAD),
                         make_source("ONS 2020"), value=Decimal("1"),
                         period_start=date(2019, 1, 1), period_end=date(2019, 12, 31),
                         vintage=POP_VINTAGE)   # the older population edition too
        call_command("bootstrap_seed")
        names = [c.args[0] for c in cc.call_args_list]
        for loader in ("seed_v1", "ingest_gva", "ingest_population", "derive_per_head", "ingest_hpi"):
            self.assertNotIn(loader, names, f"{loader} should have been skipped")
        self.assertIn("seed_admin", names)

    @mock.patch.dict("os.environ", {"DJANGO_SUPERUSER_PASSWORD": "pw"})
    @mock.patch("core.management.commands.bootstrap_seed.call_command")
    def test_old_edition_db_triggers_staleness_refresh(self, cc):
        # A DB holding only the OLD editions MUST pull the 2025 refresh on deploy —
        # the whole point of the vintage-aware guards (staleness refresh reaches live).
        self._seed_economy("2019-12-19", POP_VINTAGE, "gva:2019-12-19/pop:2020-06-mye")
        call_command("bootstrap_seed")
        names = [c.args[0] for c in cc.call_args_list]
        self.assertIn("ingest_gva", names)          # 2025 GVA edition
        self.assertIn("ingest_population", names)    # 2025 population edition
        self.assertIn("derive_per_head", names)      # re-derive to extend per-head
        self.assertNotIn("ingest_hpi", names)        # HPI already present — untouched

    @mock.patch.dict("os.environ", {}, clear=True)
    @mock.patch("core.management.commands.bootstrap_seed.call_command")
    def test_no_superuser_password_means_no_seed_admin(self, cc):
        call_command("bootstrap_seed")
        names = [c.args[0] for c in cc.call_args_list]
        self.assertNotIn("seed_admin", names)
