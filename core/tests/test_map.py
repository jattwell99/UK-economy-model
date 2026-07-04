"""
Choropleth endpoint (docs/map_timeslider_brief.md §4, §8).

Covers: latest-vintage-per-period value selection, the two kinds of no_data
(nation-absence AND within-coverage holes), additive totals refused, the picker
excluding additive, and the coverage block.
"""

from datetime import date
from decimal import Decimal

from django.test import TestCase

from core.models import PlaceTier, ValueType
from core.selectors import (
    ChoroplethError,
    available_years,
    choropleth_data,
    mappable_indicators,
    resolve_place,
)

from .factories import make_domain, make_indicator, make_observation, make_place, make_source


class ChoroplethValueTests(TestCase):
    def setUp(self):
        econ = make_domain("economy", "Economy")
        self.ind = make_indicator(econ, code="gva-per-head", is_additive=False,
                                  value_type=ValueType.RATIO, unit="£")
        self.src = make_source("ONS")
        self.p = make_place("E07000223", "Adur", tier=PlaceTier.LAD)

    def test_latest_vintage_wins_for_the_period(self):
        # Same place/period, two vintages — the newest must be the mapped value.
        for vintage, val in [("2020-old", 100), ("2021-new", 130)]:
            make_observation(self.ind, self.p, self.src, value=Decimal(val),
                             period_start=date(2018, 1, 1), period_end=date(2018, 12, 31),
                             period_type="CALENDAR_YEAR", vintage=vintage)
        data = choropleth_data("gva-per-head", tier=PlaceTier.LAD, period="2018")
        self.assertEqual(data["values"]["E07000223"], 130.0)
        self.assertEqual(data["period"], "2018")
        self.assertEqual(data["scale"], {"min": 130.0, "max": 130.0})
        self.assertFalse(data["is_additive"])

    def test_period_defaults_to_latest(self):
        make_observation(self.ind, self.p, self.src, value=Decimal(50),
                         period_start=date(2016, 1, 1), period_end=date(2016, 12, 31))
        make_observation(self.ind, self.p, self.src, value=Decimal(60),
                         period_start=date(2018, 1, 1), period_end=date(2018, 12, 31))
        data = choropleth_data("gva-per-head", tier=PlaceTier.LAD)
        self.assertEqual(data["period"], "2018")
        self.assertEqual(data["values"]["E07000223"], 60.0)


class ChoroplethNoDataTests(TestCase):
    """no_data folds in BOTH nation-absence and within-coverage holes (§8.1).

    Vehicle is an England-only indicator (IMD average score) — life expectancy is no
    longer partial now that the ONS UK release supplies all four nations.
    """

    def setUp(self):
        depr = make_domain("deprivation", "Deprivation")
        self.imd = make_indicator(depr, code="imd-average-score-england",
                                  is_additive=False, value_type=ValueType.INDEX, unit="score",
                                  name="IMD: population-weighted average score (England)")
        self.src = make_source("MHCLG")
        self.eng = make_place("E07000223", "Adur", tier=PlaceTier.LAD)          # has data
        self.eng_hole = make_place("E07000224", "Arun", tier=PlaceTier.LAD)     # English, no data
        self.wales = make_place("W06000019", "Blaenau Gwent", tier=PlaceTier.LAD)  # nation-absent
        make_observation(self.imd, self.eng, self.src, value=Decimal("22.5"),
                         period_start=date(2019, 1, 1), period_end=date(2019, 12, 31),
                         period_type="CALENDAR_YEAR")

    def test_both_kinds_of_absence_are_no_data(self):
        data = choropleth_data("imd-average-score-england", tier=PlaceTier.LAD)
        self.assertIn("E07000223", data["values"])
        self.assertNotIn("E07000223", data["no_data"])
        # nation-absence: Wales greyed
        self.assertIn("W06000019", data["no_data"])
        # within-coverage hole: English LAD with no observation greyed too
        self.assertIn("E07000224", data["no_data"])

    def test_coverage_block_reports_england_only(self):
        data = choropleth_data("imd-average-score-england", tier=PlaceTier.LAD)
        self.assertEqual(data["coverage"], {"nations": ["E"], "note": "England only"})


