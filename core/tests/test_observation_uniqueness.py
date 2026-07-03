"""
CLAUDE.md: "Observation uniqueness: same place/period/indicator with a different
vintage = two rows; identical = rejected by the constraint."

This is the provenance guarantee — publishers restate figures, so vintage is
part of the natural key, and it is non-null so Postgres does not treat repeated
rows as distinct.
"""

from datetime import date
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.test import TestCase

from .factories import make_domain, make_indicator, make_observation, make_place, make_source


class ObservationUniquenessTests(TestCase):
    def setUp(self):
        self.indicator = make_indicator(make_domain())
        self.source = make_source()
        self.place = make_place("E07000001", valid_from=date(2024, 5, 1))

    def test_different_vintage_creates_two_rows(self):
        make_observation(self.indicator, self.place, self.source,
                         value=Decimal("100"), vintage="2024-04")
        make_observation(self.indicator, self.place, self.source,
                         value=Decimal("105"), vintage="2025-04")  # restatement
        self.assertEqual(
            self.place.observations.filter(indicator=self.indicator).count(), 2
        )

    def test_identical_natural_key_rejected(self):
        make_observation(self.indicator, self.place, self.source,
                         value=Decimal("100"), vintage="2024-04")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                make_observation(self.indicator, self.place, self.source,
                                 value=Decimal("999"), vintage="2024-04")

    def test_default_vintage_is_non_null_sentinel(self):
        # Two rows that omit vintage collide on the sentinel rather than both
        # slipping through as "distinct NULLs".
        obs = make_observation(self.indicator, self.place, self.source,
                               value=Decimal("100"), vintage="unversioned")
        self.assertEqual(obs.vintage, "unversioned")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                make_observation(self.indicator, self.place, self.source,
                                 value=Decimal("200"), vintage="unversioned")
