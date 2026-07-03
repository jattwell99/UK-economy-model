"""
CLAUDE.md: "Crosswalk roll-up: additive indicators sum; non-additive ones raise,
not sum." This is the load-bearing guarantee behind is_additive.
"""

from datetime import date
from decimal import Decimal

from django.test import TestCase

from core.aggregation import NonAdditiveRollupError, rollup_place_value
from core.models import CrosswalkBasis, PlaceTier, ValueType

from .factories import (
    make_crosswalk,
    make_domain,
    make_indicator,
    make_observation,
    make_place,
    make_source,
)


class CrosswalkRollupTests(TestCase):
    def setUp(self):
        self.domain = make_domain()
        self.source = make_source()
        # Two LADs feeding one constituency.
        self.lad_a = make_place("E07000001", "LAD A", tier=PlaceTier.LAD)
        self.lad_b = make_place("E07000002", "LAD B", tier=PlaceTier.LAD)
        self.wpc = make_place("E14000001", "Constituency", tier=PlaceTier.WPC)
        # 60% of LAD A and 25% of LAD B fall in the constituency.
        make_crosswalk(self.lad_a, self.wpc, Decimal("0.600000"), CrosswalkBasis.POPULATION)
        make_crosswalk(self.lad_b, self.wpc, Decimal("0.250000"), CrosswalkBasis.POPULATION)

    def test_additive_indicator_sums_apportioned(self):
        gva = make_indicator(
            self.domain, code="gva-balanced-total",
            is_additive=True, value_type=ValueType.CURRENCY,
        )
        make_observation(gva, self.lad_a, self.source, value=Decimal("1000"))
        make_observation(gva, self.lad_b, self.source, value=Decimal("400"))

        total = rollup_place_value(
            gva, self.wpc,
            period_start=date(2021, 1, 1), period_end=date(2021, 12, 31),
            basis=CrosswalkBasis.POPULATION,
        )
        # 1000 * 0.6 + 400 * 0.25 = 700
        self.assertEqual(total, Decimal("700"))

    def test_non_additive_indicator_raises_not_sums(self):
        per_head = make_indicator(
            self.domain, code="gva-per-head",
            is_additive=False, value_type=ValueType.RATIO, unit="£",
        )
        make_observation(per_head, self.lad_a, self.source, value=Decimal("30000"))
        make_observation(per_head, self.lad_b, self.source, value=Decimal("28000"))

        with self.assertRaises(NonAdditiveRollupError):
            rollup_place_value(
                per_head, self.wpc,
                period_start=date(2021, 1, 1), period_end=date(2021, 12, 31),
                basis=CrosswalkBasis.POPULATION,
            )

    def test_rollup_returns_none_when_no_observations(self):
        gva = make_indicator(self.domain, code="gva-balanced-total", is_additive=True)
        result = rollup_place_value(
            gva, self.wpc,
            period_start=date(2021, 1, 1), period_end=date(2021, 12, 31),
            basis=CrosswalkBasis.POPULATION,
        )
        self.assertIsNone(result)
