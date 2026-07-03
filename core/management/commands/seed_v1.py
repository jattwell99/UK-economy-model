"""
Seed command for the UK place engine — Phases 0 and 1.

Steps are independent and idempotent. Run in this order:

    python manage.py seed_v1 --dimensions
    python manage.py seed_v1 --sic
    python manage.py seed_v1 --sic --sic-csv path/to/sic_codes.csv
    python manage.py seed_v1 --geography
    python manage.py seed_v1 --crosswalk --lookup-csv path/to/ward_lookup.csv

Or everything (once the two external inputs are ready):

    python manage.py seed_v1 --all --sic-csv sic.csv --lookup-csv ward_lookup.csv

Two external inputs you must supply / confirm:
  1. The LAD boundaries FeatureServer URL (see GEO_SOURCES below) — grab the
     current one from the ONS Open Geography Portal; the year suffix changes.
  2. The ONS "Ward to Westminster Parliamentary Constituency to LAD to UTLA
     (July 2024)" lookup CSV — download it and pass with --lookup-csv.
"""

import csv
import json
import re
import urllib.parse
import urllib.request
from datetime import date

from django.core.management.base import BaseCommand
from django.db import transaction

from core.models import (
    ActivityClass,
    CrosswalkBasis,
    Indicator,
    IndicatorDomain,
    Place,
    PlaceCrosswalk,
    PlaceTier,
    Source,
    SubjectScope,
    ValueType,
)


# ---------------------------------------------------------------------------
# Static seed data
# ---------------------------------------------------------------------------

DOMAINS = [
    ("economy", "Economy"),
    ("labour-market", "Labour market"),
    ("housing", "Housing"),
    ("health", "Health"),
    ("education", "Education"),
    ("civic", "Civic & democratic"),
    ("community", "Community & social"),
]

# code, name, domain, unit, value_type, is_additive, subject_scope
INDICATORS = [
    ("gva-balanced-total", "GVA (balanced), total", "economy", "£m", ValueType.CURRENCY, True, SubjectScope.PLACE),
    ("gva-per-head", "GVA per head", "economy", "£", ValueType.RATIO, False, SubjectScope.PLACE),
    ("gdhi-per-head", "Gross disposable household income per head", "economy", "£", ValueType.RATIO, False, SubjectScope.PLACE),
    ("employment-rate-16-64", "Employment rate (16-64)", "labour-market", "%", ValueType.RATE, False, SubjectScope.PLACE),
    ("claimant-count", "Claimant count", "labour-market", "count", ValueType.COUNT, True, SubjectScope.PLACE),
    ("median-weekly-pay", "Median gross weekly pay (residence)", "labour-market", "£", ValueType.RATIO, False, SubjectScope.PLACE),
    ("jobs-density", "Jobs density", "labour-market", "ratio", ValueType.RATIO, False, SubjectScope.PLACE),
    ("median-house-price", "Median house price", "housing", "£", ValueType.RATIO, False, SubjectScope.PLACE),
    ("healthy-life-expectancy-birth", "Healthy life expectancy at birth", "health", "years", ValueType.RATIO, False, SubjectScope.PLACE),
    ("life-expectancy-birth", "Life expectancy at birth", "health", "years", ValueType.RATIO, False, SubjectScope.PLACE),
    ("imd-most-deprived-decile-share", "Share of LSOAs in most-deprived decile", "community", "%", ValueType.RATE, False, SubjectScope.PLACE),
    ("turnout", "Turnout", "civic", "%", ValueType.RATE, False, SubjectScope.PLACE),
    ("winning-party-vote-share", "Winning-party vote share", "civic", "%", ValueType.RATE, False, SubjectScope.PLACE),
    ("majority", "Majority", "civic", "count", ValueType.COUNT, True, SubjectScope.PLACE),
]

