"""
Phase 4c (deprivation) — Scottish SIMD decile-share at the LAD tier.

Covers: the rank -> most-deprived-decile derivation (ceil(N/10) cut, since SIMD has no
decile column and no score), Data Zone -> council aggregation (raw ranks never stored),
the Western-Isles name alias, an unmatched council failing loudly, and the Scotland-only
coverage note / cross-nation exclusion. Data Zones are aggregated through; never Place rows.
"""

import tempfile
from datetime import date
from decimal import Decimal

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from openpyxl import Workbook

from core.management.commands.ingest_simd import parse_simd_ranks
from core.models import PeriodType, PlaceObservation, PlaceTier, ValueType
from core.selectors import coverage_notes, series_payload

from .factories import make_domain, make_indicator, make_place, make_source

CODE = "simd-most-deprived-decile-share-scotland"


def _make_simd_workbook(rows):
    """rows = list of (data_zone, council_name, rank). Header on row 1, sheet named
    exactly like gov.scot's."""
    wb = Workbook()
    wb.active.title = "Content & notes"
    ws = wb.create_sheet("SIMD 2020v2 ranks")
    ws.append(["Data_Zone", "Intermediate_Zone", "Council_area",
               "Total_population", "Working_age_population", "SIMD2020v2_Rank"])
    for dz, council, rank in rows:
        ws.append([dz, "IZ", council, 800, 500, rank])
    fh = tempfile.NamedTemporaryFile("wb", suffix=".xlsx", delete=False)
    wb.save(fh.name)
    return fh.name


def _seed_indicator():
    community = make_domain("community", "Community & social")
    make_indicator(community, code=CODE, is_additive=False, value_type=ValueType.RATE,
                   unit="%", name="SIMD: share of Data Zones in most-deprived national decile (Scotland)")


# 20 Data Zones -> decile-1 cut = ceil(20/10) = 2 (ranks 1 and 2 are "most deprived").
#   Glasgow City : 4 DZs at ranks 1,2,3,4  -> 2 in decile 1 -> 50%
#   Western Isles: 2 DZs at ranks 5,6       -> 0%
#   Aberdeen City: 14 DZs at ranks 7..20    -> 0%
ROWS = (
    [("S01000001", "Glasgow City", 1), ("S01000002", "Glasgow City", 2),
     ("S01000003", "Glasgow City", 3), ("S01000004", "Glasgow City", 4),
     ("S01000005", "Na h-Eileanan an Iar", 5), ("S01000006", "Na h-Eileanan an Iar", 6)]
    + [(f"S010000{7+i:02d}", "Aberdeen City", 7 + i) for i in range(14)]
)


class SimdParseTests(TestCase):
    def test_parses_datazone_council_rank(self):
        rows = list(parse_simd_ranks(_make_simd_workbook(ROWS)))
        self.assertEqual(len(rows), 20)
        self.assertEqual(rows[0], ("S01000001", "Glasgow City", 1))


class SimdIngestTests(TestCase):
    def setUp(self):
        _seed_indicator()
        make_source("Scottish Index of Multiple Deprivation", "Scottish Government")
        make_place("S12000049", "Glasgow City", tier=PlaceTier.LAD, valid_from=date(2019, 12, 1))
        make_place("S12000013", "Na h-Eileanan Siar", tier=PlaceTier.LAD, valid_from=date(2019, 12, 1))
        make_place("S12000033", "Aberdeen City", tier=PlaceTier.LAD, valid_from=date(2019, 12, 1))

    def _val(self, gss):
        return PlaceObservation.objects.get(place__gss_code=gss, indicator__code=CODE).value

    def test_decile_share_and_point_observation(self):
        call_command("ingest_simd", path=_make_simd_workbook(ROWS), vintage="SIMD-test")
        self.assertEqual(PlaceObservation.objects.count(), 3)   # one per council
        self.assertEqual(self._val("S12000049"), Decimal("50.00"))   # 2/4 in decile 1
        self.assertEqual(self._val("S12000033"), Decimal("0.00"))
        o = PlaceObservation.objects.get(place__gss_code="S12000049")
        self.assertEqual(o.period_type, PeriodType.POINT)
        self.assertEqual(o.vintage, "SIMD-test")

    def test_western_isles_alias_resolves(self):
        call_command("ingest_simd", path=_make_simd_workbook(ROWS), vintage="SIMD-test")
        # "Na h-Eileanan an Iar" (file) -> "Na h-Eileanan Siar" (spine, S12000013).
        self.assertTrue(PlaceObservation.objects.filter(place__gss_code="S12000013").exists())
        self.assertEqual(self._val("S12000013"), Decimal("0.00"))

    def test_unmatched_council_fails_loudly(self):
        rows = [("S01000001", "Atlantis", 1)] + ROWS[1:]
        make_place("S12000049", "Glasgow City", tier=PlaceTier.LAD)  # already exists; harmless
        with self.assertRaises(CommandError):
            call_command("ingest_simd", path=_make_simd_workbook(rows), vintage="SIMD-test")

    def test_idempotent(self):
        path = _make_simd_workbook(ROWS)
        call_command("ingest_simd", path=path, vintage="SIMD-test")
        call_command("ingest_simd", path=path, vintage="SIMD-test")
        self.assertEqual(PlaceObservation.objects.count(), 3)


class SimdCoverageTests(TestCase):
    def setUp(self):
        _seed_indicator()

    def test_scotland_place_shows_caption_no_absence_note_and_descriptor(self):
        from core.models import Indicator
        p = make_place("S12000049", "Glasgow City", tier=PlaceTier.LAD)
        src = make_source("Scottish Index of Multiple Deprivation", "Scottish Government")
        from .factories import make_observation
        ind = Indicator.objects.get(code=CODE)
        make_observation(ind, p, src, value=Decimal("30"), period_start=date(2020, 1, 28),
                         period_end=date(2020, 1, 28), period_type="POINT", vintage="SIMD2020v2")
        pay = series_payload(p, ind)
        # The chart caption states the coverage; the place-dependent "absent" note list
        # does NOT flag SIMD for a Scottish place (it has the data).
        self.assertEqual(pay["coverage"], "Scotland only")
        self.assertNotIn("Scotland only", {n["note"] for n in coverage_notes(p)})
        self.assertTrue(pay["descriptor"])
        self.assertNotIn("worse", pay["descriptor"].lower())

    def test_non_scotland_place_flags_simd(self):
        for gss, name in [("E07000223", "Adur"), ("W06000019", "Blaenau Gwent"),
                          ("N09000001", "Antrim")]:
            p = make_place(gss, name, tier=PlaceTier.LAD)
            noted = {n["note"] for n in coverage_notes(p)}
            self.assertIn("Scotland only", noted)
