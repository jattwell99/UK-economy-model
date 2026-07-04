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

    def _seed_unitary_backfilled(self):
        """Seed the Somerset sentinel (E06000066) with an observation for every indicator
        the LAD-refresh backfill guards on, so both the spine refresh and the backfill skip
        — i.e. a spine that has already been refreshed. Vintage 'unit-test' so it never
        satisfies the staleness (2025-vintage) guards."""
        from core.models import Indicator, IndicatorDomain
        som = make_place("E06000066", "Somerset", tier=PlaceTier.LAD, valid_from=date(2023, 4, 1))
        src = make_source("backfilled", "x")
        specs = [("gva-balanced-total", "economy"), ("population", "economy"),
                 ("gva-per-head", "economy"), ("gdhi-total", "economy"),
                 ("average-house-price", "economy"), ("claimant-count", "labour-market"),
                 ("employment-rate-16-64", "labour-market"), ("median-weekly-pay", "labour-market"),
                 ("jobs-density", "labour-market"), ("life-expectancy-birth-male", "health")]
        for code, dom_code in specs:
            ind = Indicator.objects.filter(code=code).first()
            if ind is None:
                dom, _ = IndicatorDomain.objects.get_or_create(code=dom_code, defaults={"name": dom_code})
                ind = make_indicator(dom, code=code, is_additive=False, value_type=ValueType.RATIO)
            make_observation(ind, som, src, value=Decimal("1"),
                             period_start=date(2023, 1, 1), period_end=date(2023, 12, 31),
                             vintage="unit-test")

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
        # A DB holding BOTH population editions, the 2025 GVA/per-head, AND a refreshed LAD
        # spine (unitaries backfilled) re-runs nothing.
        self._seed_economy(GVA_VINTAGE_2025, POP_VINTAGE_2025, PERHEAD_VINTAGE_2025)
        from core.models import Indicator
        make_observation(Indicator.objects.get(code="population"),
                         make_place("E08000001", "Bolton", tier=PlaceTier.LAD),
                         make_source("ONS 2020"), value=Decimal("1"),
                         period_start=date(2019, 1, 1), period_end=date(2019, 12, 31),
                         vintage=POP_VINTAGE)   # the older population edition too
        self._seed_unitary_backfilled()        # spine already refreshed -> refresh + backfill skip
        call_command("bootstrap_seed")
        names = [c.args[0] for c in cc.call_args_list]
        # These are the economy/spine loaders this test seeds for; deprivation / Nomis / LE
        # aren't seeded here so their normal steps still run (unrelated to this assertion).
        for loader in ("seed_v1", "refresh_lad_spine", "ingest_gva", "ingest_population",
                       "derive_per_head", "ingest_hpi"):
            self.assertNotIn(loader, names, f"{loader} should have been skipped")
        self.assertIn("seed_admin", names)

    @mock.patch.dict("os.environ", {"DJANGO_SUPERUSER_PASSWORD": "pw"})
    @mock.patch("core.management.commands.bootstrap_seed.call_command")
    def test_old_edition_db_triggers_staleness_refresh(self, cc):
        # A DB holding only the OLD editions MUST pull the 2025 refresh on deploy —
        # the whole point of the vintage-aware guards (staleness refresh reaches live).
        self._seed_economy("2019-12-19", POP_VINTAGE, "gva:2019-12-19/pop:2020-06-mye")
        self._seed_unitary_backfilled()             # isolate: spine refreshed, so no LAD backfill
        call_command("bootstrap_seed")
        names = [c.args[0] for c in cc.call_args_list]
        self.assertIn("ingest_gva", names)          # 2025 GVA edition (staleness guard)
        self.assertIn("ingest_population", names)    # 2025 population edition
        self.assertIn("derive_per_head", names)      # re-derive to extend per-head
        self.assertNotIn("ingest_hpi", names)        # HPI present + spine refreshed — untouched

    @mock.patch.dict("os.environ", {}, clear=True)
    @mock.patch("core.management.commands.bootstrap_seed.call_command")
    def test_no_superuser_password_means_no_seed_admin(self, cc):
        call_command("bootstrap_seed")
        names = [c.args[0] for c in cc.call_args_list]
        self.assertNotIn("seed_admin", names)
