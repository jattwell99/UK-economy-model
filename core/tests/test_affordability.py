"""
Affordability — ONS house-price-to-residence-based-earnings ratio (E&W, LAD).

Covers: parsing sheet 5c (ratio, skipping the trailing "5-Year Average" column and
suppressed "[x]" cells), the Barnsley/Sheffield recode alias landing on the spine code,
matching (including a post-2019 unitary), and the England-and-Wales-only coverage note.
"""

import tempfile
from datetime import date
from decimal import Decimal

from django.core.management import call_command
from django.test import TestCase

from openpyxl import Workbook

from core.management.commands.ingest_affordability import parse_affordability
from core.models import Indicator, PeriodType, PlaceObservation, PlaceTier, ValueType
from core.selectors import coverage_notes, series_payload

from .factories import make_domain, make_indicator, make_place, make_source

CODE = "house-price-to-earnings-ratio-residence"


def _make_workbook(rows):
    """rows = list of (la_code, la_name, {year: value}). Builds a 5c-shaped xlsx with a
    title row, a header row (incl. a '5-Year Average' column to be skipped), then data."""
    wb = Workbook()
    wb.active.title = "Contents"
    ws = wb.create_sheet("5c")
    ws.append(["Table 5c - Ratio of median house price to ... residence-based earnings"])
    ws.append(["Country/Region code", "Country/Region name", "Local authority code",
               "Local authority name", "2002", "2003", "5-Year Average"])
    for code, name, vals in rows:
        ws.append(["E12000001", "Region", code, name,
                   vals.get(2002, ""), vals.get(2003, ""), vals.get("avg", "")])
    fh = tempfile.NamedTemporaryFile("wb", suffix=".xlsx", delete=False)
    wb.save(fh.name)
    return fh.name


ROWS = [
    ("E07000223", "Adur", {2002: 5.0, 2003: 6.0, "avg": 5.5}),
    ("E06000066", "Somerset", {2002: 7.0, 2003: 8.57, "avg": 7.8}),   # unitary
    ("E08000038", "Barnsley", {2002: 4.0, 2003: 4.2, "avg": 4.1}),    # recode -> E08000016
    ("E09000001", "City of London", {2002: "[x]", 2003: "[x]", "avg": "[x]"}),  # suppressed
]


class AffordabilityParseTests(TestCase):
    def test_parses_years_skips_average_and_suppressed(self):
        rows = list(parse_affordability(_make_workbook(ROWS)))
        # Adur/Somerset/Barnsley x 2 years = 6; the "5-Year Average" col and City of
        # London's "[x]" cells are skipped.
        got = {(c, y): float(v) for c, _n, y, v in rows}
        self.assertEqual(got[("E07000223", 2002)], 5.0)
        self.assertEqual(got[("E07000223", 2003)], 6.0)
        self.assertNotIn(("E09000001", 2002), got)          # suppressed -> skipped
        self.assertTrue(all(y in (2002, 2003) for _c, _n, y, _v in rows))  # no 5-Year Average


class AffordabilityIngestTests(TestCase):
    def setUp(self):
        housing = make_domain("housing", "Housing")
        make_indicator(housing, code=CODE, is_additive=False, value_type=ValueType.RATIO,
                       unit="ratio", name="House price to residence-based earnings ratio")
        make_source("ONS Housing affordability", "ONS")
        make_place("E07000223", "Adur", tier=PlaceTier.LAD, valid_from=date(2019, 12, 1))
        make_place("E06000066", "Somerset", tier=PlaceTier.LAD, valid_from=date(2023, 4, 1))
        make_place("E08000016", "Barnsley", tier=PlaceTier.LAD, valid_from=date(2019, 12, 1))
        make_place("E09000001", "City of London", tier=PlaceTier.LAD, valid_from=date(2019, 12, 1))

    def _val(self, gss, year):
        return PlaceObservation.objects.get(
            place__gss_code=gss, indicator__code=CODE, period_start=date(year, 1, 1)).value

    def test_ingest_matches_unitary_recode_and_skips_suppressed(self):
        call_command("ingest_affordability", path=_make_workbook(ROWS), vintage="aff-test")
        # Adur (2) + Somerset (2) + Barnsley-via-recode (2) = 6; City of London suppressed.
        self.assertEqual(PlaceObservation.objects.count(), 6)
        self.assertEqual(self._val("E06000066", 2003), Decimal("8.57"))   # unitary lands
        self.assertEqual(self._val("E08000016", 2002), Decimal("4.0"))    # recode -> spine code
        self.assertFalse(PlaceObservation.objects.filter(place__gss_code="E09000001").exists())
        o = PlaceObservation.objects.get(place__gss_code="E07000223", period_start=date(2002, 1, 1))
        self.assertEqual(o.period_type, PeriodType.CALENDAR_YEAR)
        self.assertEqual(o.vintage, "aff-test")

    def test_idempotent(self):
        path = _make_workbook(ROWS)
        call_command("ingest_affordability", path=path, vintage="aff-test")
        call_command("ingest_affordability", path=path, vintage="aff-test")
        self.assertEqual(PlaceObservation.objects.count(), 6)


class AffordabilityCoverageTests(TestCase):
    def setUp(self):
        housing = make_domain("housing", "Housing")
        make_indicator(housing, code=CODE, is_additive=False, value_type=ValueType.RATIO,
                       unit="ratio", name="House price to residence-based earnings ratio")

    def test_ew_place_shows_caption_no_absence_note(self):
        from .factories import make_observation
        p = make_place("W06000015", "Cardiff", tier=PlaceTier.LAD)
        src = make_source("ONS Housing affordability", "ONS")
        ind = Indicator.objects.get(code=CODE)
        make_observation(ind, p, src, value=Decimal("7.6"), period_start=date(2023, 1, 1),
                         period_end=date(2023, 12, 31), period_type="CALENDAR_YEAR", vintage="aff")
        pay = series_payload(p, ind)
        self.assertEqual(pay["coverage"], "England and Wales only")
        self.assertNotIn("England and Wales only", {n["note"] for n in coverage_notes(p)})
        self.assertTrue(pay["descriptor"])
        self.assertNotIn("good", pay["descriptor"].lower())   # factual, not good/bad

    def test_scotland_and_ni_places_flag_affordability(self):
        for gss, name in [("S12000049", "Glasgow City"), ("N09000003", "Belfast")]:
            p = make_place(gss, name, tier=PlaceTier.LAD)
            self.assertIn("England and Wales only", {n["note"] for n in coverage_notes(p)})