# name, publisher, url, licence
SOURCES = [
    ("ONS Open Geography Portal", "Office for National Statistics", "https://geoportal.statistics.gov.uk", "OGL v3.0"),
    ("ONS Regional accounts (GVA / GDP / GDHI)", "Office for National Statistics", "https://www.ons.gov.uk", "OGL v3.0"),
    ("Nomis", "Office for National Statistics", "https://www.nomisweb.co.uk", "OGL v3.0"),
    ("UK House Price Index", "HM Land Registry", "https://landregistry.data.gov.uk", "OGL v3.0"),
    ("OHID Fingertips", "Office for Health Improvement and Disparities", "https://fingertips.phe.org.uk", "OGL v3.0"),
    ("English Indices of Deprivation", "Ministry of Housing, Communities and Local Government", "https://www.gov.uk", "OGL v3.0"),
    ("House of Commons Library — elections", "House of Commons Library", "https://commonslibrary.parliament.uk", "Parliamentary reuse"),
    ("Companies House", "Companies House", "https://developer.company-information.service.gov.uk", "Companies House terms"),
    ("Charity Commission", "Charity Commission for England and Wales", "https://register-of-charities.charitycommission.gov.uk", "Charity Commission terms"),
    ("postcodes.io / ONS Postcode Directory", "Office for National Statistics", "https://postcodes.io", "OGL v3.0"),
]

# SIC 2007 sections: letter, name, first division, last division (inclusive)
SIC_SECTIONS = [
    ("A", "Agriculture, forestry and fishing", 1, 3),
    ("B", "Mining and quarrying", 5, 9),
    ("C", "Manufacturing", 10, 33),
    ("D", "Electricity, gas, steam and air conditioning supply", 35, 35),
    ("E", "Water supply; sewerage, waste management and remediation activities", 36, 39),
    ("F", "Construction", 41, 43),
    ("G", "Wholesale and retail trade; repair of motor vehicles and motorcycles", 45, 47),
    ("H", "Transportation and storage", 49, 53),
    ("I", "Accommodation and food service activities", 55, 56),
    ("J", "Information and communication", 58, 63),
    ("K", "Financial and insurance activities", 64, 66),
    ("L", "Real estate activities", 68, 68),
    ("M", "Professional, scientific and technical activities", 69, 75),
    ("N", "Administrative and support service activities", 77, 82),
    ("O", "Public administration and defence; compulsory social security", 84, 84),
    ("P", "Education", 85, 85),
    ("Q", "Human health and social work activities", 86, 88),
    ("R", "Arts, entertainment and recreation", 90, 93),
    ("S", "Other service activities", 94, 96),
    ("T", "Activities of households as employers and own-use production", 97, 98),
    ("U", "Activities of extraterritorial organisations and bodies", 99, 99),
]

# Geography feeds from the ONS Open Geography Portal (ArcGIS REST).
# WPC July 2024 URL is current as of build; CONFIRM before running.
# LAD URL is intentionally blank — paste the current LAD boundaries
# FeatureServer from the portal and set the matching field names.
GEO_SOURCES = {
    "WPC": {
        "tier": PlaceTier.WPC,
        "valid_from": date(2024, 7, 4),
        "feature_server": (
            "https://services1.arcgis.com/ESMARspQHYMw9BZ9/arcgis/rest/services/"
            "Westminster_Parliamentary_Constituencies_July_2024_Boundaries_UK_BFC/FeatureServer"
        ),
        "code_field": "PCON24CD",
        "name_field": "PCON24NM",
    },
    "LAD": {
        "tier": PlaceTier.LAD,
        "valid_from": date(2024, 5, 1),   # set to the boundary set date you load
        "feature_server": "",             # TODO: paste current LAD boundaries FeatureServer URL
        "code_field": "LAD24CD",
        "name_field": "LAD24NM",
    },
}

# Column names in the ONS ward lookup CSV. CONFIRM against the file header —
# the year suffix and exact casing vary between editions.
LOOKUP_COLUMNS = {
    "wpc_code": "PCON24CD",
    "wpc_name": "PCON24NM",
    "lad_code": "LAD24CD",
    "lad_name": "LAD24NM",
}


