"""
Phase 3 breadth — Source 3: Nomis labour market.

Covers period parsing per dataset, the config's additive flags, a mocked ingest
(no network) with suppressed/unmatched handling, and roll-up behaviour.
"""

from datetime import date
from decimal import Decimal
from unittest import mock

from django.core.management import call_command
from django.test import TestCase

from core.aggregation import NonAdditiveRollupError, rollup_place_value
from core.management.commands.ingest_nomis import DATASETS, _period
from core.models import (
    CrosswalkBasis,
    PeriodType,
    PlaceObservation,
    PlaceTier,
    ValueType,
)

from .factories import make_crosswalk, make_domain, make_indicator, make_observation, make_place, make_source


class NomisPeriodTests(TestCase):
    def test_month(self):
        self.assertEqual(
            _period("month", {"DATE": "2026-05"}),
            (date(2026, 5, 1), date(2026, 5, 31), PeriodType.MONTH))

    def test_calendar_year(self):
        self.assertEqual(
            _period("year", {"DATE": "2025"}),
            (date(2025, 1, 1), date(2025, 12, 31), PeriodType.CALENDAR_YEAR))

    def test_rolling_jan_dec_is_calendar_year(self):
        self.assertEqual(
            _period("rolling", {"DATE_NAME": "Jan 2025-Dec 2025"}),
            (date(2025, 1, 1), date(2025, 12, 31), PeriodType.CALENDAR_YEAR))

    def test_rolling_non_calendar_is_point(self):
        ps, pe, pt = _period("rolling", {"DATE_NAME": "Apr 2024-Mar 2025"})
        self.assertEqual((ps, pe), (date(2024, 4, 1), date(2025, 3, 31)))
        self.assertEqual(pt, PeriodType.POINT)

    def test_config_additive_flags(self):
        self.assertTrue(DATASETS["claimant-count"]["additive"])
        for code in ("employment-rate-16-64", "median-weekly-pay", "jobs-density"):
            self.assertFalse(DATASETS[code]["additive"])


SAMPLE = [
    {"DATE": "2026-05", "DATE_NAME": "May 2026", "GEOGRAPHY_CODE": "E08000003",
     "GEOGRAPHY_NAME": "Manchester", "OBS_VALUE": "25550", "OBS_STATUS": "A"},
    {"DATE": "2026-05", "DATE_NAME": "May 2026", "GEOGRAPHY_CODE": "E06000001",
     "GEOGRAPHY_NAME": "Hartlepool", "OBS_VALUE": "2830", "OBS_STATUS": "A"},
    {"DATE": "2026-05", "DATE_NAME": "May 2026", "GEOGRAPHY_CODE": "E08000003",
     "GEOGRAPHY_NAME": "Manchester", "OBS_VALUE": "", "OBS_STATUS": "x"},   # suppressed
    {"DATE": "2026-05", "DATE_NAME": "May 2026", "GEOGRAPHY_CODE": "E06000063",
     "GEOGRAPHY_NAME": "Cumberland", "OBS_VALUE": "500", "OBS_STATUS": "A"},  # unmatched
]


class NomisIngestTests(TestCase):
    def setUp(self):
        make_place("E08000003", "Manchester", tier=PlaceTier.LAD)
        make_place("E06000001", "Hartlepool", tier=PlaceTier.LAD)
        make_indicator(make_domain("labour-market", "Labour market"),
                       code="claimant-count", is_additive=True,
                       value_type=ValueType.COUNT, unit="count")

    @mock.patch("core.management.commands.ingest_nomis.Command._fetch", return_value=SAMPLE)
    def test_ingest_skips_suppressed_and_unmatched(self, _fetch):
        call_command("ingest_nomis", only="claimant-count", vintage="test-v")
        obs = PlaceObservation.objects.filter(indicator__code="claimant-count")
        self.assertEqual(obs.count(), 2)   # suppressed + unmatched dropped
        o = obs.get(place__gss_code="E08000003")
        self.assertEqual(o.value, Decimal("25550"))
        self.assertEqual(o.period_type, PeriodType.MONTH)
        self.assertEqual(o.period_start, date(2026, 5, 1))
        self.assertEqual(o.vintage, "test-v")
        self.assertEqual(o.source.name, "Nomis")

    @mock.patch("core.management.commands.ingest_nomis.Command._fetch", return_value=SAMPLE)
    def test_ingest_idempotent(self, _fetch):
        call_command("ingest_nomis", only="claimant-count", vintage="test-v")
        call_command("ingest_nomis", only="claimant-count", vintage="test-v")
        self.assertEqual(
            PlaceObservation.objects.filter(indicator__code="claimant-count").count(), 2)


class NomisRollupTests(TestCase):
    def setUp(self):
        self.dom = make_domain("labour-market", "Labour market")
        self.src = make_source("Nomis")
        self.lad_a = make_place("E08000003", "A", tier=PlaceTier.LAD)
        self.lad_b = make_place("E06000001", "B", tier=PlaceTier.LAD)
        self.wpc = make_place("E14000001", "C", tier=PlaceTier.WPC)
        make_crosswalk(self.lad_a, self.wpc, Decimal("1.000000"), CrosswalkBasis.POPULATION)
        make_crosswalk(self.lad_b, self.wpc, Decimal("0.500000"), CrosswalkBasis.POPULATION)

    def test_claimant_count_rolls_up(self):
        cc = make_indicator(self.dom, code="claimant-count", is_additive=True,
                            value_type=ValueType.COUNT, unit="count")
        make_observation(cc, self.lad_a, self.src, value=Decimal("1000"),
                         period_start=date(2026, 5, 1), period_end=date(2026, 5, 31))
        make_observation(cc, self.lad_b, self.src, value=Decimal("400"),
                         period_start=date(2026, 5, 1), period_end=date(2026, 5, 31))
        total = rollup_place_value(cc, self.wpc, period_start=date(2026, 5, 1),
                                   period_end=date(2026, 5, 31), basis=CrosswalkBasis.POPULATION)
        self.assertEqual(total, Decimal("1200"))  # 1000*1.0 + 400*0.5

    def test_rates_refuse_rollup(self):
        for code, vt in [("employment-rate-16-64", ValueType.RATE),
                         ("median-weekly-pay", ValueType.RATIO),
                         ("jobs-density", ValueType.RATIO)]:
            ind = make_indicator(self.dom, code=code, is_additive=False, value_type=vt)
            make_observation(ind, self.lad_a, self.src, value=Decimal("70"),
                             period_start=date(2025, 1, 1), period_end=date(2025, 12, 31))
            with self.assertRaises(NonAdditiveRollupError):
                rollup_place_value(ind, self.wpc, period_start=date(2025, 1, 1),
                                   period_end=date(2025, 12, 31), basis=CrosswalkBasis.POPULATION)
