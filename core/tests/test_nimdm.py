"""
Phase 4c (deprivation) — Northern Irish NIMDM decile-share at the LGD tier.

Covers: the rank -> most-deprived-decile derivation (ceil(N/10) cut; NISRA publishes
decile 1 = ranks 1-89 for 890 SOAs, so no rounding ambiguity), SOA -> LGD aggregation
(raw SOA ranks never stored), the code-based join (LGD2014code IS the N09 GSS code, no
name alias), an unmatched LGD failing loudly, and the NI-only coverage note / cross-nation
exclusion. SOAs are aggregated through; never Place rows.
"""

import tempfile
from datetime import date
from decimal import Decimal

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from core.management.commands.ingest_nimdm import parse_nimdm
from core.models import PeriodType, PlaceObservation, PlaceTier, ValueType
from core.selectors import coverage_notes, series_payload

from .factories import make_domain, make_indicator, make_place, make_source

CODE = "nimdm-most-deprived-decile-share-northern-ireland"

# Minimal columns the parser needs; a few extra to mirror the real 93-column file.
HEADER = "LGD2014code,LGD2014name,SOA2001,SOA2001name,MDM_rank\n"


def _make_csv(rows):
    """rows = list of (lgd_code, soa_code, rank)."""
    body = "".join(f"{lgd},LGDname,{soa},SOAname,{rank}\n" for lgd, soa, rank in rows)
    fh = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8")
    fh.write(HEADER + body)
    fh.close()
    return fh.name


def _seed_indicator():
    community = make_domain("community", "Community & social")
    make_indicator(community, code=CODE, is_additive=False, value_type=ValueType.RATE,
                   unit="%", name="NIMDM: share of SOAs in most-deprived national decile (Northern Ireland)")


# 20 SOAs -> decile-1 cut = ceil(20/10) = 2 (ranks 1,2 are "most deprived").
#   N09000003 (Belfast):    4 SOAs at ranks 1,2,3,4  -> 2 in decile 1 -> 50%
#   N09000005 (Derry):      2 SOAs at ranks 5,6       -> 0%
#   N09000006 (Fermanagh):  14 SOAs at ranks 7..20    -> 0%
ROWS = (
    [("N09000003", "S1", 1), ("N09000003", "S2", 2), ("N09000003", "S3", 3), ("N09000003", "S4", 4),
     ("N09000005", "S5", 5), ("N09000005", "S6", 6)]
    + [("N09000006", f"S{7+i}", 7 + i) for i in range(14)]
)


class NimdmParseTests(TestCase):
    def test_parses_soa_lgd_rank(self):
        rows = list(parse_nimdm(_make_csv(ROWS)))
        self.assertEqual(len(rows), 20)
        self.assertEqual(rows[0], ("S1", "N09000003", 1))


class NimdmIngestTests(TestCase):
    def setUp(self):
        _seed_indicator()
        make_source("Northern Ireland Multiple Deprivation Measure", "NISRA")
        for gss, name in [("N09000003", "Belfast"), ("N09000005", "Derry City and Strabane"),
                          ("N09000006", "Fermanagh and Omagh")]:
            make_place(gss, name, tier=PlaceTier.LAD, valid_from=date(2019, 12, 1))

    def _val(self, gss):
        return PlaceObservation.objects.get(place__gss_code=gss, indicator__code=CODE).value

    def test_decile_share_and_point_observation(self):
        call_command("ingest_nimdm", path=_make_csv(ROWS), vintage="NIMDM-test")
        self.assertEqual(PlaceObservation.objects.count(), 3)     # one per LGD
        self.assertEqual(self._val("N09000003"), Decimal("50.00"))  # 2/4 in decile 1
        self.assertEqual(self._val("N09000006"), Decimal("0.00"))
        o = PlaceObservation.objects.get(place__gss_code="N09000003")
        self.assertEqual(o.period_type, PeriodType.POINT)
        self.assertEqual(o.vintage, "NIMDM-test")

    def test_code_join_no_alias_needed(self):
        # LGD2014code IS the N09 GSS code -> direct join, all districts resolve.
        call_command("ingest_nimdm", path=_make_csv(ROWS), vintage="NIMDM-test")
        self.assertEqual(
            set(PlaceObservation.objects.values_list("place__gss_code", flat=True)),
            {"N09000003", "N09000005", "N09000006"})

    def test_unmatched_lgd_fails_loudly(self):
        rows = [("N09999999", "S1", 1)] + ROWS[1:]
        with self.assertRaises(CommandError):
            call_command("ingest_nimdm", path=_make_csv(rows), vintage="NIMDM-test")

    def test_idempotent(self):
        path = _make_csv(ROWS)
        call_command("ingest_nimdm", path=path, vintage="NIMDM-test")
        call_command("ingest_nimdm", path=path, vintage="NIMDM-test")
        self.assertEqual(PlaceObservation.objects.count(), 3)


class NimdmCoverageTests(TestCase):
    def setUp(self):
        _seed_indicator()

    def test_ni_place_shows_caption_no_absence_note(self):
        from core.models import Indicator
        p = make_place("N09000003", "Belfast", tier=PlaceTier.LAD)
        src = make_source("Northern Ireland Multiple Deprivation Measure", "NISRA")
        from .factories import make_observation
        ind = Indicator.objects.get(code=CODE)
        make_observation(ind, p, src, value=Decimal("27"), period_start=date(2017, 11, 23),
                         period_end=date(2017, 11, 23), period_type="POINT", vintage="NIMDM2017")
        pay = series_payload(p, ind)
        self.assertEqual(pay["coverage"], "Northern Ireland only")
        self.assertNotIn("Northern Ireland only", {n["note"] for n in coverage_notes(p)})
        self.assertTrue(pay["descriptor"])

    def test_non_ni_place_flags_nimdm(self):
        for gss, name in [("E07000223", "Adur"), ("W06000019", "Blaenau Gwent"),
                          ("S12000049", "Glasgow City")]:
            p = make_place(gss, name, tier=PlaceTier.LAD)
            self.assertIn("Northern Ireland only", {n["note"] for n in coverage_notes(p)})
