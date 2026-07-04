"""
Phase 4c (deprivation) — Welsh WIMD decile-share at the LA tier.

Covers: reading the PUBLISHED decile column from an ODS file with the stdlib reader (no
derivation — so no rounding question), LSOA -> LA aggregation (raw deciles never stored),
name-based join to the 22 W06 spine Places, an unmatched LA failing loudly, and the
Wales-only coverage note. Also asserts NO score metric exists (WG says averaging the
exponentially-transformed scores is invalid). LSOAs are aggregated through; never Place rows.
"""

import tempfile
import zipfile
from datetime import date
from decimal import Decimal

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from core.management.commands.ingest_wimd import parse_wimd
from core.models import Indicator, PeriodType, PlaceObservation, PlaceTier, ValueType
from core.selectors import coverage_notes, series_payload

from .factories import make_domain, make_indicator, make_place, make_source

CODE = "wimd-most-deprived-decile-share-wales"

_HEADER = ["LSOA code", "LSOA name (Eng)", "Local Authority name (Eng)",
           "WIMD 2019 overall rank", "WIMD 2019 overall decile",
           "WIMD 2019 overall quintile", "WIMD 2019 overall quartile"]


def _cell(v):
    if isinstance(v, (int, float)):
        return (f'<table:table-cell office:value-type="float" office:value="{v}">'
                f'<text:p>{v}</text:p></table:table-cell>')
    return f'<table:table-cell office:value-type="string"><text:p>{v}</text:p></table:table-cell>'


def _row(cells):
    return "<table:table-row>" + "".join(_cell(c) for c in cells) + "</table:table-row>"


def _make_wimd_ods(data_rows):
    """data_rows = list of (lsoa, la_name, rank, decile). Builds a minimal valid ODS
    with the Deciles_quintiles_quartiles sheet (a title row, then header, then data)."""
    body_rows = [_row(["WIMD 2019: LSOA overall"]), _row(_HEADER)]
    for lsoa, la, rank, decile in data_rows:
        body_rows.append(_row([lsoa, "LSOAname", la, rank, decile, 0, 0]))
    content = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<office:document-content '
        'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
        'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" '
        'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0">'
        '<office:body><office:spreadsheet>'
        '<table:table table:name="Deciles_quintiles_quartiles">'
        + "".join(body_rows) +
        '</table:table></office:spreadsheet></office:body></office:document-content>'
    )
    fh = tempfile.NamedTemporaryFile("wb", suffix=".ods", delete=False)
    with zipfile.ZipFile(fh.name, "w") as z:
        z.writestr("mimetype", "application/vnd.oasis.opendocument.spreadsheet")
        z.writestr("content.xml", content)
    return fh.name


# Newport: 4 LSOAs, deciles [1,1,3,5] -> 2/4 = 50%.  Monmouthshire: [8,9] -> 0%.
ROWS = [
    ("W01000001", "Newport", 10, 1), ("W01000002", "Newport", 20, 1),
    ("W01000003", "Newport", 800, 3), ("W01000004", "Newport", 1200, 5),
    ("W01000005", "Monmouthshire", 1700, 8), ("W01000006", "Monmouthshire", 1850, 9),
]


def _seed_indicator():
    community = make_domain("community", "Community & social")
    make_indicator(community, code=CODE, is_additive=False, value_type=ValueType.RATE,
                   unit="%", name="WIMD: share of LSOAs in most-deprived national decile (Wales)")


class WimdParseTests(TestCase):
    def test_reads_published_decile(self):
        rows = list(parse_wimd(_make_wimd_ods(ROWS)))
        self.assertEqual(len(rows), 6)
        self.assertEqual(rows[0], ("W01000001", "Newport", 1))
        self.assertEqual(rows[4], ("W01000005", "Monmouthshire", 8))


class WimdIngestTests(TestCase):
    def setUp(self):
        _seed_indicator()
        make_source("Welsh Index of Multiple Deprivation", "Welsh Government")
        make_place("W06000022", "Newport", tier=PlaceTier.LAD, valid_from=date(2019, 12, 1))
        make_place("W06000021", "Monmouthshire", tier=PlaceTier.LAD, valid_from=date(2019, 12, 1))

    def _val(self, gss):
        return PlaceObservation.objects.get(place__gss_code=gss, indicator__code=CODE).value

    def test_decile_share_and_point_observation(self):
        call_command("ingest_wimd", path=_make_wimd_ods(ROWS), vintage="WIMD-test")
        self.assertEqual(PlaceObservation.objects.count(), 2)
        self.assertEqual(self._val("W06000022"), Decimal("50.00"))   # 2/4 in decile 1
        self.assertEqual(self._val("W06000021"), Decimal("0.00"))
        o = PlaceObservation.objects.get(place__gss_code="W06000022")
        self.assertEqual(o.period_type, PeriodType.POINT)
        self.assertEqual(o.vintage, "WIMD-test")

    def test_unmatched_la_fails_loudly(self):
        rows = [("W01000001", "Atlantis", 10, 1)] + ROWS[1:]
        with self.assertRaises(CommandError):
            call_command("ingest_wimd", path=_make_wimd_ods(rows), vintage="WIMD-test")

    def test_idempotent(self):
        path = _make_wimd_ods(ROWS)
        call_command("ingest_wimd", path=path, vintage="WIMD-test")
        call_command("ingest_wimd", path=path, vintage="WIMD-test")
        self.assertEqual(PlaceObservation.objects.count(), 2)


class WimdCoverageTests(TestCase):
    def setUp(self):
        _seed_indicator()

    def test_wales_place_shows_caption_no_absence_note(self):
        p = make_place("W06000022", "Newport", tier=PlaceTier.LAD)
        src = make_source("Welsh Index of Multiple Deprivation", "Welsh Government")
        from .factories import make_observation
        ind = Indicator.objects.get(code=CODE)
        make_observation(ind, p, src, value=Decimal("24"), period_start=date(2019, 11, 27),
                         period_end=date(2019, 11, 27), period_type="POINT", vintage="WIMD2019")
        pay = series_payload(p, ind)
        self.assertEqual(pay["coverage"], "Wales only")
        self.assertNotIn("Wales only", {n["note"] for n in coverage_notes(p)})
        self.assertTrue(pay["descriptor"])

    def test_non_wales_place_flags_wimd(self):
        for gss, name in [("E07000223", "Adur"), ("S12000049", "Glasgow City"),
                          ("N09000003", "Belfast")]:
            p = make_place(gss, name, tier=PlaceTier.LAD)
            self.assertIn("Wales only", {n["note"] for n in coverage_notes(p)})

    def test_no_score_metric_exists(self):
        # WG says averaging the exponentially-transformed scores is invalid, so Wales
        # (unlike England) gets decile-share only — no wimd-average-score indicator.
        self.assertFalse(Indicator.objects.filter(code="wimd-average-score-wales").exists())
