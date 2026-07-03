"""
Explore surface — the read layer (docs/explore_surface_v1_brief.md).

Two server-rendered routes over LAD place data. No rankings, no verdicts; every
chart carries its provenance. The vintage-safe series query lives in selectors.
"""

from django.http import Http404
from django.shortcuts import render

from .selectors import (
    indicators_for_place,
    places_with_observations,
    resolve_place,
    series_payload,
)


def place_list(request):
    """List LADs that have observations, with case-insensitive name search."""
    query = request.GET.get("q", "").strip()
    places = places_with_observations(query or None)
    return render(
        request,
        "explore/place_list.html",
        {"places": places, "query": query},
    )


def place_detail(request, gss_code):
    """Place header + one trend chart per indicator (latest vintage per period)."""
    place = resolve_place(gss_code)
    if place is None:
        raise Http404("No local authority with observations for that GSS code.")
    charts = [series_payload(place, ind) for ind in indicators_for_place(place)]
    return render(
        request,
        "explore/place_detail.html",
        {"place": place, "charts": charts, "indicator_count": len(charts)},
    )
