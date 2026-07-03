"""
Explore surface — the read layer (docs/explore_surface_v1_brief.md).

Central guarantee: the series selector shows ONE point per period = the latest
vintage, even when a (place, indicator, period) has multiple vintages. Plus
smoke tests for both routes and the provenance label.
"""

from datetime import date
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from core.models import PlaceTier, ValueType
from core.selectors import (
    indicators_for_place,
    latest_series,
    places_with_observations,
    series_payload,
)

from .factories import (
    make_domain,
    make_indicator,
    make_observation,
    make_place,
    make_source,
)


class SeriesSelectorTests(TestCase):
    def setUp(self):
        self.gva = make_indicator(
            make_domain("economy", "Economy"), code="gva-balanced-total",
            is_additive=True, value_type=ValueType.CURRENCY, unit="£m",
        )
        self.place = make_place("E08000003", "Manchester", tier=PlaceTier.LAD)
        self.source = make_source("ONS Regional accounts (GVA / GDP / GDHI)")

    def test_one_point_per_period_across_vintages(self):
        """Two vintages of the same (place, indicator, period) -> one displayed point."""
        for yr in (2016, 2017, 2018):
            make_observation(self.gva, self.place, self.source, value=Decimal(yr),
                             period_start=date(yr, 1, 1), period_end=date(yr, 12, 31),
                             vintage="2019-12-19")
        # A later restatement of 2018 only.
        make_observation(self.gva, self.place, self.source, value=Decimal("9999"),
                         period_start=date(2018, 1, 1), period_end=date(2018, 12, 31),
                         vintage="2020-12-16")

        rows = list(latest_series(self.place, self.gva))
        years = [o.period_start.year for o in rows]
        self.assertEqual(years, [2016, 2017, 2018])          # exactly one per period
        self.assertEqual(len(years), len(set(years)))         # no duplicate periods
        latest_2018 = next(o for o in rows if o.period_start.year == 2018)
        self.assertEqual(latest_2018.value, Decimal("9999"))  # latest vintage wins
        self.assertEqual(latest_2018.vintage, "2020-12-16")

    def test_payload_points_and_provenance(self):
        make_observation(self.gva, self.place, self.source, value=Decimal("22550"),
                         period_start=date(2018, 1, 1), period_end=date(2018, 12, 31),
                         vintage="2019-12-19")
        payload = series_payload(self.place, self.gva)
        self.assertEqual(payload["unit"], "£m")
        self.assertEqual(payload["points"], [{"year": 2018, "value": 22550.0}])
        self.assertEqual(
            payload["provenance"],
            [{"source": "ONS Regional accounts (GVA / GDP / GDHI)", "vintage": "2019-12-19"}],
        )


class RouteTests(TestCase):
    def setUp(self):
        econ = make_domain("economy", "Economy")
        self.gva = make_indicator(econ, code="gva-balanced-total",
                                  is_additive=True, value_type=ValueType.CURRENCY, unit="£m")
        self.source = make_source("ONS Regional accounts (GVA / GDP / GDHI)")
        self.mcr = make_place("E08000003", "Manchester", tier=PlaceTier.LAD)
        self.leeds = make_place("E08000035", "Leeds", tier=PlaceTier.LAD)
        # Manchester has data; Leeds does not — so Leeds is excluded from the list.
        make_observation(self.gva, self.mcr, self.source, value=Decimal("22550"),
                         period_start=date(2018, 1, 1), period_end=date(2018, 12, 31),
                         vintage="2019-12-19")

    def test_place_list_only_places_with_observations(self):
        resp = self.client.get(reverse("explore:place_list"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Manchester")
        self.assertNotContains(resp, "Leeds")   # no observations -> not listed

    def test_place_list_search(self):
        resp = self.client.get(reverse("explore:place_list"), {"q": "manch"})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Manchester")

    def test_place_detail_renders_chart_and_provenance(self):
        resp = self.client.get(reverse("explore:place_detail", args=["E08000003"]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Manchester")
        self.assertContains(resp, "<canvas")                 # a chart is rendered
        self.assertContains(resp, "vintage 2019-12-19")      # provenance label
        self.assertContains(resp, "charts-data")             # chart JSON payload

    def test_place_detail_unknown_code_404(self):
        resp = self.client.get(reverse("explore:place_detail", args=["E99999999"]))
        self.assertEqual(resp.status_code, 404)

    def test_no_ranking_language_on_detail(self):
        resp = self.client.get(reverse("explore:place_detail", args=["E08000003"]))
        body = resp.content.decode().lower()
        for word in ("rank", "decile", "verdict", "best", "worst"):
            self.assertNotIn(word, body)
