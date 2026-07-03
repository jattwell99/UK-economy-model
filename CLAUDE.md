# CLAUDE.md — UK place-centric research engine

## What this is

A personal research engine modelling UK economic activity and societal outcomes
by place, built to civic-tool rigour. V1 goal: for any UK place, show economic
activity and how outcomes are trending. **Explore-the-data — no rankings, scores,
or opinions in the data layer.**

## Source of truth

- `docs/uk_place_engine_v1_spec.md` — the data model. Authoritative for tables,
  fields, keys, constraints.
- `docs/uk_place_engine_v1_build_plan.md` — build sequence and data-source catalogue.
- If code and docs disagree, either the docs win or you update the docs **in the
  same change**. Never let them drift.

## Stack (decided — don't re-litigate without asking)

- Django + Django admin as the V1 data-management surface.
- **PostgreSQL from day one, not sqlite.** The observation uniqueness design
  depends on Postgres NULL-in-unique-constraint semantics, and PostGIS comes
  later. Run locally via docker-compose (`docker-compose up -d db`).
- Python 3.12+ (dev container here is 3.11; keep code 3.11-compatible), a single
  Django app: `core`.
- Ingestion = Django management commands, one per source, following the existing
  `seed_v1` pattern (`core/management/commands/`).

## Layout

```
config/            Django project (settings read from env; see .env.example)
core/
  models.py        Place, PlaceCrosswalk, IndicatorDomain, Indicator, Source,
                   PlaceObservation, ActivityClass (Phases 0-2)
  admin.py         Admin with the autocomplete / list_select_related gotchas applied
  aggregation.py   Crosswalk roll-up gated by Indicator.is_additive
  management/commands/
    seed_v1.py       Dimensions, SIC tree, geography (LAD Dec-2019 + WPC), crosswalk
    ingest_gva.py    ONS regional GVA (balanced) by LAD -> PlaceObservation (Phase 2)
    ingest_population.py  ONS mid-year LAD population estimates (Phase 3)
    derive_per_head.py    gva-per-head = GVA total x 1e6 / population (Phase 3)
  tests/           Early guarantees + the GVA and population/per-head verticals
docs/              Spec + build plan (source of truth)
```

## Build order — one phase per session, do not skip ahead

1. Geography spine + crosswalk (Place, PlaceCrosswalk), seeded from ONS.
2. Dimensions (IndicatorDomain, Indicator, Source) + the SIC ActivityClass tree.
3. **First vertical:** ingest ONS regional GVA at LAD into PlaceObservation;
   prove the trend query and a crosswalk roll-up to constituency. GO/NO-GO gate.
4. Place breadth: economy, labour (Nomis), housing (Land Registry HPI).
5. Outcomes: health (Fingertips), deprivation, civic (election results at WPC tier).
6. Organisation cluster: Companies House + Charity Commission.

Each phase ends in: migration applied + a test + something verifiable in admin.

**Status:** Phases 0-3 are in place and verified against Postgres:
- 0-2: schema, admin, seed scaffold, early tests; geography loaded (382 LADs
  Dec-2019 + 650 WPCs); GVA balanced total ingested (7,980 obs, vintage
  2019-12-19), trend + crosswalk roll-up proven (the go/no-go gate).
- 3: ONS LAD population estimates ingested (8,162 obs); `gva-per-head` derived
  from GVA total / population (7,749 obs) against a "Derived" source with a
  dual-input vintage. `population` is additive; `gva-per-head` is not.

Organisation cluster models (Organisation, OrganisationIdentifier,
OrganisationSite, OrganisationClassification, OrganisationObservation) are
specified in the spec but deliberately not yet added — they belong to a later
session per the build order.

## Conventions

- Surrogate PKs everywhere; natural keys are unique + indexed fields.
- Every FK in admin uses `autocomplete_fields`; fact tables get `list_filter`,
  `date_hierarchy`, and `list_select_related`.
- Observations are append-only by vintage: a new release is a **new row**, never
  an overwrite.
- `is_additive` gates crosswalk roll-ups. Summing a rate/ratio/per-head across
  places is always wrong — `core.aggregation.rollup_place_value` refuses it.

## Confirm, don't assume

- ONS ArcGIS FeatureServer URLs and CSV column names drift between editions —
  verify against the live source before trusting an ingest run.
- Crosswalk weights are interim (ward-count) until population best-fit weights
  from LSOA/OA lookups are wired in.
- The UK is four statistical systems. `nation` is first-class on Place. Never
  create a single UK-wide deprivation indicator — model each nation separately.

## Tests worth writing early (all present in `core/tests/`)

- Crosswalk roll-up: additive indicators sum; non-additive ones raise, not sum.
- Place versioning: 2019 and 2024 constituency sets coexist as distinct rows.
- Observation uniqueness: same place/period/indicator with a different vintage =
  two rows; identical = rejected by the constraint.

Run them with `python manage.py test core`.

## Working style

Prefer small, verifiable steps and paste-ready output. Flag errors and dubious
assumptions directly. Engage as a peer.