class ChoroplethLifeExpectancyUkWideTests(TestCase):
    """Life expectancy is UK-wide now: no coverage note, and a Welsh place with an
    ONS observation shades rather than being greyed as nation-absent."""

    def setUp(self):
        health = make_domain("health", "Health")
        self.le = make_indicator(health, code="life-expectancy-birth-female",
                                 is_additive=False, value_type=ValueType.RATIO, unit="years",
                                 name="Life expectancy at birth (female)")
        self.src = make_source("ONS life expectancy for local areas", "ONS")
        self.eng = make_place("E07000223", "Adur", tier=PlaceTier.LAD)
        self.wales = make_place("W06000019", "Blaenau Gwent", tier=PlaceTier.LAD)
        for p, v in [(self.eng, "83.1"), (self.wales, "82.7")]:
            make_observation(self.le, p, self.src, value=Decimal(v),
                             period_start=date(2022, 1, 1), period_end=date(2024, 12, 31),
                             period_type="CALENDAR_YEAR")

    def test_no_coverage_note(self):
        data = choropleth_data("life-expectancy-birth-female", tier=PlaceTier.LAD)
        self.assertEqual(data["coverage"], {"nations": None, "note": None})

    def test_welsh_place_is_shaded_not_greyed(self):
        data = choropleth_data("life-expectancy-birth-female", tier=PlaceTier.LAD)
        self.assertIn("W06000019", data["values"])
        self.assertNotIn("W06000019", data["no_data"])


class ChoroplethAdditiveGuardTests(TestCase):
    def setUp(self):
        econ = make_domain("economy", "Economy")
        make_indicator(econ, code="gva-balanced-total", is_additive=True,
                       value_type=ValueType.CURRENCY, unit="£m")
        pph = make_indicator(econ, code="gva-per-head", is_additive=False,
                             value_type=ValueType.RATIO, unit="£")
        p = make_place("E07000223", "Adur", tier=PlaceTier.LAD)
        make_observation(pph, p, make_source("ONS"), value=Decimal("28150"),
                         period_start=date(2018, 1, 1), period_end=date(2018, 12, 31))

    def test_additive_total_is_refused(self):
        with self.assertRaises(ChoroplethError) as cm:
            choropleth_data("gva-balanced-total", tier=PlaceTier.LAD)
        self.assertEqual(cm.exception.status, 400)

    def test_unknown_indicator_is_404(self):
        with self.assertRaises(ChoroplethError) as cm:
            choropleth_data("does-not-exist", tier=PlaceTier.LAD)
        self.assertEqual(cm.exception.status, 404)

    def test_picker_excludes_additive_totals(self):
        codes = [i["code"] for i in mappable_indicators(PlaceTier.LAD)]
        self.assertIn("gva-per-head", codes)
        self.assertNotIn("gva-balanced-total", codes)


class ChoroplethScaleTests(TestCase):
    """Quantile breaks give contrast + honest per-band value ranges (colour-scale fix)."""

    def setUp(self):
        econ = make_domain("economy", "Economy")
        self.ind = make_indicator(econ, code="gva-per-head", is_additive=False,
                                  value_type=ValueType.RATIO, unit="£")
        self.src = make_source("ONS")
        # One extreme outlier (City-of-London-like) + a tight cluster below it.
        vals = [10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 5000]
        for i, v in enumerate(vals):
            p = make_place("E0700%04d" % i, "LAD%d" % i, tier=PlaceTier.LAD)
            make_observation(self.ind, p, self.src, value=Decimal(v),
                             period_start=date(2018, 1, 1), period_end=date(2018, 12, 31))

    def test_breaks_are_quantiles_not_linear(self):
        d = choropleth_data("gva-per-head", tier=PlaceTier.LAD)
        b = d["breaks"]
        self.assertGreaterEqual(len(b), 2)
        self.assertEqual(b[0], 10.0)          # min
        self.assertEqual(b[-1], 5000.0)       # max (the outlier is the top edge)
        # Quantile: the second-highest edge is far below the outlier, so the non-outlier
        # LADs spread across bands instead of all collapsing into the palest one.
        self.assertLess(b[-2], 100)
        # Edges are strictly increasing (deduped) — every band is a real value range.
        self.assertEqual(sorted(set(b)), b)


