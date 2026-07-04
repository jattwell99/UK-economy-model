"""
Phase 4a — civic: 2024 general election at the WPC tier.
Phase 4b — civic: historic GEs (2015/2017/2019) on the OLD boundary set.

Covers the derivations (turnout from all ballots cast, winner share incl. the
non-party "other winner" case AND the 2015 reconstruction from Majority + runner-up,
dynamic per-year party columns), WPC matching, the additive flags, and the
old/new boundary coexistence + election-date-window resolver.
"""

import tempfile
from datetime import date
from decimal import Decimal

from django.core.management import call_command
from django.test import TestCase

from core.aggregation import NonAdditiveRollupError, rollup_place_value
from core.management.commands.ingest_elections import parse_results
from core.models import (
    CrosswalkBasis,
    PeriodType,
    Place,
    PlaceObservation,
    PlaceTier,
    ValueType,
)
from core.selectors import ambiguous_gss_codes, places_with_observations, resolve_place

from .factories import make_crosswalk, make_domain, make_indicator, make_observation, make_place, make_source

# ONS ID, Constituency name, First party, Electorate, Valid votes, Invalid votes,
# Majority, Lab, Con, "Of which other winner"
CSV = (
    "ONS ID,Constituency name,First party,Electorate,Valid votes,Invalid votes,Majority,Lab,Con,Of which other winner\n"
    "E14000001,Testville,Lab,100000,60000,100,10000,30000,20000,0\n"     # party winner
    "E14000002,Independentia,Ind,80000,50000,50,5000,10000,9000,25000\n"  # non-party winner
    "E14009999,Nowhere,Lab,50000,25000,0,1000,13000,12000,0\n"            # unmatched (no Place)
)


def _seed_civic_indicators():
    civic = make_domain("civic", "Civic & democratic")
    make_indicator(civic, code="turnout", is_additive=False, value_type=ValueType.RATE, unit="%")
    make_indicator(civic, code="winning-party-vote-share", is_additive=False, value_type=ValueType.RATE, unit="%")
    make_indicator(civic, code="majority", is_additive=True, value_type=ValueType.COUNT, unit="count")


class ElectionsParseTests(TestCase):
    def test_derivations(self):
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as fh:
            fh.write(CSV)
            path = fh.name
        rows = {code: vals for code, name, vals, winner in parse_results(path)}
        # Party winner: turnout=(60000+100)/100000=60.1%; share=30000/60000=50%; maj=10000
        v = rows["E14000001"]
        self.assertEqual(v["turnout"], Decimal("60.10"))
        self.assertEqual(v["winning-party-vote-share"], Decimal("50.00"))
        self.assertEqual(v["majority"], Decimal("10000"))
        # Non-party winner (Ind): winner votes from "Of which other winner"=25000
        # turnout=(50000+50)/80000=62.56%; share=25000/50000=50%
        v2 = rows["E14000002"]
        self.assertEqual(v2["turnout"], Decimal("62.56"))
        self.assertEqual(v2["winning-party-vote-share"], Decimal("50.00"))


class ElectionsIngestTests(TestCase):
    def setUp(self):
        _seed_civic_indicators()
        make_source("House of Commons Library — elections", "House of Commons Library")
        make_place("E14000001", "Testville", tier=PlaceTier.WPC)
        make_place("E14000002", "Independentia", tier=PlaceTier.WPC)
        # E14009999 deliberately has no Place -> unmatched

    def _ingest(self):
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as fh:
            fh.write(CSV)
            path = fh.name
        call_command("ingest_elections", path=path)

    def test_writes_point_observations_on_wpc(self):
        self._ingest()
        # 2 matched constituencies x 3 indicators = 6; the unmatched one dropped
        self.assertEqual(PlaceObservation.objects.count(), 6)
        o = PlaceObservation.objects.get(place__gss_code="E14000001", indicator__code="turnout")
        self.assertEqual(o.period_type, PeriodType.POINT)
        self.assertEqual(o.period_start, date(2024, 7, 4))
        self.assertEqual(o.period_end, date(2024, 7, 4))
        self.assertEqual(o.value, Decimal("60.10"))
        self.assertEqual(o.vintage, "GE2024")

    def test_idempotent(self):
        self._ingest()
        self._ingest()
        self.assertEqual(PlaceObservation.objects.count(), 6)


