"""Tiny helpers to build objects for the tests without a factory library."""

from datetime import date

from core.models import (
    Indicator,
    IndicatorDomain,
    Place,
    PlaceCrosswalk,
    PlaceObservation,
    PlaceTier,
    Source,
    SubjectScope,
    ValueType,
)


def make_domain(code="economy", name="Economy"):
    return IndicatorDomain.objects.create(code=code, name=name)


def make_indicator(domain=None, *, code="gva-balanced-total", is_additive=True,
                   value_type=ValueType.CURRENCY, unit="£m", name=None):
    return Indicator.objects.create(
        code=code,
        name=name or code.replace("-", " ").title(),
        domain=domain or make_domain(),
        unit=unit,
        value_type=value_type,
        is_additive=is_additive,
        subject_scope=SubjectScope.PLACE,
    )


def make_source(name="ONS Regional accounts", publisher="Office for National Statistics"):
    return Source.objects.create(name=name, publisher=publisher)


def make_place(gss_code, name=None, *, tier=PlaceTier.LAD, valid_from=date(2024, 5, 1),
               valid_to=None):
    return Place.objects.create(
        gss_code=gss_code,
        name=name or gss_code,
        tier=tier,
        valid_from=valid_from,
        valid_to=valid_to,
    )


def make_observation(indicator, place, source, *, value,
                     period_start=date(2021, 1, 1), period_end=date(2021, 12, 31),
                     period_type="CALENDAR_YEAR", vintage="2024-04", unit=""):
    return PlaceObservation.objects.create(
        indicator=indicator,
        place=place,
        source=source,
        value=value,
        period_start=period_start,
        period_end=period_end,
        period_type=period_type,
        vintage=vintage,
        unit=unit,
    )


def make_crosswalk(from_place, to_place, weight, basis):
    return PlaceCrosswalk.objects.create(
        from_place=from_place, to_place=to_place, weight=weight, basis=basis,
    )
