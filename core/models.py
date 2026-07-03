"""
UK place-centric research engine — models for Phases 0-2.

Covers: Place (spine, versioned), PlaceCrosswalk (tier apportionment),
IndicatorDomain + Indicator + Source (dimensions), PlaceObservation (fact),
and ActivityClass (the SIC activity tree).

Design notes are in docs/uk_place_engine_v1_spec.md. Key choices honoured here:
- Surrogate PKs everywhere; natural keys are unique indexed fields.
- Entities are versioned (valid_from/valid_to); observations are a plain dated fact table.
- vintage is non-null so the observation uniqueness constraint holds on Postgres.
- is_additive gates crosswalk roll-ups (never sum a rate).
"""

from django.db import models


# ---------------------------------------------------------------------------
# Shared enums
# ---------------------------------------------------------------------------

class Nation(models.TextChoices):
    ENGLAND = "E", "England"
    WALES = "W", "Wales"
    SCOTLAND = "S", "Scotland"
    NORTHERN_IRELAND = "N", "Northern Ireland"
    UNKNOWN = "X", "Unknown / UK-wide"


# First character of a 9-char GSS code -> nation.
GSS_NATION_PREFIX = {
    "E": Nation.ENGLAND,
    "W": Nation.WALES,
    "S": Nation.SCOTLAND,
    "N": Nation.NORTHERN_IRELAND,
    "K": Nation.UNKNOWN,   # K##### = GB / UK-wide combined codes
}


class PlaceTier(models.TextChoices):
    LAD = "LAD", "Local authority district"
    WPC = "WPC", "Westminster parliamentary constituency"
    # Reserved for later phases (declared now so migrations don't churn):
    COUNTRY = "COUNTRY", "Country"
    REGION = "REGION", "Region / ITL1"
    ITL2 = "ITL2", "ITL2"
    ITL3 = "ITL3", "ITL3"
    MSOA = "MSOA", "MSOA"
    LSOA = "LSOA", "LSOA"


class CrosswalkBasis(models.TextChoices):
    POPULATION = "POPULATION", "Population"
    HOUSEHOLDS = "HOUSEHOLDS", "Households"
    AREA = "AREA", "Area"
    WARD_COUNT = "WARD_COUNT", "Ward count (interim)"


class ValueType(models.TextChoices):
    COUNT = "COUNT", "Count"
    CURRENCY = "CURRENCY", "Currency"
    RATE = "RATE", "Rate"
    RATIO = "RATIO", "Ratio"
    INDEX = "INDEX", "Index"


class SubjectScope(models.TextChoices):
    PLACE = "PLACE", "Place"
    ORGANISATION = "ORGANISATION", "Organisation"
    BOTH = "BOTH", "Both"


class PeriodType(models.TextChoices):
    CALENDAR_YEAR = "CALENDAR_YEAR", "Calendar year"
    FINANCIAL_YEAR = "FINANCIAL_YEAR", "Financial year"
    QUARTER = "QUARTER", "Quarter"
    MONTH = "MONTH", "Month"
    POINT = "POINT", "Point in time"


class ObservationStatus(models.TextChoices):
    PROVISIONAL = "PROVISIONAL", "Provisional"
    REVISED = "REVISED", "Revised"
    FINAL = "FINAL", "Final"


# ---------------------------------------------------------------------------
# Geography spine
# ---------------------------------------------------------------------------

class Place(models.Model):
    """A single geography of any tier, versioned by boundary set."""

    gss_code = models.CharField(max_length=9, db_index=True)
    name = models.CharField(max_length=200)
    tier = models.CharField(max_length=16, choices=PlaceTier.choices)
    nation = models.CharField(
        max_length=1, choices=Nation.choices, editable=False,
        help_text="Derived from the GSS code prefix on save.",
    )
    parent = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT,
        related_name="children",
        help_text="Only for tiers that nest cleanly. Null for constituencies.",
    )
    valid_from = models.DateField(help_text="Date this boundary set took effect.")
    valid_to = models.DateField(null=True, blank=True, help_text="Null = current.")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["gss_code", "valid_from"], name="uq_place_code_version",
            ),
        ]
        indexes = [
            models.Index(fields=["tier"]),
            models.Index(fields=["nation"]),
        ]
        ordering = ["tier", "name"]

    def save(self, *args, **kwargs):
        self.nation = GSS_NATION_PREFIX.get(
            (self.gss_code or "")[:1], Nation.UNKNOWN,
        )
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name} ({self.gss_code})"


