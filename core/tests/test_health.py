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

    def test_wales_lad_has_no_notes_le_now_uk_wide(self):
        # LE is now sourced UK-wide from ONS, so a Welsh place is no longer flagged
        # for it; labour (GB) also includes Wales -> no notes at all.
        p = make_place("W06000019", "Blaenau Gwent", tier=PlaceTier.LAD)
        notes = {n["indicator"] for n in coverage_notes(p)}
        self.assertNotIn("Life expectancy at birth (male)", notes)
        self.assertNotIn("Employment rate (16-64)", notes)
        self.assertEqual(coverage_notes(p), [])

    def test_ni_lad_flags_labour_only_not_life_expectancy(self):
        # NI now HAS life expectancy (ONS), but still no NI labour data (NISRA).
        p = make_place("N09000001", "Antrim", tier=PlaceTier.LAD)
        notes = {n["indicator"] for n in coverage_notes(p)}
        self.assertNotIn("Life expectancy at birth (female)", notes)
        self.assertIn("Employment rate (16-64)", notes)
        self.assertIn("Median gross weekly pay (residence)", notes)

    def test_wpc_place_gets_no_lad_indicator_notes(self):
        # These indicators live at LAD; a constituency shouldn't be told about them.
        p = make_place("W07000081", "Some Seat", tier=PlaceTier.WPC)
        self.assertEqual(coverage_notes(p), [])

    def test_series_payload_le_now_has_no_coverage_label(self):
        from core.models import Indicator
        p = make_place("E07000223", "Adur", tier=PlaceTier.LAD)
        src = make_source("ONS life expectancy for local areas", "ONS")
        le = Indicator.objects.get(code="life-expectancy-birth-male")
        from .factories import make_observation
        make_observation(le, p, src, value=Decimal("79"), period_start=date(2001, 1, 1),
                         period_end=date(2003, 12, 31), period_type="CALENDAR_YEAR")
        # UK-wide now -> no coverage caption.
        self.assertIsNone(series_payload(p, le)["coverage"])
        # The mechanism still works for an indicator that IS partial (GB-only labour).
        emp = Indicator.objects.get(code="employment-rate-16-64")
        make_observation(emp, p, src, value=Decimal("75"), period_start=date(2020, 1, 1),
                         period_end=date(2020, 12, 31), period_type="CALENDAR_YEAR")
        self.assertEqual(series_payload(p, emp)["coverage"],
                         "Great Britain only — no Northern Ireland")


class OnsLePooledPeriodTests(TestCase):
    def test_parses_range_and_rejects_single(self):
        from core.management.commands.ingest_le_ons import _pooled_period
        self.assertEqual(_pooled_period("2001 to 2003"),
                         (date(2001, 1, 1), date(2003, 12, 31)))
        self.assertEqual(_pooled_period("2022 to 2024"),
                         (date(2022, 1, 1), date(2024, 12, 31)))
        self.assertIsNone(_pooled_period("2024"))
        self.assertIsNone(_pooled_period("2001 - 03"))  # Fingertips form, not ONS


def _make_ons_le_workbook():
    """Fabricate a minimal ONS-LE-shaped workbook (sheet '1', header on row 6)."""
    import tempfile

    from openpyxl import Workbook

    wb = Workbook()
    wb.active.title = "Cover_sheet"
    ws = wb.create_sheet("1")
    for _ in range(5):
        ws.append([])  # pad so the header lands on row 6, like the real file
    ws.append(["Period", "Country", "Area type", "Area code", "Area name", "Sex",
               "Sex code", "Age group", "Age code", "Life expectancy",
               "Lower confidence interval", "Upper confidence interval"])
    rows = [
        # KEEP: Local Areas, at-birth, Male/Female, across all four nations.
        ["2022 to 2024", "England", "Local Areas", "E07000223", "Adur", "Male", 1, "<1", 0, 80.8, 80, 81],
        ["2022 to 2024", "Wales", "Local Areas", "W06000019", "Blaenau Gwent", "Female", 2, "<1", 0, 82.7, 82, 83],
        ["2022 to 2024", "Scotland", "Local Areas", "S12000036", "City of Edinburgh", "Male", 1, "<1", 0, 78.3, 78, 79],
        ["2021 to 2023", "N. Ireland", "Local Areas", "N09000001", "Antrim", "Male", 1, "<1", 0, 79.2, 78, 80],
        # DROP: wrong age group (not at birth).
        ["2022 to 2024", "England", "Local Areas", "E07000223", "Adur", "Male", 1, "01 to 04", 1, 80.0, 79, 81],
        # DROP: Country-level comparator (Area type not "Local Areas").
        ["2022 to 2024", "England", "Country", "E92000001", "England", "Male", 1, "<1", 0, 79.5, 79, 80],
        # DROP: a sex we don't model.
        ["2022 to 2024", "England", "Local Areas", "E07000223", "Adur", "Persons", 4, "<1", 0, 82.0, 81, 83],
        # UNMATCHED: upper-tier county (E10) has no LAD Place -> reported, not written.
        ["2022 to 2024", "England", "Local Areas", "E10000003", "Cambridgeshire", "Male", 1, "<1", 0, 81.0, 80, 82],
    ]
    for r in rows:
        ws.append(r)
    fh = tempfile.NamedTemporaryFile("wb", suffix=".xlsx", delete=False)
    wb.save(fh.name)
    return fh.name


