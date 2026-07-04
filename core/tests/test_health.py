"""
Phase 4c (health) — OHID Fingertips life expectancy at birth, England, LAD tier.

Covers the pooled-period parser, the sex-split ingest (headline value only, 3-year
pooled only, unmatched areas reported), and the partial-coverage notes that tell a
non-England / non-GB place why an indicator is absent rather than leaving a blank.
"""

from datetime import date
from decimal import Decimal
from unittest import mock

from django.core.management import call_command
from django.test import TestCase

from core.management.commands.ingest_fingertips import Command as FingertipsCommand
from core.management.commands.ingest_fingertips import _parse_pooled_period
from core.models import PeriodType, PlaceObservation, PlaceTier, ValueType
from core.selectors import coverage_notes, series_payload

from .factories import make_domain, make_indicator, make_place, make_source


def _row(area, name, sex, period, value, category=""):
    return {
        "Area Code": area, "Area Name": name, "Sex": sex, "Age": "All ages",
        "Category Type": category, "Time period": period, "Value": value,
    }


class PooledPeriodTests(TestCase):
    def test_parses_three_year_window(self):
        self.assertEqual(_parse_pooled_period("2001 - 03"), (date(2001, 1, 1), date(2003, 12, 31)))
        self.assertEqual(_parse_pooled_period("2022 - 24"), (date(2022, 1, 1), date(2024, 12, 31)))

    def test_single_year_is_rejected(self):
        self.assertIsNone(_parse_pooled_period("2024"))


class FingertipsIngestTests(TestCase):
    def setUp(self):
        health = make_domain("health", "Health")
        make_indicator(health, code="life-expectancy-birth-male", is_additive=False,
                       value_type=ValueType.RATIO, unit="years")
        make_indicator(health, code="life-expectancy-birth-female", is_additive=False,
                       value_type=ValueType.RATIO, unit="years")
        make_place("E07000223", "Adur", tier=PlaceTier.LAD, valid_from=date(2019, 12, 1))
        # E06000060 deliberately absent -> unmatched (geography drift, like Nomis).

    FIXTURE = [
        _row("E07000223", "Adur", "Male", "2001 - 03", "77.3"),
        _row("E07000223", "Adur", "Female", "2001 - 03", "82.3"),
        _row("E07000223", "Adur", "Male", "2002 - 04", "77.1"),
        _row("E07000223", "Adur", "Male", "2003", "78.0"),          # single year -> skipped
        _row("E07000223", "Adur", "Persons", "2001 - 03", "79.8"),  # no such sex -> skipped
        _row("E07000223", "Adur", "Male", "2001 - 03", "60.0",
             category="LSOA21 deprivation deciles within area (IMD trend)"),  # breakdown -> skipped
        _row("E06000060", "Buckinghamshire UA", "Male", "2001 - 03", "80.0"),  # unmatched place
    ]

    def _run(self):
        with mock.patch.object(FingertipsCommand, "_fetch", return_value=self.FIXTURE):
            call_command("ingest_fingertips", vintage="fingertips-test")

    def test_writes_only_pooled_headline_by_sex_on_matched_places(self):
        self._run()
        # Adur: Male 2001-03, Male 2002-04, Female 2001-03 = 3. Single-year, Persons,
        # the deprivation breakdown, and the unmatched Bucks row are all excluded.
        self.assertEqual(PlaceObservation.objects.count(), 3)
        o = PlaceObservation.objects.get(indicator__code="life-expectancy-birth-male",
                                         period_start=date(2001, 1, 1))
        self.assertEqual(o.period_end, date(2003, 12, 31))
        self.assertEqual(o.period_type, PeriodType.CALENDAR_YEAR)
        self.assertEqual(o.value, Decimal("77.3"))
        self.assertFalse(PlaceObservation.objects.filter(place__gss_code="E06000060").exists())

    def test_idempotent(self):
        self._run()
        self._run()
        self.assertEqual(PlaceObservation.objects.count(), 3)


class CoverageNoteTests(TestCase):
    def setUp(self):
        health = make_domain("health", "Health")
        labour = make_domain("labour-market", "Labour market")
        make_indicator(health, code="life-expectancy-birth-male", is_additive=False,
                       value_type=ValueType.RATIO, unit="years", name="Life expectancy at birth (male)")
        make_indicator(health, code="life-expectancy-birth-female", is_additive=False,
                       value_type=ValueType.RATIO, unit="years", name="Life expectancy at birth (female)")
        make_indicator(labour, code="employment-rate-16-64", is_additive=False,
                       value_type=ValueType.RATE, unit="%", name="Employment rate (16-64)")
        make_indicator(labour, code="median-weekly-pay", is_additive=False,
                       value_type=ValueType.RATIO, unit="£", name="Median gross weekly pay (residence)")

    def test_england_lad_has_no_notes(self):
        p = make_place("E07000223", "Adur", tier=PlaceTier.LAD)
        self.assertEqual(coverage_notes(p), [])

    def test_wales_lad_flags_life_expectancy_only(self):
        p = make_place("W06000019", "Blaenau Gwent", tier=PlaceTier.LAD)
        notes = {n["indicator"] for n in coverage_notes(p)}
        self.assertIn("Life expectancy at birth (male)", notes)
        self.assertNotIn("Employment rate (16-64)", notes)  # GB includes Wales

    def test_ni_lad_flags_health_and_labour(self):
        p = make_place("N09000001", "Antrim", tier=PlaceTier.LAD)
        notes = {n["indicator"] for n in coverage_notes(p)}
        self.assertIn("Life expectancy at birth (female)", notes)
        self.assertIn("Employment rate (16-64)", notes)
        self.assertIn("Median gross weekly pay (residence)", notes)

    def test_wpc_place_gets_no_lad_indicator_notes(self):
        # These indicators live at LAD; a constituency shouldn't be told about them.
        p = make_place("W07000081", "Some Seat", tier=PlaceTier.WPC)
        self.assertEqual(coverage_notes(p), [])

    def test_series_payload_carries_coverage_label(self):
        from core.models import Indicator
        p = make_place("E07000223", "Adur", tier=PlaceTier.LAD)
        src = make_source("OHID Fingertips", "OHID")
        le = Indicator.objects.get(code="life-expectancy-birth-male")
        from .factories import make_observation
        make_observation(le, p, src, value=Decimal("79"), period_start=date(2001, 1, 1),
                         period_end=date(2003, 12, 31), period_type="CALENDAR_YEAR")
        self.assertEqual(series_payload(p, le)["coverage"], "England only")
