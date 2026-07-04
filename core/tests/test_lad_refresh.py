"""
LAD-vintage refresh (data half) — version-in the post-2019 unitaries, version-out the
abolished districts, and the ONS-LE recode alias. See
docs/lad_vintage_refresh_scoping_brief.md.

Non-negotiables under test: abolished districts are versioned out (valid_to set) but their
historic observations are KEPT (never deleted); the new unitary is created as a current
Place; the operation is idempotent; and the Barnsley/Sheffield recode lands on the spine
code, not a dropped row.
"""

import tempfile
import zipfile  # noqa: F401  (kept parallel with other ODS tests; xlsx used here)
from datetime import date
from decimal import Decimal

from django.core.management import call_command
from django.test import TestCase

from openpyxl import Workbook

from core.management.commands.refresh_lad_spine import Command as RefreshCommand
from core.models import Indicator, PlaceObservation, PlaceTier, ValueType
from core.selectors import series_payload

from .factories import make_domain, make_indicator, make_observation, make_place, make_source


class RefreshLadSpineTests(TestCase):
    def setUp(self):
        econ = make_domain("economy", "Economy")
        self.gva = make_indicator(econ, code="gva-balanced-total", is_additive=True,
                                  value_type=ValueType.CURRENCY, unit="£m")
        self.src = make_source("ONS Regional accounts", "ONS")
        # The 4 Buckinghamshire districts on the Dec-2019 spine, one with historic data.
        for gss, name in [("E07000004", "Aylesbury Vale"), ("E07000005", "Chiltern"),
                          ("E07000006", "South Bucks"), ("E07000007", "Wycombe")]:
            make_place(gss, name, tier=PlaceTier.LAD, valid_from=date(2019, 12, 1))
        av = self.p("E07000004")
        make_observation(self.gva, av, self.src, value=Decimal("1000"),
                         period_start=date(2018, 1, 1), period_end=date(2018, 12, 31),
                         period_type="CALENDAR_YEAR")

    @staticmethod
    def p(gss):
        from core.models import Place
        return Place.objects.get(gss_code=gss, tier=PlaceTier.LAD, valid_from=date(2019, 12, 1))

    def test_versions_in_unitary_and_out_districts_preserving_obs(self):
        call_command("refresh_lad_spine")
        from core.models import Place
        # Unitary created as a current Place (2020-04-01, valid_to null, England).
        bucks = Place.objects.get(gss_code="E06000060", tier=PlaceTier.LAD)
        self.assertEqual(bucks.valid_from, date(2020, 4, 1))
        self.assertIsNone(bucks.valid_to)
        self.assertEqual(bucks.nation, "E")
        # Districts versioned out (valid_to = day before), rows + obs preserved.
        av = self.p("E07000004")
        self.assertEqual(av.valid_to, date(2020, 3, 31))
        self.assertEqual(PlaceObservation.objects.filter(place=av).count(), 1)  # NOT deleted

    def test_idempotent(self):
        from core.models import Place
        call_command("refresh_lad_spine")
        call_command("refresh_lad_spine")
        self.assertEqual(Place.objects.filter(gss_code="E06000060", tier=PlaceTier.LAD).count(), 1)
        self.assertEqual(self.p("E07000004").valid_to, date(2020, 3, 31))
        self.assertEqual(PlaceObservation.objects.count(), 1)  # no dupes, nothing lost

    def test_unitary_renders_on_detail(self):
        call_command("refresh_lad_spine")
        from core.models import Place
        bucks = Place.objects.get(gss_code="E06000060", tier=PlaceTier.LAD)
        make_observation(self.gva, bucks, self.src, value=Decimal("5000"),
                         period_start=date(2022, 1, 1), period_end=date(2022, 12, 31),
                         period_type="CALENDAR_YEAR")
        pts = series_payload(bucks, self.gva)["points"]
        self.assertEqual(pts, [{"year": 2022, "value": 5000.0}])


def _make_ons_le_workbook(rows):
    """Minimal ONS-LE-shaped xlsx (sheet '1', header row 6). rows = (code,name,sex,period,le)."""
    wb = Workbook()
    wb.active.title = "Cover_sheet"
    ws = wb.create_sheet("1")
    for _ in range(5):
        ws.append([])
    ws.append(["Period", "Country", "Area type", "Area code", "Area name", "Sex",
               "Sex code", "Age group", "Age code", "Life expectancy",
               "Lower confidence interval", "Upper confidence interval"])
    for code, name, sex, period, le in rows:
        ws.append([period, "England", "Local Areas", code, name, sex, 1, "<1", 0, le, le, le])
    fh = tempfile.NamedTemporaryFile("wb", suffix=".xlsx", delete=False)
    wb.save(fh.name)
    return fh.name


class OnsLeRecodeAliasTests(TestCase):
    def setUp(self):
        health = make_domain("health", "Health")
        make_indicator(health, code="life-expectancy-birth-male", is_additive=False,
                       value_type=ValueType.RATIO, unit="years")
        make_indicator(health, code="life-expectancy-birth-female", is_additive=False,
                       value_type=ValueType.RATIO, unit="years")
        # Spine still uses the OLD Barnsley code; ONS LE emits the recoded one.
        make_place("E08000016", "Barnsley", tier=PlaceTier.LAD, valid_from=date(2019, 12, 1))

    def test_recode_lands_on_spine_code(self):
        path = _make_ons_le_workbook([("E08000038", "Barnsley", "Male", "2022 to 2024", 77.0)])
        call_command("ingest_le_ons", path=path, vintage="ons-test")
        # The recoded E08000038 row must attach to the existing E08000016 Place.
        o = PlaceObservation.objects.get(indicator__code="life-expectancy-birth-male")
        self.assertEqual(o.place.gss_code, "E08000016")
        self.assertEqual(o.value, Decimal("77.0"))