class OnsLeParseTests(TestCase):
    def test_filters_to_local_areas_at_birth_by_sex(self):
        from core.management.commands.ingest_le_ons import parse_le
        rows = list(parse_le(_make_ons_le_workbook()))
        codes = sorted((c, sex) for c, _n, sex, _ps, _pe, _v in rows)
        # 4 kept: Adur M, Blaenau Gwent F, Edinburgh M, Antrim M. Nothing else.
        self.assertEqual(codes, [("E07000223", "Male"), ("E10000003", "Male"),
                                 ("N09000001", "Male"), ("S12000036", "Male"),
                                 ("W06000019", "Female")])


class OnsLeIngestTests(TestCase):
    def setUp(self):
        health = make_domain("health", "Health")
        make_indicator(health, code="life-expectancy-birth-male", is_additive=False,
                       value_type=ValueType.RATIO, unit="years")
        make_indicator(health, code="life-expectancy-birth-female", is_additive=False,
                       value_type=ValueType.RATIO, unit="years")
        for gss, name in [("E07000223", "Adur"), ("W06000019", "Blaenau Gwent"),
                          ("S12000036", "City of Edinburgh"), ("N09000001", "Antrim")]:
            make_place(gss, name, tier=PlaceTier.LAD, valid_from=date(2019, 12, 1))
        # E10000003 (county) deliberately absent -> unmatched, like geography drift.

    def test_writes_all_four_nations_matched_only(self):
        path = _make_ons_le_workbook()
        call_command("ingest_le_ons", path=path, vintage="ons-le-test")
        # 4 matched observations across E/W/S/N; the E10 county is not written.
        self.assertEqual(PlaceObservation.objects.count(), 4)
        nations = sorted(PlaceObservation.objects.values_list("place__gss_code", flat=True))
        self.assertEqual(nations, ["E07000223", "N09000001", "S12000036", "W06000019"])
        self.assertFalse(PlaceObservation.objects.filter(place__gss_code="E10000003").exists())
        w = PlaceObservation.objects.get(place__gss_code="W06000019")
        self.assertEqual(w.indicator.code, "life-expectancy-birth-female")
        self.assertEqual(w.value, Decimal("82.7"))
        self.assertEqual((w.period_start, w.period_end), (date(2022, 1, 1), date(2024, 12, 31)))
        self.assertEqual(w.vintage, "ons-le-test")

    def test_idempotent(self):
        path = _make_ons_le_workbook()
        call_command("ingest_le_ons", path=path, vintage="ons-le-test")
        call_command("ingest_le_ons", path=path, vintage="ons-le-test")
        self.assertEqual(PlaceObservation.objects.count(), 4)

    def test_new_vintage_coexists_with_fingertips(self):
        # Append-only: an ONS row lands beside a Fingertips row for the same
        # place/period/indicator (different source+vintage) — two rows, not one.
        from core.models import Indicator
        p = self._lad("E07000223")
        le = Indicator.objects.get(code="life-expectancy-birth-male")
        finger = make_source("OHID Fingertips", "OHID")
        from .factories import make_observation
        make_observation(le, p, finger, value=Decimal("80.8"), period_start=date(2022, 1, 1),
                         period_end=date(2024, 12, 31), period_type="CALENDAR_YEAR",
                         vintage="fingertips-x")
        call_command("ingest_le_ons", path=_make_ons_le_workbook(), vintage="ons-le-test")
        self.assertEqual(
            PlaceObservation.objects.filter(
                place=p, indicator=le, period_start=date(2022, 1, 1)).count(), 2)

    @staticmethod
    def _lad(gss):
        from core.models import Place
        return Place.objects.get(gss_code=gss, tier=PlaceTier.LAD)