class CivicRollupTests(TestCase):
    def setUp(self):
        self.civic = make_domain("civic", "Civic & democratic")
        self.src = make_source("House of Commons Library — elections")
        self.a = make_place("E14000001", "A", tier=PlaceTier.WPC)
        self.b = make_place("E14000002", "B", tier=PlaceTier.WPC)
        self.target = make_place("E14000003", "T", tier=PlaceTier.WPC)
        make_crosswalk(self.a, self.target, Decimal("1.000000"), CrosswalkBasis.POPULATION)

    def test_turnout_and_share_refuse_rollup(self):
        for code, vt in [("turnout", ValueType.RATE), ("winning-party-vote-share", ValueType.RATE)]:
            ind = make_indicator(self.civic, code=code, is_additive=False, value_type=vt, unit="%")
            make_observation(ind, self.a, self.src, value=Decimal("60"),
                             period_start=date(2024, 7, 4), period_end=date(2024, 7, 4),
                             period_type="POINT")
            with self.assertRaises(NonAdditiveRollupError):
                rollup_place_value(ind, self.target, period_start=date(2024, 7, 4),
                                   period_end=date(2024, 7, 4), basis=CrosswalkBasis.POPULATION)

    def test_majority_is_additive(self):
        maj = make_indicator(self.civic, code="majority", is_additive=True,
                             value_type=ValueType.COUNT, unit="count")
        make_observation(maj, self.a, self.src, value=Decimal("10000"),
                         period_start=date(2024, 7, 4), period_end=date(2024, 7, 4),
                         period_type="POINT")
        # additive: does not raise (rolls up the apportioned value)
        total = rollup_place_value(maj, self.target, period_start=date(2024, 7, 4),
                                   period_end=date(2024, 7, 4), basis=CrosswalkBasis.POPULATION)
        self.assertEqual(total, Decimal("10000"))


# --- Phase 4b: historic boundary set -------------------------------------------

# 2015-style file: NO "Of which other winner" column, UKIP among the party columns,
# and a Speaker win whose votes must be reconstructed as Majority + runner-up.
CSV_2015 = (
    "ONS ID,Constituency name,First party,Second party,Electorate,Valid votes,Invalid votes,Majority,Con,Lab,LD,UKIP,Green,All other candidates\n"
    "E14000763,Islington N,Lab,Con,70000,42000,80,15000,12000,27000,2000,1000,0,0\n"      # party (Lab) winner
    "E14000608,Buckingham,Spk,UKIP,77000,53000,1200,22000,0,0,0,11000,0,20000\n"          # Speaker: 22000+11000
)

# 2019-style file: BRX (not UKIP), and "Of which other winner" present.
CSV_2019 = (
    "ONS ID,Constituency name,First party,Second party,Electorate,Valid votes,Invalid votes,Majority,Con,Lab,LD,BRX,Green,All other candidates,Of which other winner\n"
    "E14000763,Islington N,Lab,Con,72000,50000,90,26000,10000,36000,3000,500,500,0,0\n"    # BRX column present, Lab wins
    "E14000637,Chorley,Spk,Lab,75000,48000,60,17000,0,0,0,0,0,48000,26000\n"               # Speaker via "other winner"
)


class HistoricParseTests(TestCase):
    def test_2015_reconstructs_non_party_winner_from_majority_plus_runner_up(self):
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as fh:
            fh.write(CSV_2015)
            path = fh.name
        rows = {code: vals for code, name, vals, winner in parse_results(path)}
        # Speaker seat: no "Of which other winner" column, so winner = Majority + runner-up
        # (UKIP) = 22000 + 11000 = 33000; share = 33000/53000 = 62.26%.
        self.assertEqual(rows["E14000608"]["winning-party-vote-share"], Decimal("62.26"))
        # turnout uses all ballots: (53000 + 1200) / 77000 = 70.39%.
        self.assertEqual(rows["E14000608"]["turnout"], Decimal("70.39"))
        # Party winner still read from its named column (Lab = 27000 / 42000 = 64.29%).
        self.assertEqual(rows["E14000763"]["winning-party-vote-share"], Decimal("64.29"))

    def test_2019_uses_other_winner_column_and_brx_party_set(self):
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as fh:
            fh.write(CSV_2019)
            path = fh.name
        rows = {code: vals for code, name, vals, winner in parse_results(path)}
        # Speaker: "Of which other winner" = 26000 / 48000 = 54.17%.
        self.assertEqual(rows["E14000637"]["winning-party-vote-share"], Decimal("54.17"))
        # BRX column doesn't break the Lab win: 36000 / 50000 = 72.00%.
        self.assertEqual(rows["E14000763"]["winning-party-vote-share"], Decimal("72.00"))