class ChoroplethTierTests(TestCase):
    """Tier param swaps place set; picker is per-tier and non-additive only."""

    def setUp(self):
        econ = make_domain("economy", "Economy")
        civic = make_domain("civic", "Civic & democratic")
        self.src = make_source("mixed")
        # LAD indicator
        pph = make_indicator(econ, code="gva-per-head", is_additive=False,
                             value_type=ValueType.RATIO, unit="£")
        # WPC indicators: turnout (non-additive), majority (additive)
        turnout = make_indicator(civic, code="turnout", is_additive=False,
                                 value_type=ValueType.RATE, unit="%")
        make_indicator(civic, code="majority", is_additive=True,
                       value_type=ValueType.COUNT, unit="count")
        lad = make_place("E07000223", "Adur", tier=PlaceTier.LAD)
        wpc = make_place("E14001305", "Islington North", tier=PlaceTier.WPC,
                         valid_from=date(2024, 7, 4))
        make_observation(pph, lad, self.src, value=Decimal("28150"),
                         period_start=date(2018, 1, 1), period_end=date(2018, 12, 31))
        make_observation(turnout, wpc, self.src, value=Decimal("67.5"),
                         period_start=date(2024, 7, 4), period_end=date(2024, 7, 4),
                         period_type="POINT")

    def test_wpc_tier_returns_constituency_values(self):
        d = choropleth_data("turnout", tier=PlaceTier.WPC)
        self.assertIn("E14001305", d["values"])
        self.assertEqual(d["tier"], PlaceTier.WPC)
        self.assertNotIn("E07000223", d["values"])   # LAD place absent from WPC map

    def test_picker_is_per_tier(self):
        lad_codes = [i["code"] for i in mappable_indicators(PlaceTier.LAD)]
        wpc_codes = [i["code"] for i in mappable_indicators(PlaceTier.WPC)]
        # Civic lives at WPC, economy at LAD — no cross-tier leakage.
        self.assertIn("gva-per-head", lad_codes)
        self.assertNotIn("turnout", lad_codes)
        self.assertIn("turnout", wpc_codes)
        self.assertNotIn("gva-per-head", wpc_codes)
        # Additive majority excluded even though it's a WPC indicator.
        self.assertNotIn("majority", wpc_codes)


