"""
Phase 2 — the GVA vertical, go/no-go guarantees.

(a) The crosswalk rolls up an *additive* indicator (gva-balanced-total) across the
    LADs of a constituency by summing apportioned values, and *refuses* to sum a
    non-additive one (gva-per-head).
(b) Two vintages of the same place/period/indicator coexist as distinct rows,
    while an identical duplicate is rejected by the uniqueness constraint — this
    is how an ONS restatement is preserved rather than overwriting the prior edition.
"""

from datetime import date
from decimal import Decimal

from django.db import IntegrityError, transaction
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


class GvaCrosswalkRollupTests(TestCase):
    """(a) Additive GVA total sums across a constituency's LADs; per-head refuses."""

    def setUp(self):
        self.economy = make_domain("economy", "Economy")
        self.source = make_source()
        # Two LADs overlapping one Westminster constituency.
        self.lad_a = make_place("E07000001", "LAD A", tier=PlaceTier.LAD)
        self.lad_b = make_place("E07000002", "LAD B", tier=PlaceTier.LAD)
        self.wpc = make_place("E14000001", "A Constituency", tier=PlaceTier.WPC)
        make_crosswalk(self.lad_a, self.wpc, Decimal("0.500000"), CrosswalkBasis.POPULATION)
        make_crosswalk(self.lad_b, self.wpc, Decimal("0.250000"), CrosswalkBasis.POPULATION)

    def test_gva_total_rolls_up_by_summing(self):
        gva_total = make_indicator(
            self.economy, code="gva-balanced-total",
            is_additive=True, value_type=ValueType.CURRENCY, unit="£m",
        )
        make_observation(gva_total, self.lad_a, self.source, value=Decimal("5000"))
        make_observation(gva_total, self.lad_b, self.source, value=Decimal("2000"))

        total = rollup_place_value(
            gva_total, self.wpc,
            period_start=date(2021, 1, 1), period_end=date(2021, 12, 31),
            basis=CrosswalkBasis.POPULATION,
        )
        # 5000 * 0.5 + 2000 * 0.25 = 3000
        self.assertEqual(total, Decimal("3000"))

    def test_gva_per_head_refuses_to_roll_up(self):
        gva_per_head = make_indicator(
            self.economy, code="gva-per-head",
            is_additive=False, value_type=ValueType.RATIO, unit="£",
        )
        make_observation(gva_per_head, self.lad_a, self.source, value=Decimal("32000"))
        make_observation(gva_per_head, self.lad_b, self.source, value=Decimal("29000"))

        with self.assertRaises(NonAdditiveRollupError):
            rollup_place_value(
                gva_per_head, self.wpc,
                period_start=date(2021, 1, 1), period_end=date(2021, 12, 31),
                basis=CrosswalkBasis.POPULATION,
            )


class GvaVintageTests(TestCase):
    """(b) Restatements coexist by vintage; exact duplicates are rejected."""

    def setUp(self):
        self.gva_total = make_indicator(
            make_domain("economy", "Economy"), code="gva-balanced-total",
            is_additive=True, value_type=ValueType.CURRENCY, unit="£m",
        )
        self.source = make_source()
        self.place = make_place("E07000001", "LAD A", tier=PlaceTier.LAD)

    def test_two_vintages_coexist(self):
        make_observation(self.gva_total, self.place, self.source,
                         value=Decimal("5000.0"), vintage="2019-12-19")
        make_observation(self.gva_total, self.place, self.source,
                         value=Decimal("5123.4"), vintage="2020-12-16")  # restatement
        rows = self.place.observations.filter(indicator=self.gva_total)
        self.assertEqual(rows.count(), 2)
        self.assertEqual(
            {r.vintage for r in rows}, {"2019-12-19", "2020-12-16"},
        )

    def test_identical_duplicate_rejected(self):
        make_observation(self.gva_total, self.place, self.source,
                         value=Decimal("5000.0"), vintage="2019-12-19")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                make_observation(self.gva_total, self.place, self.source,
                                 value=Decimal("9999.9"), vintage="2019-12-19")
