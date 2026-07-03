"""
CLAUDE.md: "Place versioning: 2019 and 2024 constituency sets coexist as
distinct rows." Also checks the nation-from-GSS-prefix derivation.
"""

from datetime import date

from django.db import IntegrityError, transaction
from django.test import TestCase

from core.models import Nation, Place, PlaceTier

from .factories import make_place


class PlaceVersioningTests(TestCase):
    def test_two_boundary_versions_coexist(self):
        same_code = "E14000123"
        old = make_place(
            same_code, "Old seat", tier=PlaceTier.WPC,
            valid_from=date(2019, 12, 12), valid_to=date(2024, 7, 3),
        )
        new = make_place(
            same_code, "New seat", tier=PlaceTier.WPC,
            valid_from=date(2024, 7, 4), valid_to=None,
        )
        self.assertNotEqual(old.pk, new.pk)
        self.assertEqual(Place.objects.filter(gss_code=same_code).count(), 2)

    def test_same_code_same_valid_from_rejected(self):
        make_place("E07000010", valid_from=date(2024, 5, 1))
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                make_place("E07000010", valid_from=date(2024, 5, 1))

    def test_nation_derived_from_gss_prefix(self):
        cases = {
            "E07000001": Nation.ENGLAND,
            "W06000001": Nation.WALES,
            "S12000001": Nation.SCOTLAND,
            "N09000001": Nation.NORTHERN_IRELAND,
            "K02000001": Nation.UNKNOWN,
        }
        for code, expected in cases.items():
            place = make_place(code, valid_from=date(2024, 5, 1))
            self.assertEqual(place.nation, expected, code)