class Command(BaseCommand):
    help = "Seed dimensions, SIC tree, geography spine and the WPC<->LAD crosswalk."

    def add_arguments(self, parser):
        parser.add_argument("--all", action="store_true")
        parser.add_argument("--dimensions", action="store_true")
        parser.add_argument("--sic", action="store_true")
        parser.add_argument("--geography", action="store_true")
        parser.add_argument("--crosswalk", action="store_true")
        parser.add_argument("--sic-csv", dest="sic_csv", default=None,
                            help="CSV of SIC codes: columns 'code','description'.")
        parser.add_argument("--lookup-csv", dest="lookup_csv", default=None,
                            help="ONS ward -> WPC -> LAD lookup CSV.")

    def handle(self, *args, **opts):
        run_all = opts["all"]
        if run_all or opts["dimensions"]:
            self.seed_dimensions()
        if run_all or opts["sic"]:
            self.seed_sic(opts["sic_csv"])
        if run_all or opts["geography"]:
            self.seed_geography()
        if run_all or opts["crosswalk"]:
            self.seed_crosswalk(opts["lookup_csv"])
        self.stdout.write(self.style.SUCCESS("Seed run complete."))

    # -- dimensions ---------------------------------------------------------

    @transaction.atomic
    def seed_dimensions(self):
        for code, name in DOMAINS:
            IndicatorDomain.objects.get_or_create(code=code, defaults={"name": name})
        domains = {d.code: d for d in IndicatorDomain.objects.all()}

        for code, name, dcode, unit, vt, additive, scope in INDICATORS:
            Indicator.objects.update_or_create(
                code=code,
                defaults={
                    "name": name, "domain": domains[dcode], "unit": unit,
                    "value_type": vt, "is_additive": additive, "subject_scope": scope,
                },
            )
        for name, pub, url, lic in SOURCES:
            Source.objects.get_or_create(
                name=name, publisher=pub,
                defaults={"url": url, "licence": lic},
            )
        self.stdout.write(
            f"Dimensions: {IndicatorDomain.objects.count()} domains, "
            f"{Indicator.objects.count()} indicators, {Source.objects.count()} sources."
        )

    # -- SIC activity tree --------------------------------------------------

    @transaction.atomic
    def seed_sic(self, sic_csv):
        cache = {}   # code (letter or digits) -> ActivityClass
        division_to_section = {}
        for letter, name, lo, hi in SIC_SECTIONS:
            node, _ = ActivityClass.objects.get_or_create(
                code=letter, scheme="SIC-2007",
                defaults={"name": name, "parent": None, "level": 0},
            )
            cache[letter] = node
            for d in range(lo, hi + 1):
                division_to_section[f"{d:02d}"] = letter
        self.division_to_section = division_to_section

        if sic_csv:
            with open(sic_csv, newline="", encoding="utf-8-sig") as fh:
                reader = csv.DictReader(fh)
                cols = {c.lower(): c for c in reader.fieldnames or []}
                code_col = cols.get("code") or (reader.fieldnames or [None])[0]
                desc_col = cols.get("description") or cols.get("name")
                for row in reader:
                    digits = re.sub(r"\D", "", row.get(code_col, ""))
                    if not (2 <= len(digits) <= 5):
                        continue
                    name = (row.get(desc_col) or "").strip() if desc_col else ""
                    self._ensure_activity(digits, name, cache)

        self.stdout.write(f"SIC tree: {ActivityClass.objects.count()} nodes.")

    def _ensure_activity(self, digits, name, cache):
        if digits in cache:
            node = cache[digits]
            if name and node.name.startswith("("):   # upgrade placeholder name
                node.name = name
                node.save(update_fields=["name"])
            return node

        if len(digits) == 2:
            parent = cache.get(self.division_to_section.get(digits))
            level = 1
        else:
            parent = self._ensure_activity(digits[:-1], "", cache)
            level = len(digits) - 1

        node, _ = ActivityClass.objects.get_or_create(
            code=digits, scheme="SIC-2007",
            defaults={"name": name or f"(SIC {digits})", "parent": parent, "level": level},
        )
        cache[digits] = node
        return node

    # -- geography ----------------------------------------------------------

    @transaction.atomic
    def seed_geography(self):
        for key, cfg in GEO_SOURCES.items():
            fs = cfg["feature_server"]
            if not fs:
                self.stdout.write(self.style.WARNING(
                    f"{key}: no FeatureServer URL set — skipping. "
                    f"Paste the current URL into GEO_SOURCES['{key}']."
                ))
                continue
            rows = self._fetch_arcgis(fs, [cfg["code_field"], cfg["name_field"]])
            created = 0
            for attrs in rows:
                code = attrs.get(cfg["code_field"])
                name = attrs.get(cfg["name_field"])
                if not code:
                    continue
                _, was_created = Place.objects.get_or_create(
                    gss_code=code, valid_from=cfg["valid_from"],
                    defaults={"name": name, "tier": cfg["tier"]},
                )
                created += int(was_created)
            self.stdout.write(f"{key}: {len(rows)} fetched, {created} new places.")

    @staticmethod
    def _fetch_arcgis(feature_server, out_fields):
        base = feature_server.rstrip("/") + "/0/query"
        rows, offset, page = [], 0, 2000
        while True:
            params = {
                "where": "1=1",
                "outFields": ",".join(out_fields),
                "returnGeometry": "false",
                "f": "json",
                "resultOffset": offset,
                "resultRecordCount": page,
            }
            url = base + "?" + urllib.parse.urlencode(params)
            with urllib.request.urlopen(url, timeout=90) as resp:
                data = json.load(resp)
            feats = data.get("features", [])
            if not feats:
                break
            rows.extend(f["attributes"] for f in feats)
            if len(feats) < page:
                break
            offset += page
        return rows

    # -- crosswalk ----------------------------------------------------------

    @transaction.atomic
    def seed_crosswalk(self, lookup_csv):
        if not lookup_csv:
            self.stdout.write(self.style.WARNING(
                "--crosswalk needs --lookup-csv (the ONS ward -> WPC -> LAD lookup). Skipping."
            ))
            return

        col = LOOKUP_COLUMNS
        pair_wards = {}        # (wpc_code, lad_code) -> ward count
        wpc_total = {}         # wpc_code -> ward count
        lad_total = {}         # lad_code -> ward count

        with open(lookup_csv, newline="", encoding="utf-8-sig") as fh:
            for row in csv.DictReader(fh):
                wpc, lad = row.get(col["wpc_code"]), row.get(col["lad_code"])
                if not (wpc and lad):
                    continue
                pair_wards[(wpc, lad)] = pair_wards.get((wpc, lad), 0) + 1
                wpc_total[wpc] = wpc_total.get(wpc, 0) + 1
                lad_total[lad] = lad_total.get(lad, 0) + 1

        made = 0
        for (wpc, lad), n in pair_wards.items():
            p_wpc = self._current_place(wpc)
            p_lad = self._current_place(lad)
            if not (p_wpc and p_lad):
                continue
            # Interim WARD_COUNT weighting. TODO: replace with population best-fit
            # weights from LSOA/OA lookups + population estimates before using
            # roll-ups for anything quantitative.
            self._upsert_weight(p_wpc, p_lad, n / wpc_total[wpc])
            self._upsert_weight(p_lad, p_wpc, n / lad_total[lad])
            made += 2

        self.stdout.write(
            f"Crosswalk: {made} directional rows over {len(pair_wards)} overlaps "
            f"(basis=WARD_COUNT, interim)."
        )

    @staticmethod
    def _current_place(gss_code):
        return (
            Place.objects.filter(gss_code=gss_code)
            .order_by("-valid_from")
            .first()
        )

    @staticmethod
    def _upsert_weight(from_place, to_place, weight):
        PlaceCrosswalk.objects.update_or_create(
            from_place=from_place, to_place=to_place,
            basis=CrosswalkBasis.WARD_COUNT,
            defaults={"weight": round(weight, 6)},
        )
