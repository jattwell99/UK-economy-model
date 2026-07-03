"""
Phase 3 breadth — Source 1: UK House Price Index (housing).

Covers the monthly ingester and the non-additive guardrail for the renamed
`average-house-price` indicator.
"""

import io
import tempfile
from datetime import date
from decimal import Decimal

from django.core.management import call_command
from django.test import TestCase

from core.aggregation import NonAdditiveRollupError, rollup_place_value
from core.management.commands.ingest_hpi import _month_bounds, read_hpi_rows
from core.models import (
    CrosswalkBasis,
    Indicator,
    PeriodType,
    PlaceObservation,
    PlaceTier,
    ValueType,
)

from .factories import make_crosswalk, make_domain, make_indicator, make_place


CSV = """Date,Region_Name,Area_Code,Average_Price,Monthly_Change
1995-01-01,Manchester,E08000003,33806,
1995-02-01,Manchester,E08000003,34000,0.6
1995-01-01,Leeds,E08000035,40000,
2026-04-01,Manchester,E08000003,255000,0.3
1995-01-01,England,E92000001,53000,
1995-01-01,Blankville,E07009999,,
1995-01-01,Zeroville,E07009998,0,
"""


class HpiParserTests(TestCase):
    def test_month_bounds(self):
        self.assertEqual(_month_bounds(date(2026, 2, 15)), (date(2026, 2, 1), date(2026, 2, 28)))
        self.assertEqual(_month_bounds(date(2024, 2, 10)), (date(2024, 2, 1), date(2024, 2, 29)))  # leap
        self.assertEqual(_month_bounds(date(2025, 4, 3)), (date(2025, 4, 1), date(2025, 4, 30)))

    def test_reads_underscored_headers_and_skips_bad_rows(self):
        rows = list(read_hpi_rows(io.StringIO(CSV)))
        codes = [r[0] for r in rows]
        # blank price and zero price rows are dropped; everything else kept
        self.assertNotIn("E07009999", codes)
        self.assertNotIn("E07009998", codes)
        self.assertEqual(len(rows), 5)
        first = rows[0]
        self.assertEqual(first[0], "E08000003")
        self.assertEqual(first[2], date(1995, 1, 1))
        self.assertEqual(first[3], Decimal("33806"))

    def test_reads_camelcase_headers(self):
        camel = "Date,RegionName,AreaCode,AveragePrice\n1995-01-01,Manchester,E08000003,33806\n"
        rows = list(read_hpi_rows(io.StringIO(camel)))
        self.assertEqual(rows, [("E08000003", "Manchester", date(1995, 1, 1), Decimal("33806"))])


class HpiIngestTests(TestCase):
    def setUp(self):
        make_place("E08000003", "Manchester", tier=PlaceTier.LAD)
        make_place("E08000035", "Leeds", tier=PlaceTier.LAD)

    def _ingest(self):
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as fh:
            fh.write(CSV)
            path = fh.name
        call_command("ingest_hpi", path=path, vintage="2026-04")

    def test_ingest_creates_monthly_observations(self):
        self._ingest()
        ind = Indicator.objects.get(code="average-house-price")
        self.assertFalse(ind.is_additive)
        self.assertEqual(ind.value_type, ValueType.RATIO)

        obs = PlaceObservation.objects.filter(indicator=ind)
        # 4 valid LAD rows (3 Manchester + 1 Leeds); England row unmatched, blanks dropped
        self.assertEqual(obs.count(), 4)
        o = obs.get(place__gss_code="E08000003", period_start=date(1995, 1, 1))
        self.assertEqual(o.period_type, PeriodType.MONTH)
        self.assertEqual(o.period_end, date(1995, 1, 31))
        self.assertEqual(o.value, Decimal("33806"))
        self.assertEqual(o.vintage, "2026-04")
        self.assertEqual(o.source.name, "UK House Price Index")

    def test_ingest_is_idempotent(self):
        self._ingest()
        self._ingest()
        self.assertEqual(
            PlaceObservation.objects.filter(indicator__code="average-house-price").count(), 4
        )


class HpiRollupRefusalTests(TestCase):
    def test_average_house_price_refuses_rollup(self):
        housing = make_domain("housing", "Housing")
        ahp = make_indicator(
            housing, code="average-house-price",
            is_additive=False, value_type=ValueType.RATIO, unit="£",
        )
        lad = make_place("E08000003", "Manchester", tier=PlaceTier.LAD)
        wpc = make_place("E14000001", "A Constituency", tier=PlaceTier.WPC)
        make_crosswalk(lad, wpc, Decimal("0.500000"), CrosswalkBasis.POPULATION)
        with self.assertRaises(NonAdditiveRollupError):
            rollup_place_value(
                ahp, wpc,
                period_start=date(2026, 4, 1), period_end=date(2026, 4, 30),
                basis=CrosswalkBasis.POPULATION,
            )