class ChoroplethSliderTests(TestCase):
    """Time slider: period lists per indicator, single-point handling, and the
    per-period date-window boundary resolver (brief §5, §8.3)."""

    OLD_FROM, NEW_FROM = date(2010, 5, 6), date(2024, 7, 4)

    def setUp(self):
        econ = make_domain("economy", "Economy")
        civic = make_domain("civic", "Civic")
        community = make_domain("community", "Community")
        self.src = make_source("mixed")
        self.pph = make_indicator(econ, code="gva-per-head", is_additive=False,
                                  value_type=ValueType.RATIO, unit="£")
        self.turnout = make_indicator(civic, code="turnout", is_additive=False,
                                      value_type=ValueType.RATE, unit="%")
        self.imd = make_indicator(community, code="imd-average-score-england",
                                  is_additive=False, value_type=ValueType.INDEX, unit="score",
                                  name="IMD score (England)")
        # LAD multi-year economy
        lad = make_place("E07000223", "Adur", tier=PlaceTier.LAD)
        for yr, v in [(2016, 50), (2017, 55), (2018, 60)]:
            make_observation(self.pph, lad, self.src, value=Decimal(v),
                             period_start=date(yr, 1, 1), period_end=date(yr, 12, 31))
        make_observation(self.imd, lad, self.src, value=Decimal("22"),
                         period_start=date(2019, 9, 26), period_end=date(2019, 9, 26),
                         period_type="POINT")
        # A constituency that exists in BOTH boundary eras (colliding Scottish-style code).
        self.old = make_place("S14000021", "East Renfrewshire", tier=PlaceTier.WPC,
                              valid_from=self.OLD_FROM, valid_to=date(2024, 7, 3))
        self.new = make_place("S14000021", "East Renfrewshire", tier=PlaceTier.WPC,
                              valid_from=self.NEW_FROM)
        for yr, place, v in [(2015, self.old, 81), (2019, self.old, 77), (2024, self.new, 67)]:
            make_observation(self.turnout, place, self.src, value=Decimal(v),
                             period_start=date(yr, 6, 1), period_end=date(yr, 6, 1),
                             period_type="POINT")

    def test_period_list_spans_boundary_eras(self):
        self.assertEqual(available_years(self.turnout, PlaceTier.WPC),
                         ["2015", "2019", "2024"])
        self.assertEqual(available_years(self.pph, PlaceTier.LAD),
                         ["2016", "2017", "2018"])

    def test_single_point_indicator(self):
        d = choropleth_data("imd-average-score-england", tier=PlaceTier.LAD)
        self.assertEqual(d["periods"], ["2019"])   # client hides the slider on len==1

    def test_response_carries_periods_and_layer(self):
        d = choropleth_data("gva-per-head", tier=PlaceTier.LAD)
        self.assertEqual(d["layer"], "lad")
        self.assertIn("periods", d)

    def test_historic_period_uses_old_boundary_layer_and_values(self):
        d = choropleth_data("turnout", tier=PlaceTier.WPC, period="2019")
        self.assertEqual(d["layer"], "wpc-2010")            # old geometry
        self.assertEqual(d["values"]["S14000021"], 77.0)    # old seat's 2019 value

    def test_current_period_uses_new_boundary_layer_and_values(self):
        d = choropleth_data("turnout", tier=PlaceTier.WPC, period="2024")
        self.assertEqual(d["layer"], "wpc-2024")            # 2024 geometry
        self.assertEqual(d["values"]["S14000021"], 67.0)    # 2024 seat's value

    def test_colliding_code_resolves_per_era_not_bleeding(self):
        old = choropleth_data("turnout", tier=PlaceTier.WPC, period="2019")
        new = choropleth_data("turnout", tier=PlaceTier.WPC, period="2024")
        self.assertNotEqual(old["values"]["S14000021"], new["values"]["S14000021"])
        self.assertNotEqual(old["layer"], new["layer"])

    def test_click_boundary_lands_on_the_on_screen_version(self):
        # The `boundary` a period returns is what the click carries into the versioned
        # URL — resolving it must land on the SAME era the map is showing, for the
        # colliding Scottish code (2019 -> old seat, 2024 -> new seat).
        old = choropleth_data("turnout", tier=PlaceTier.WPC, period="2019")
        new = choropleth_data("turnout", tier=PlaceTier.WPC, period="2024")
        self.assertEqual(old["boundary"], "2010-05-06")
        self.assertEqual(new["boundary"], "2024-07-04")
        p_old = resolve_place("S14000021", valid_from=date.fromisoformat(old["boundary"]))
        p_new = resolve_place("S14000021", valid_from=date.fromisoformat(new["boundary"]))
        self.assertEqual(p_old.valid_from, self.OLD_FROM)
        self.assertEqual(p_old.valid_to, date(2024, 7, 3))
        self.assertEqual(p_new.valid_from, self.NEW_FROM)
        self.assertIsNone(p_new.valid_to)


