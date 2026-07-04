"""
Phase 4c (deprivation) — English IoD, both metrics at the LAD tier.

Covers the two aggregations (decile-share and population-weighted score), the
concentration-vs-level divergence that motivated keeping both, LAD matching /
unmatched reporting, the England-only coverage note, and the chart descriptors.
LSOAs are aggregated through the file; they are never Place rows.
"""

import tempfile
from datetime import date
from decimal import Decimal

from django.core.management import call_command
from django.test import TestCase

from core.models import PeriodType, PlaceObservation, PlaceTier, ValueType
from core.selectors import coverage_notes, series_payload

from .factories import make_domain, make_indicator, make_place, make_source

SHARE = "imd-most-deprived-decile-share-england"
SCORE = "imd-average-score-england"

HEADER = (
    "LSOA code (2011),Local Authority District code (2019),"
    "Index of Multiple Deprivation (IMD) Score,"
    "Index of Multiple Deprivation (IMD) Decile (where 1 is most deprived 10% of LSOAs),"
    "Total population: mid 2015 (excluding prisoners)\n"
)

# LAD A "Concentratown": 3 of 5 LSOAs in decile 1 (60% share) but moderate scores.
# LAD B "Uniformville": 2 of 5 LSOAs in decile 1 (40% share) but uniformly high scores.
# So A wins decile-share, B wins population-weighted score — the real divergence.
ROWS = (
    "E06000002,E06000002,40,1,1000\n"
    "E06000002,E06000002,38,1,1000\n"
    "E06000002,E06000002,35,1,1000\n"
    "E06000002,E06000002,10,5,1000\n"
    "E06000002,E06000002,8,7,1000\n"
    "E06000009,E06000009,46,1,1000\n"
    "E06000009,E06000009,44,1,1000\n"
    "E06000009,E06000009,42,3,1000\n"
    "E06000009,E06000009,41,3,1000\n"
    "E06000009,E06000009,40,4,1000\n"
    "E09999999,E09999999,50,1,1000\n"   # not in spine -> unmatched
)


def _seed_imd_indicators():
    community = make_domain("community", "Community & social")
    make_indicator(community, code=SHARE, is_additive=False, value_type=ValueType.RATE,
                   unit="%", name="IMD: share of LSOAs in most-deprived national decile (England)")
    make_indicator(community, code=SCORE, is_additive=False, value_type=ValueType.INDEX,
                   unit="score", name="IMD: population-weighted average score (England)")


class ImdIngestTests(TestCase):
    def setUp(self):
        _seed_imd_indicators()
        make_source("English Indices of Deprivation", "MHCLG")
        make_place("E06000002", "Concentratown", tier=PlaceTier.LAD, valid_from=date(2019, 12, 1))
        make_place("E06000009", "Uniformville", tier=PlaceTier.LAD, valid_from=date(2019, 12, 1))

    def _ingest(self):
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as fh:
            fh.write(HEADER + ROWS)
            path = fh.name
        call_command("ingest_imd", path=path)

    def _val(self, gss, code):
        return PlaceObservation.objects.get(place__gss_code=gss, indicator__code=code).value

    def test_metrics_compute_and_are_point_observations(self):
        self._ingest()
        # 2 matched LADs x 2 metrics = 4; the unmatched LAD contributes nothing.
        self.assertEqual(PlaceObservation.objects.count(), 4)
        # A: 3/5 in decile 1 = 60.00%; weighted score (equal pops) = mean(40,38,35,10,8)=26.20.
        self.assertEqual(self._val("E06000002", SHARE), Decimal("60.00"))
        self.assertEqual(self._val("E06000002", SCORE), Decimal("26.20"))
        # B: 2/5 in decile 1 = 40.00%; weighted score = mean(46,44,42,41,40)=42.60.
        self.assertEqual(self._val("E06000009", SHARE), Decimal("40.00"))
        self.assertEqual(self._val("E06000009", SCORE), Decimal("42.60"))
        o = PlaceObservation.objects.get(place__gss_code="E06000002", indicator__code=SHARE)
        self.assertEqual(o.period_type, PeriodType.POINT)
        self.assertEqual(o.vintage, "IoD2019")

    def test_concentration_vs_level_diverge(self):
        self._ingest()
        # A is more deprived by concentration (decile-share); B by overall level (score).
        self.assertGreater(self._val("E06000002", SHARE), self._val("E06000009", SHARE))
        self.assertLess(self._val("E06000002", SCORE), self._val("E06000009", SCORE))

    def test_unmatched_lad_dropped(self):
        self._ingest()
        self.assertFalse(PlaceObservation.objects.filter(place__gss_code="E09999999").exists())

    def test_idempotent(self):
        self._ingest()
        self._ingest()
        self.assertEqual(PlaceObservation.objects.count(), 4)


class ImdPopulationWeightingTests(TestCase):
    def setUp(self):
        _seed_imd_indicators()
        make_source("English Indices of Deprivation", "MHCLG")
        make_place("E06000002", "Weighttown", tier=PlaceTier.LAD, valid_from=date(2019, 12, 1))

    def test_score_is_population_weighted_not_simple_mean(self):
        # Two LSOAs: score 10 (pop 100) and score 50 (pop 900). Simple mean = 30;
        # population-weighted = (10*100 + 50*900)/1000 = 46.00.
        rows = ("E06000002,E06000002,10,5,100\n"
                "E06000002,E06000002,50,1,900\n")
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as fh:
            fh.write(HEADER + rows)
            path = fh.name
        call_command("ingest_imd", path=path)
        v = PlaceObservation.objects.get(place__gss_code="E06000002", indicator__code=SCORE).value
        self.assertEqual(v, Decimal("46.00"))


class ImdSurfaceTests(TestCase):
    def setUp(self):
        _seed_imd_indicators()

    def test_coverage_note_fires_for_non_england(self):
        wales = make_place("W06000019", "Blaenau Gwent", tier=PlaceTier.LAD)
        noted = {n["indicator"] for n in coverage_notes(wales)}
        self.assertIn("IMD: share of LSOAs in most-deprived national decile (England)", noted)
        self.assertIn("IMD: population-weighted average score (England)", noted)

    def test_england_place_has_no_imd_note(self):
        eng = make_place("E06000002", "Somewhere", tier=PlaceTier.LAD)
        noted = {n["indicator"] for n in coverage_notes(eng)}
        self.assertNotIn("IMD: population-weighted average score (England)", noted)

    def test_descriptors_present_and_factual(self):
        from core.models import Indicator
        p = make_place("E06000002", "Somewhere", tier=PlaceTier.LAD)
        src = make_source("English Indices of Deprivation", "MHCLG")
        from .factories import make_observation
        for code in (SHARE, SCORE):
            ind = Indicator.objects.get(code=code)
            make_observation(ind, p, src, value=Decimal("20"), period_start=date(2019, 9, 26),
                             period_end=date(2019, 9, 26), period_type="POINT")
            desc = series_payload(p, ind)["descriptor"]
            self.assertTrue(desc)
            self.assertNotIn("worse", desc.lower())  # factual, no judgment
