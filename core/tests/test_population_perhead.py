"""
Phase 3 — population source + derived per-head.

- Population is a COUNT and additive: it rolls up (sums) across a constituency's
  LADs, exactly like GVA total.
- gva-per-head is derived (GVA total x 1e6 / population) at LAD level, stored
  against the Derived source with a dual-input vintage, and is non-additive so a
  crosswalk roll-up refuses it.
"""

from datetime import date
from decimal import Decimal

from django.core.management import call_command
from django.test import TestCase

from core.aggregation import NonAdditiveRollupError, rollup_place_value
from core.models import (
    CrosswalkBasis,
    Indicator,
    PlaceObservation,
    PlaceTier,
    ValueType,
)

from .factories import (
    make_crosswalk,
    make_domain,
    make_indicator,
    make_observation,
    make_place,
    make_source,
)


class PopulationRollupTests(TestCase):
    """Population (COUNT, additive) sums across a constituency's LADs."""

    def setUp(self):
        self.demography = make_domain("demography", "Demography")
        self.source = make_source("ONS mid-year population estimates")
        self.lad_a = make_place("E07000001", "LAD A", tier=PlaceTier.LAD)
        self.lad_b = make_place("E07000002", "LAD B", tier=PlaceTier.LAD)
        self.wpc = make_place("E14000001", "A Constituency", tier=PlaceTier.WPC)
        make_crosswalk(self.lad_a, self.wpc, Decimal("1.000000"), CrosswalkBasis.POPULATION)
        make_crosswalk(self.lad_b, self.wpc, Decimal("0.500000"), CrosswalkBasis.POPULATION)

    def test_population_rolls_up_by_summing(self):
        pop = make_indicator(
            self.demography, code="population",
            is_additive=True, value_type=ValueType.COUNT, unit="count",
        )
        make_observation(pop, self.lad_a, self.source, value=Decimal("100000"))
        make_observation(pop, self.lad_b, self.source, value=Decimal("40000"))
        total = rollup_place_value(
            pop, self.wpc,
            period_start=date(2021, 1, 1), period_end=date(2021, 12, 31),
            basis=CrosswalkBasis.POPULATION,
        )
        # 100000 * 1.0 + 40000 * 0.5 = 120000
        self.assertEqual(total, Decimal("120000"))


class PerHeadDerivationTests(TestCase):
    """derive_per_head materialises GVA total x 1e6 / population at LAD level."""

    def setUp(self):
        economy = make_domain("economy", "Economy")
        demography = make_domain("demography", "Demography")
        self.gva = make_indicator(
            economy, code="gva-balanced-total",
            is_additive=True, value_type=ValueType.CURRENCY, unit="£m",
        )
        self.pop = make_indicator(
            demography, code="population",
            is_additive=True, value_type=ValueType.COUNT, unit="count",
        )
        self.per_head = make_indicator(
            economy, code="gva-per-head",
            is_additive=False, value_type=ValueType.RATIO, unit="£",
        )
        self.place = make_place("E08000003", "Manchester", tier=PlaceTier.LAD)
        self.gva_src = make_source("ONS Regional accounts (GVA / GDP / GDHI)")
        self.pop_src = make_source("ONS mid-year population estimates")

        make_observation(self.gva, self.place, self.gva_src,
                         value=Decimal("22550"), vintage="2019-12-19",
                         period_start=date(2018, 1, 1), period_end=date(2018, 12, 31))
        make_observation(self.pop, self.place, self.pop_src,
                         value=Decimal("547627"), vintage="2020-06-mye",
                         period_start=date(2018, 1, 1), period_end=date(2018, 12, 31))

    def test_derivation_value_source_and_vintage(self):
        call_command("derive_per_head")
        obs = PlaceObservation.objects.get(indicator=self.per_head, place=self.place)
        # 22550m * 1e6 / 547627 = 41,177.7 -> 41178 (ROUND_HALF_UP)
        self.assertEqual(obs.value, Decimal("41178"))
        self.assertEqual(obs.unit, "£")
        self.assertEqual(obs.vintage, "gva:2019-12-19/pop:2020-06-mye")
        self.assertEqual(obs.source.name, "Derived — Currence engine")

    def test_derivation_is_idempotent(self):
        call_command("derive_per_head")
        call_command("derive_per_head")
        self.assertEqual(
            PlaceObservation.objects.filter(indicator=self.per_head).count(), 1
        )

    def test_derived_per_head_refuses_rollup(self):
        call_command("derive_per_head")
        other_lad = make_place("E08000002", "Another LAD", tier=PlaceTier.LAD)
        wpc = make_place("E14000009", "Constituency", tier=PlaceTier.WPC)
        make_crosswalk(self.place, wpc, Decimal("0.500000"), CrosswalkBasis.POPULATION)
        make_crosswalk(other_lad, wpc, Decimal("0.500000"), CrosswalkBasis.POPULATION)
        with self.assertRaises(NonAdditiveRollupError):
            rollup_place_value(
                self.per_head, wpc,
                period_start=date(2018, 1, 1), period_end=date(2018, 12, 31),
                basis=CrosswalkBasis.POPULATION,
            )