class ChoroplethApiViewTests(TestCase):
    def setUp(self):
        econ = make_domain("economy", "Economy")
        make_indicator(econ, code="gva-balanced-total", is_additive=True,
                       value_type=ValueType.CURRENCY, unit="£m")
        ind = make_indicator(econ, code="gva-per-head", is_additive=False,
                             value_type=ValueType.RATIO, unit="£")
        p = make_place("E07000223", "Adur", tier=PlaceTier.LAD)
        make_observation(ind, p, make_source("ONS"), value=Decimal("28150"),
                         period_start=date(2018, 1, 1), period_end=date(2018, 12, 31))

    def test_endpoint_returns_json_shape(self):
        r = self.client.get("/api/choropleth/?indicator=gva-per-head&tier=LAD")
        self.assertEqual(r.status_code, 200)
        d = r.json()
        for key in ("values", "unit", "value_type", "is_additive", "scale", "breaks", "coverage", "no_data"):
            self.assertIn(key, d)
        self.assertEqual(d["values"]["E07000223"], 28150.0)

    def test_endpoint_400_for_additive(self):
        r = self.client.get("/api/choropleth/?indicator=gva-balanced-total&tier=LAD")
        self.assertEqual(r.status_code, 400)
        self.assertIn("error", r.json())

    def test_endpoint_404_for_unknown(self):
        r = self.client.get("/api/choropleth/?indicator=nope&tier=LAD")
        self.assertEqual(r.status_code, 404)


class DefaultPeriodBroadCoverageTests(TestCase):
    """The default slider tick is the latest year with broad coverage, not the latest
    year that has any data (docs: LE 2023-25 England straggler must not be the default)."""

    def setUp(self):
        health = make_domain("health", "Health")
        self.le = make_indicator(health, code="life-expectancy-birth-male",
                                 is_additive=False, value_type=ValueType.RATIO, unit="years")
        self.src = make_source("ONS")
        # 10 places with data in the "broad" year, only 1 in the later straggler year.
        self.places = [make_place(f"E07{i:06d}", f"P{i}", tier=PlaceTier.LAD) for i in range(10)]
        for p in self.places:
            make_observation(self.le, p, self.src, value=Decimal("80"),
                             period_start=date(2022, 1, 1), period_end=date(2024, 12, 31),
                             period_type="CALENDAR_YEAR")
        # One straggler with a later, lonelier period.
        make_observation(self.le, self.places[0], self.src, value=Decimal("81"),
                         period_start=date(2023, 1, 1), period_end=date(2025, 12, 31),
                         period_type="CALENDAR_YEAR")

    def test_default_skips_the_straggler_year(self):
        d = choropleth_data("life-expectancy-birth-male", tier=PlaceTier.LAD)
        self.assertEqual(d["period"], "2022")          # broad year, not 2023
        self.assertEqual(len(d["values"]), 10)         # all ten shaded
        self.assertIn("2023", d["periods"])            # straggler still a slider tick

    def test_explicit_straggler_period_still_reachable(self):
        d = choropleth_data("life-expectancy-birth-male", tier=PlaceTier.LAD, period="2023")
        self.assertEqual(d["period"], "2023")
        self.assertEqual(len(d["values"]), 1)          # honesty: only the straggler

    def test_full_coverage_defaults_to_latest(self):
        # If every year is fully covered, the default is simply the latest year.
        for p in self.places:
            make_observation(self.le, p, self.src, value=Decimal("82"),
                             period_start=date(2023, 1, 1), period_end=date(2025, 12, 31),
                             period_type="CALENDAR_YEAR", vintage="2024-later")
        d = choropleth_data("life-expectancy-birth-male", tier=PlaceTier.LAD)
        self.assertEqual(d["period"], "2023")
