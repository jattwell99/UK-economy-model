"""
Comparison-over-time tool — CONSERVATIVE path (docs/comparison_tool_scoping_brief.md).

Covers the multi-place assembler (shared axis, gap-breaking, provenance), the structural
"comparable" rules (non-additive only, single tier, single boundary era), and coverage
exclusion (a place outside the indicator's nations can't join, with the reason).
"""

from datetime import date
from decimal import Decimal

from django.test import TestCase

from core.models import PlaceTier, ValueType
from core.selectors import ComparisonError, comparison_series

from .factories import make_domain, make_indicator, make_observation, make_place, make_source


def _obs(ind, place, src, year, value):
    make_observation(ind, place, src, value=Decimal(value),
                     period_start=date(year, 1, 1), period_end=date(year, 12, 31),
                     period_type="CALENDAR_YEAR")


class ComparisonAssemblerTests(TestCase):
    def setUp(self):
        econ = make_domain("economy", "Economy")
        self.ind = make_indicator(econ, code="gva-per-head", is_additive=False,
                                  value_type=ValueType.RATIO, unit="£")
        self.src = make_source("ONS")
        self.a = make_place("E07000223", "Adur", tier=PlaceTier.LAD)
        self.b = make_place("E08000003", "Manchester", tier=PlaceTier.LAD)
        for yr, v in [(2016, 20), (2017, 22), (2018, 24)]:
            _obs(self.ind, self.a, self.src, yr, v)
        # B is MISSING 2017 -> the line must break, not interpolate.
        _obs(self.ind, self.b, self.src, 2016, 30)
        _obs(self.ind, self.b, self.src, 2018, 34)

    def test_shared_axis_and_gap_breaks(self):
        d = comparison_series("gva-per-head", "LAD", [("E07000223", None), ("E08000003", None)])
        self.assertEqual(d["periods"], ["2016", "2017", "2018"])
        by = {s["place_name"]: s["values"] for s in d["series"]}
        self.assertEqual(by["Adur"], [20.0, 22.0, 24.0])
        # Manchester's 2017 is None (gap) — NOT interpolated between 30 and 34.
        self.assertEqual(by["Manchester"], [30.0, None, 34.0])

    def test_latest_vintage_per_period(self):
        # A newer vintage for one period must win in the assembled series.
        make_observation(self.ind, self.a, self.src, value=Decimal("99"),
                         period_start=date(2018, 1, 1), period_end=date(2018, 12, 31),
                         period_type="CALENDAR_YEAR", vintage="2099-new")
        d = comparison_series("gva-per-head", "LAD", [("E07000223", None), ("E08000003", None)])
        by = {s["place_name"]: s["values"] for s in d["series"]}
        self.assertEqual(by["Adur"][-1], 99.0)

    def test_provenance_present(self):
        d = comparison_series("gva-per-head", "LAD", [("E07000223", None), ("E08000003", None)])
        self.assertTrue(d["provenance"])


class ComparisonRuleTests(TestCase):
    def setUp(self):
        econ = make_domain("economy", "Economy")
        civic = make_domain("civic", "Civic")
        self.src = make_source("mixed")
        self.pph = make_indicator(econ, code="gva-per-head", is_additive=False,
                                  value_type=ValueType.RATIO, unit="£")
        self.total = make_indicator(econ, code="gva-balanced-total", is_additive=True,
                                    value_type=ValueType.CURRENCY, unit="£m")
        self.turnout = make_indicator(civic, code="turnout", is_additive=False,
                                      value_type=ValueType.RATE, unit="%")
        self.lad_a = make_place("E07000223", "Adur", tier=PlaceTier.LAD)
        self.lad_b = make_place("E08000003", "Manchester", tier=PlaceTier.LAD)
        # A colliding WPC code in two eras.
        self.old = make_place("S14000021", "East Renfrewshire", tier=PlaceTier.WPC,
                              valid_from=date(2010, 5, 6), valid_to=date(2024, 7, 3))
        self.new = make_place("S14000021", "East Renfrewshire", tier=PlaceTier.WPC,
                              valid_from=date(2024, 7, 4))
        self.old2 = make_place("S14000027", "Na h-Eileanan an Iar", tier=PlaceTier.WPC,
                               valid_from=date(2010, 5, 6), valid_to=date(2024, 7, 3))
        for yr in (2016, 2018):
            _obs(self.pph, self.lad_a, self.src, yr, 20)
            _obs(self.pph, self.lad_b, self.src, yr, 30)
            _obs(self.total, self.lad_a, self.src, yr, 100)
            _obs(self.total, self.lad_b, self.src, yr, 200)
        for p in (self.old, self.new, self.old2):
            make_observation(self.turnout, p, self.src, value=Decimal("65"),
                             period_start=date(2019 if p is not self.new else 2024, 6, 1),
                             period_end=date(2019 if p is not self.new else 2024, 6, 1),
                             period_type="POINT")

    def test_additive_total_refused(self):
        with self.assertRaises(ComparisonError) as cm:
            comparison_series("gva-balanced-total", "LAD",
                              [("E07000223", None), ("E08000003", None)])
        self.assertEqual(cm.exception.status, 400)

    def test_fewer_than_two_places_refused(self):
        with self.assertRaises(ComparisonError):
            comparison_series("gva-per-head", "LAD", [("E07000223", None)])

    def test_mixed_tier_prevented(self):
        with self.assertRaises(ComparisonError) as cm:
            comparison_series("gva-per-head", "LAD",
                              [("E07000223", None), ("S14000021", date(2024, 7, 4))])
        self.assertEqual(cm.exception.status, 400)

    def test_mixed_boundary_era_prevented(self):
        with self.assertRaises(ComparisonError) as cm:
            comparison_series("turnout", "WPC",
                              [("S14000021", date(2010, 5, 6)), ("S14000021", date(2024, 7, 4))])
        self.assertEqual(cm.exception.status, 400)

    def test_same_era_wpc_comparison_ok(self):
        d = comparison_series("turnout", "WPC",
                              [("S14000021", date(2010, 5, 6)), ("S14000027", date(2010, 5, 6))])
        self.assertEqual(d["boundary"], "2010-05-06")
        self.assertEqual(len(d["series"]), 2)


class ComparisonCoverageTests(TestCase):
    def setUp(self):
        health = make_domain("health", "Health")
        self.le = make_indicator(health, code="life-expectancy-birth-female",
                                 is_additive=False, value_type=ValueType.RATIO, unit="years",
                                 name="Life expectancy at birth (female)")
        self.src = make_source("OHID Fingertips", "OHID")
        self.eng = make_place("E07000223", "Adur", tier=PlaceTier.LAD)
        self.eng2 = make_place("E08000003", "Manchester", tier=PlaceTier.LAD)
        self.wales = make_place("W06000019", "Blaenau Gwent", tier=PlaceTier.LAD)
        for p in (self.eng, self.eng2):
            make_observation(self.le, p, self.src, value=Decimal("83"),
                             period_start=date(2020, 1, 1), period_end=date(2022, 12, 31),
                             period_type="CALENDAR_YEAR")

    def test_non_covered_place_excluded_with_note(self):
        d = comparison_series("life-expectancy-birth-female", "LAD",
                              [("E07000223", None), ("E08000003", None), ("W06000019", None)])
        names = {s["place_name"] for s in d["series"]}
        self.assertEqual(names, {"Adur", "Manchester"})       # Wales dropped from the lines
        excluded = {n["place"]: n["note"] for n in d["coverage_notes"]}
        self.assertEqual(excluded, {"Blaenau Gwent": "England only"})
