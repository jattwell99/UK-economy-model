"""
Crosswalk roll-ups — the one piece of *application* logic the schema defers to code.

The spec is explicit (uk_place_engine_v1_spec.md 5.7): the crosswalk lets you
move a value between non-nesting tiers, but only additive indicators may be
summed. Summing a rate, ratio, per-head or index across places is always wrong,
so this module refuses it rather than returning a plausible-but-meaningless number.
"""

from decimal import Decimal

from .models import PlaceCrosswalk, PlaceObservation


class NonAdditiveRollupError(ValueError):
    """Raised when a roll-up is attempted on a non-additive indicator."""


def rollup_place_value(
    indicator,
    to_place,
    *,
    period_start,
    period_end,
    basis,
    source=None,
    vintage=None,
):
    """Apportion additive observations from overlapping places onto ``to_place``.

    Sums ``value * weight`` over every PlaceCrosswalk ending at ``to_place`` with
    the given ``basis``, taking each ``from_place``'s single latest-vintage
    observation for the period (never summing across vintages — a restatement is
    a new row, not extra magnitude). Pin a specific edition with ``vintage``.

    Raises NonAdditiveRollupError if ``indicator.is_additive`` is False — a rate
    or per-head figure must never be summed across places.

    Returns a Decimal (the apportioned total), or None if no contributing
    observations were found.
    """
    if not indicator.is_additive:
        raise NonAdditiveRollupError(
            f"Indicator {indicator.code!r} is not additive "
            f"({indicator.get_value_type_display()}); it cannot be rolled up "
            "through the crosswalk. Only counts and currency totals may be summed."
        )

    crosswalks = PlaceCrosswalk.objects.filter(
        to_place=to_place, basis=basis,
    ).select_related("from_place")

    total = None
    for xw in crosswalks:
        obs = PlaceObservation.objects.filter(
            indicator=indicator,
            place=xw.from_place,
            period_start=period_start,
            period_end=period_end,
        )
        if source is not None:
            obs = obs.filter(source=source)
        if vintage is not None:
            obs = obs.filter(vintage=vintage)

        # One value per from_place: the latest vintage for this period. Never Sum
        # across vintages — a restatement is a new row, not extra magnitude. (Same
        # root cause as double-plotting all vintages on the explore surface: pick
        # the latest vintage per period, or pin one with the `vintage` argument.)
        value = obs.order_by("-vintage").values_list("value", flat=True).first()
        if value is None:
            continue
        contribution = value * xw.weight
        total = contribution if total is None else total + contribution

    return total if total is None else Decimal(total)