class PlaceCrosswalk(models.Model):
    """Directional apportionment weight from one place to another.

    Lets a value be moved between non-nesting tiers (e.g. constituency <-> LAD).
    Weights are NOT symmetric, so store each direction you intend to aggregate.
    Only additive indicators may be rolled up through this (see Indicator.is_additive).
    """

    from_place = models.ForeignKey(
        Place, on_delete=models.CASCADE, related_name="crosswalks_from",
    )
    to_place = models.ForeignKey(
        Place, on_delete=models.CASCADE, related_name="crosswalks_to",
    )
    weight = models.DecimalField(
        max_digits=7, decimal_places=6,
        help_text="Apportionment fraction 0-1 of from_place attributable to to_place.",
    )
    basis = models.CharField(max_length=16, choices=CrosswalkBasis.choices)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["from_place", "to_place", "basis"], name="uq_crosswalk",
            ),
        ]

    def __str__(self):
        return f"{self.from_place.gss_code} -> {self.to_place.gss_code} ({self.weight})"


# ---------------------------------------------------------------------------
# Dimensions
# ---------------------------------------------------------------------------

class IndicatorDomain(models.Model):
    """Shallow taxonomy tree grouping indicators (Economy, Health, ...)."""

    code = models.SlugField(max_length=50, unique=True)
    name = models.CharField(max_length=120)
    parent = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT,
        related_name="children",
    )

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return self.name


class Indicator(models.Model):
    """What is measured. is_additive and value_type drive aggregation logic."""

    code = models.SlugField(max_length=80, unique=True)
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    unit = models.CharField(max_length=40)
    domain = models.ForeignKey(
        IndicatorDomain, on_delete=models.PROTECT, related_name="indicators",
    )
    value_type = models.CharField(max_length=16, choices=ValueType.choices)
    is_additive = models.BooleanField(
        default=False,
        help_text=(
            "True only for counts and currency totals that may be summed across "
            "places. Never True for rates, ratios, per-head or index values."
        ),
    )
    subject_scope = models.CharField(
        max_length=16, choices=SubjectScope.choices, default=SubjectScope.PLACE,
    )

    class Meta:
        ordering = ["domain__code", "code"]

    def __str__(self):
        return self.name


class Source(models.Model):
    """Provenance dimension. Every observation points at one."""

    name = models.CharField(max_length=200)
    publisher = models.CharField(max_length=200)
    url = models.URLField(blank=True)
    licence = models.CharField(max_length=80, blank=True)
    release_date = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["publisher", "name"]

    def __str__(self):
        return self.name


# ---------------------------------------------------------------------------
# Facts
# ---------------------------------------------------------------------------

class ObservationBase(models.Model):
    """Abstract dated measurement. PlaceObservation (and, later,
    OrganisationObservation) inherit this and add their subject FK + constraint."""

    indicator = models.ForeignKey(Indicator, on_delete=models.PROTECT)
    period_start = models.DateField()
    period_end = models.DateField()
    period_type = models.CharField(max_length=16, choices=PeriodType.choices)
    value = models.DecimalField(max_digits=18, decimal_places=4)
    unit = models.CharField(
        max_length=40, blank=True,
        help_text="Overrides indicator.unit when set.",
    )
    source = models.ForeignKey(Source, on_delete=models.PROTECT)
    vintage = models.CharField(
        max_length=60, default="unversioned",
        help_text=(
            "Release edition / restatement id. Non-null on purpose: NULLs are "
            "treated as distinct in Postgres unique constraints, which would let "
            "duplicate observations slip through."
        ),
    )
    status = models.CharField(
        max_length=16, choices=ObservationStatus.choices, blank=True,
    )

    class Meta:
        abstract = True


class PlaceObservation(ObservationBase):
    """A dated measurement of a place. The centre of the star schema."""

    place = models.ForeignKey(
        Place, on_delete=models.PROTECT, related_name="observations",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "indicator", "place", "period_start",
                    "period_end", "source", "vintage",
                ],
                name="uq_place_obs",
            ),
        ]
        indexes = [
            models.Index(fields=["place", "indicator", "period_start"]),
            models.Index(fields=["indicator", "period_start"]),
        ]
        ordering = ["place", "indicator", "period_start"]

    def __str__(self):
        return f"{self.indicator.code} @ {self.place.gss_code} {self.period_start:%Y}"


# ---------------------------------------------------------------------------
# Organisation activity taxonomy (the deep tree)
# ---------------------------------------------------------------------------

class ActivityClass(models.Model):
    """Self-referencing classification tree. Seeded from SIC 2007.

    Adjacency-list for zero-dependency V1. If subtree queries and a drag-and-drop
    tree admin become important, migrate this one model to django-treebeard --
    the seed command already writes a cached `level` to ease that.
    """

    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=255)
    parent = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT,
        related_name="children",
    )
    scheme = models.CharField(max_length=40, default="SIC-2007")
    level = models.PositiveSmallIntegerField(
        default=0, help_text="Cached depth: 0 section, 1 division, 2 group, ...",
    )

    class Meta:
        ordering = ["scheme", "code"]
        indexes = [models.Index(fields=["scheme"])]

    def __str__(self):
        return f"{self.code} {self.name}"
