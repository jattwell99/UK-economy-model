# UK place-centric research engine

A research platform modelling UK economic activity and societal outcomes **by
place**, built to civic-tool rigour. V1 goal: for any UK place, show economic
activity and how outcomes are trending — *explore the data, no rankings, scores,
or opinions in the data layer.*

Django + PostgreSQL, with the Django admin as the V1 data-management surface.

- **Data model spec (source of truth):** [`docs/uk_place_engine_v1_spec.md`](docs/uk_place_engine_v1_spec.md)
- **Build sequence & data-source catalogue:** [`docs/uk_place_engine_v1_build_plan.md`](docs/uk_place_engine_v1_build_plan.md)
- **Working conventions for contributors (and Claude):** [`CLAUDE.md`](CLAUDE.md)

## What's here (Phases 0–2)

The geography spine, dimensions and the SIC activity tree — the foundation the
rest of the engine hangs off:

| Model | Role |
|-------|------|
| `Place` | Every geography (LAD, Westminster constituency), versioned by boundary set; `nation` derived from the GSS code prefix. |
| `PlaceCrosswalk` | Directional apportionment weights between non-nesting tiers. |
| `IndicatorDomain` / `Indicator` / `Source` | The measurement + provenance dimensions. |
| `PlaceObservation` | The dated fact table — the centre of the star schema. |
| `ActivityClass` | The deep SIC-2007 organisation taxonomy (adjacency-list). |

The Organisation cluster (Organisation, sites, identifiers, classifications,
observations) is specified in the spec but deferred to a later build phase — one
phase per session, per the build order.

## Quick start

Requires Python 3.11+ and PostgreSQL (via Docker or local).

```bash
# 1. Dependencies
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2. Postgres (Docker) + config
docker compose up -d db
cp .env.example .env            # defaults already match the db service

# 3. Schema
python manage.py migrate

# 4. Seed dimensions + the SIC tree (idempotent)
python manage.py seed_v1 --dimensions --sic
#   full SIC division/group/class tree:
#   python manage.py seed_v1 --sic --sic-csv path/to/sic_codes.csv

# 5. Admin
python manage.py createsuperuser
python manage.py runserver        # http://127.0.0.1:8000/admin/
```

### Seeding geography & the crosswalk

These pull from live ONS sources whose URLs and CSV column names drift between
editions — **confirm them against the live source first** (see
`core/management/commands/seed_v1.py` → `GEO_SOURCES` and `LOOKUP_COLUMNS`).

```bash
python manage.py seed_v1 --geography
python manage.py seed_v1 --crosswalk --lookup-csv path/to/ward_lookup.csv
```

### Ingesting observations (Phases 2–3)

```bash
# Phase 2 — ONS regional GVA (balanced) by LAD (one .xlsx per ITL1 region):
python manage.py ingest_gva --path data/gva/ --dry-run
python manage.py ingest_gva --path data/gva/

# Phase 3 — LAD population, then derive GVA per head (GVA total x 1e6 / population):
python manage.py ingest_population --path data/gva/populationestimatesbylocalauthority.xlsx --vintage 2020-06-mye
python manage.py derive_per_head
```

All three are idempotent (upsert on the observation natural key) and report LAD
codes that don't match the loaded boundary set as expected boundary churn.
Source workbooks live in a gitignored `data/` — they aren't committed.

## Design guarantees (and their tests)

Three properties are load-bearing and covered by `core/tests/`:

- **`is_additive` gates crosswalk roll-ups.** `core.aggregation.rollup_place_value`
  sums additive indicators (counts, £ totals) across apportioned places and
  **raises** on rates / ratios / per-head figures — summing those is always wrong.
- **Places are versioned.** The 2019 and 2024 constituency sets coexist as
  distinct rows; `(gss_code, valid_from)` is unique.
- **Observations are append-only by vintage.** A restatement is a new row; an
  identical `(indicator, place, period, source, vintage)` is rejected. `vintage`
  is non-null so Postgres doesn't treat repeats as distinct NULLs.

```bash
python manage.py test core
```

## Why Postgres from day one

The observation uniqueness design depends on Postgres NULL-in-unique-constraint
semantics, and PostGIS is planned for a later phase. sqlite is not supported.
