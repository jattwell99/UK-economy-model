"""
Read-side query helpers for the explore surface (docs/explore_surface_v1_brief.md).

The one rule that matters here: observations are append-only by vintage, so a
(place, indicator, period) can have several rows. Everything user-facing shows
ONE series per indicator = the latest vintage per period. Never all vintages.
"""

from .models import Indicator, Place, PlaceObservation, PlaceTier


# Tiers that carry explorable observations (LAD economy/labour/housing, WPC civic).
EXPLORE_TIERS = [PlaceTier.LAD, PlaceTier.WPC]


def places_with_observations(search=None):
    """Places (LAD + WPC) with at least one observation, name-searchable, by name."""
    qs = (
        Place.objects.filter(tier__in=EXPLORE_TIERS, observations__isnull=False)
        .distinct()
        .order_by("name", "tier")
    )
    if search:
        qs = qs.filter(name__icontains=search)
    return qs


def resolve_place(gss_code):
    """Resolve a GSS code to a single Place (latest boundary version). Tier-agnostic —
    a GSS code is unique to its tier (E14… constituency vs E08… local authority)."""
    return (
        Place.objects.filter(tier__in=EXPLORE_TIERS, gss_code=gss_code)
        .order_by("-valid_from")
        .first()
    )


def indicators_for_place(place):
    """Distinct indicators that have observations for this place."""
    return (
        Indicator.objects.filter(placeobservation__place=place)
        .distinct()
        .order_by("domain__code", "code")
    )


def latest_series(place, indicator):
    """One observation per period — the latest vintage wins.

    Postgres DISTINCT ON: order by the distinct field first, then -vintage so the
    row kept per period is the newest edition (see the brief).
    """
    return (
        PlaceObservation.objects.filter(place=place, indicator=indicator)
        .select_related("source", "indicator")
        .order_by("period_start", "-vintage")
        .distinct("period_start")
    )


def series_payload(place, indicator):
    """Chart-ready payload for one indicator: points + unit + provenance.

    provenance is the distinct (source, vintage) pairs actually shown — normally
    one, but surfaced honestly if a series spans editions.
    """
    rows = list(latest_series(place, indicator))
    points = [
        {"year": o.period_start.year, "value": float(o.value)}
        for o in rows
    ]
    provenance = []
    seen = set()
    for o in rows:
        key = (o.source.name, o.vintage)
        if key not in seen:
            seen.add(key)
            provenance.append({"source": o.source.name, "vintage": o.vintage})
    return {
        "indicator_code": indicator.code,
        "indicator_name": indicator.name,
        "unit": indicator.unit,
        "points": points,
        "provenance": provenance,
    }
