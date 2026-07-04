"""
Phase 4a — civic: 2024 general election at the WPC tier.

Covers the derivations (turnout from all ballots cast, winner share incl. the
non-party "other winner" case, majority), WPC matching, and the additive flags.
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
    PlaceObservation,
    PlaceTier,
    ValueType,
)

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