class BoundaryWindowTests(TestCase):
    """The election-date-window resolver, old/new coexistence, and versioned routing."""

    OLD_FROM, OLD_TO = date(2010, 5, 6), date(2024, 7, 3)
    NEW_FROM = date(2024, 7, 4)

    def setUp(self):
        civic = make_domain("civic", "Civic & democratic")
        make_indicator(civic, code="turnout", is_additive=False, value_type=ValueType.RATE, unit="%")
        make_indicator(civic, code="winning-party-vote-share", is_additive=False, value_type=ValueType.RATE, unit="%")
        make_indicator(civic, code="majority", is_additive=True, value_type=ValueType.COUNT, unit="count")
        # A colliding code: the 2024 (new) Place pre-exists; the old one is created on load.
        make_place("S14000021", "East Renfrewshire", tier=PlaceTier.WPC, valid_from=self.NEW_FROM)

    def _write(self, text):
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as fh:
            fh.write(text)
            return fh.name

    def test_historic_load_creates_old_places_and_attaches_by_date(self):
        csv_hist = (
            "ONS ID,Constituency name,First party,Second party,Electorate,Valid votes,Invalid votes,Majority,Con,Lab,LD,UKIP,Green,All other candidates\n"
            "S14000021,East Renfrewshire,Con,Lab,60000,45000,100,5000,25000,20000,0,0,0,0\n"
        )
        call_command("ingest_elections", path=self._write(csv_hist),
                     election_date="2015-05-07", vintage="GE2015",
                     boundary_valid_from="2010-05-06", boundary_valid_to="2024-07-03")
        # Two Place rows now share the code — distinct boundary versions.
        rows = Place.objects.filter(tier=PlaceTier.WPC, gss_code="S14000021").order_by("valid_from")
        self.assertEqual([p.valid_from for p in rows], [self.OLD_FROM, self.NEW_FROM])
        # The 2015 result attached to the OLD row, not the 2024 one.
        old = rows[0]
        obs = PlaceObservation.objects.get(place=old, indicator__code="turnout")
        self.assertEqual(obs.vintage, "GE2015")
        self.assertEqual(obs.period_start, date(2015, 5, 7))
        self.assertFalse(PlaceObservation.objects.filter(place=rows[1]).exists())

    def test_old_and_new_coexist_as_a_trend_plus_single_dot(self):
        hist = ("ONS ID,Constituency name,First party,Second party,Electorate,Valid votes,Invalid votes,Majority,Con,Lab,LD,UKIP,Green,All other candidates\n"
                "S14000021,East Renfrewshire,Con,Lab,60000,45000,100,5000,25000,20000,0,0,0,0\n")
        new = ("ONS ID,Constituency name,First party,Second party,Electorate,Valid votes,Invalid votes,Majority,Con,Lab,LD,RUK,Green,All other candidates,Of which other winner\n"
               "S14000021,East Renfrewshire,Lab,Con,58000,40000,80,3000,18000,21000,0,0,0,0,0\n")
        for v, d in [("GE2015", "2015-05-07"), ("GE2017", "2017-06-08"), ("GE2019", "2019-12-12")]:
            call_command("ingest_elections", path=self._write(hist), election_date=d, vintage=v,
                         boundary_valid_from="2010-05-06", boundary_valid_to="2024-07-03")
        call_command("ingest_elections", path=self._write(new), election_date="2024-07-04", vintage="GE2024")
        old = resolve_place("S14000021", valid_from=self.OLD_FROM)
        cur = resolve_place("S14000021")  # newest
        self.assertEqual(cur.valid_from, self.NEW_FROM)
        # Old seat carries a 3-election turnout trend; new seat a single point.
        old_turnout = PlaceObservation.objects.filter(place=old, indicator__code="turnout")
        new_turnout = PlaceObservation.objects.filter(place=cur, indicator__code="turnout")
        self.assertEqual(old_turnout.count(), 3)
        self.assertEqual(new_turnout.count(), 1)

    def test_colliding_code_flagged_ambiguous_for_versioned_url(self):
        # Give both rows an observation so both appear in the explore list.
        Place.objects.get_or_create(gss_code="S14000021", valid_from=self.OLD_FROM,
                                    defaults={"name": "East Renfrewshire", "tier": PlaceTier.WPC,
                                              "valid_to": self.OLD_TO})
        src = make_source("House of Commons Library — elections")
        from core.models import Indicator
        turnout = Indicator.objects.get(code="turnout")
        for p in Place.objects.filter(gss_code="S14000021"):
            make_observation(turnout, p, src, value=Decimal("70"),
                             period_start=date(2020, 1, 1), period_end=date(2020, 1, 1),
                             period_type="POINT")
        amb = ambiguous_gss_codes(list(places_with_observations()))
        self.assertIn("S14000021", amb)
