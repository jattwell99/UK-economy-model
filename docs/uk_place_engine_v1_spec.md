# UK place-centric research engine — V1 data model spec

**Status:** build-ready for out-of-the-box Django admin.
**Scope:** "For any UK place, what's the economic activity and how are outcomes
trending" — explore-the-data, no rankings.
**Spine:** place-centric; two place tiers (LAD + Westminster constituency);
organisations hung off a deep activity tree; two shared-dimension observation
(fact) tables.

---

## 1. Design principles (read first)

These are the decisions the schema encodes. If a future change violates one of
these, stop and reconsider.

1. **Place is the spine.** Almost everything ladders up to geography. The joins
   are the product.
2. **Entities are versioned; observations are not.** Entities (Place,
   Organisation) carry `valid_from`/`valid_to` because their boundaries and
   identities change. Observations are a plain dated fact table — there is no
   "current GVA with history behind it", only GVA-for-2021, GVA-for-2022, each a row.
3. **Metrics that move over time are observations, never columns.** A metric as a
   column can hold only "now". Anything you want to trend lives in
   PlaceObservation or OrganisationObservation. Genuinely static facts (an org's
   founding year, its primary type) stay as columns.
4. **Provenance is in the schema from day one.** `source` + `vintage` are part of
   the natural key of every observation, because publishers (ONS especially)
   restate figures — (indicator, place, period) is *not* unique.
5. **Depth lives in the trees, not the records.** ActivityClass and
   IndicatorDomain are deep, self-referencing taxonomies. Organisation itself is
   deliberately thin.
6. **No opinions in the data layer.** No polarity, no scores, no rankings, no
   "how a place is doing" verdicts. That is a separate editorial layer for later.
   The data layer only exposes joinable facts.
7. **Surrogate primary keys everywhere; natural keys as indexed unique fields.**
   Natural keys (GSS codes, register IDs) change and retire; never make them the
   target of a foreign key.

---

## 2. Entity list

| # | Table | Role | Type |
|---|-------|------|------|
| 1 | Place | The spine — every geography, versioned | Entity |
| 2 | PlaceCrosswalk | Apportionment weights between tiers that don't nest | Bridge |
| 3 | IndicatorDomain | Taxonomy tree for grouping indicators | Dimension (tree) |
| 4 | Indicator | What is measured | Dimension |
| 5 | Source | Where a value came from | Dimension |
| 6 | PlaceObservation | Dated measurements of places | Fact |
| 7 | Organisation | Companies, bodies, institutions | Entity |
| 8 | OrganisationIdentifier | External register IDs (entity-resolution anchor) | Attribute |
| 9 | OrganisationSite | Where an org physically sits (org ↔ place) | Bridge |
| 10 | ActivityClass | The deep organisation taxonomy | Dimension (tree) |
| 11 | OrganisationClassification | Org ↔ activity tagging | Bridge |
| 12 | OrganisationObservation | Dated measurements of organisations | Fact |

Entities 1-6 and 10 (Place spine, dimensions, PlaceObservation, ActivityClass)
are implemented in `core/models.py` (Phases 0-2). Entities 7-9, 11-12 (the
Organisation cluster) are specified here but deferred to a later build phase.

---

## 3. Table specifications

Field types are given in Django terms. `on_delete` defaults to PROTECT for
dimension references and CASCADE for owned children, as noted.

### 3.1 Place

The single geography table. Replaces any Cities/Countries/Regions/States split.

| Field | Type | Null | Notes |
|-------|------|------|-------|
| id | AutoField PK | no | Surrogate. FKs point here, not at the GSS code. |
| gss_code | CharField(9), indexed | no | ONS GSS code, e.g. E07000223. Not unique on its own — see constraint. |
| name | CharField | no | |
| tier | CharField choices | no | LAD, WPC. Reserve COUNTRY, REGION, ITL1/2/3, LSOA, MSOA. |
| parent | FK → self | yes | Nesting *within* a clean hierarchy only. null for tiers that don't nest (constituencies). on_delete=PROTECT. |
| valid_from | DateField | no | Start of this boundary version's validity. |
| valid_to | DateField | yes | null = current. The 2024 constituency set and the 2019 set are distinct rows. |

**Constraints:** `UniqueConstraint(["gss_code", "valid_from"], name="uq_place_code_version")`
**Indexes:** gss_code, tier (and nation, derived from the GSS prefix on save).

**Django gotchas**
- `parent` is a self-FK over thousands of rows → set `autocomplete_fields =
  ["parent"]` or the admin renders a `<select>` with every place in it.
- The rule "a WPC must not have an LAD parent" can't be enforced by the FK.
  Enforce in `Model.clean()` or treat as convention for V1.

### 3.2 PlaceCrosswalk

The price of having two non-nesting tiers in V1. Lets a constituency figure be
apportioned across the LADs it overlaps (and vice versa). Seed from ONS
best-fit / exact-fit lookups.

| Field | Type | Null | Notes |
|-------|------|------|-------|
| id | AutoField PK | no | |
| from_place | FK → Place | no | related_name="crosswalks_from", on_delete=CASCADE. |
| to_place | FK → Place | no | related_name="crosswalks_to", on_delete=CASCADE. |
| weight | DecimalField(7,6) | no | Apportionment fraction 0–1. |
| basis | CharField choices | no | POPULATION, HOUSEHOLDS, AREA, WARD_COUNT (interim). |

**Constraints:** `UniqueConstraint(["from_place", "to_place", "basis"], name="uq_crosswalk")`

**Notes**
- Directional. Weights are **not symmetric** (A→B denominator differs from B→A),
  so store each direction you intend to aggregate.
- Only additive indicators may be rolled up through the crosswalk — see
  `Indicator.is_additive` (§3.4) and `core.aggregation.rollup_place_value`.

### 3.3 IndicatorDomain

Shallow taxonomy tree for grouping indicators. Adjacency-list is fine here.

| Field | Type | Null | Notes |
|-------|------|------|-------|
| id | AutoField PK | no | |
| code | SlugField, unique | no | e.g. economy, labour-market. |
| name | CharField | no | |
| parent | FK → self | yes | on_delete=PROTECT. |

**Suggested top-level seed:** Economy, Labour market, Housing, Health, Education,
Civic & democratic, Community & social.

### 3.4 Indicator

What is measured. `is_additive` and `value_type` are load-bearing for correct
aggregation.

| Field | Type | Null | Notes |
|-------|------|------|-------|
| id | AutoField PK | no | |
| code | SlugField, unique | no | e.g. gva-per-head, employment-rate. |
| name | CharField | no | |
| description | TextField | yes | Definition, caveats. |
| unit | CharField | no | £, %, count, £ per head. |
| domain | FK → IndicatorDomain | no | on_delete=PROTECT. |
| value_type | CharField choices | no | COUNT, CURRENCY, RATE, RATIO, INDEX. |
| is_additive | BooleanField | no | True = safe to sum across places (counts, £ totals). False = never sum (rates, ratios, per-head, indices). Governs crosswalk roll-ups. |
| subject_scope | CharField choices | no | PLACE, ORGANISATION, BOTH. Which observation table(s) it's valid in. |

**Deliberately omitted:** any polarity / "higher is better" field. That is the
seed of ranking and is out of scope for the explore-only V1.

### 3.5 Source

Minimal provenance dimension. This table exists so every value carries
attribution now.

| Field | Type | Null | Notes |
|-------|------|------|-------|
| id | AutoField PK | no | |
| name | CharField | no | e.g. "ONS Regional GVA (balanced)". |
| publisher | CharField | no | e.g. "Office for National Statistics". |
| url | URLField | yes | |
| licence | CharField | yes | e.g. "OGL v3.0". |
| release_date | DateField | yes | |

### 3.6 PlaceObservation — fact table

The centre of the star. Your V1 question is one query against this table:
`WHERE indicator=X AND place=Y ORDER BY period_start`.

| Field | Type | Null | Notes |
|-------|------|------|-------|
| id | AutoField PK | no | |
| indicator | FK → Indicator | no | on_delete=PROTECT. |
| place | FK → Place | no | Points at a specific boundary version. on_delete=PROTECT. |
| period_start | DateField | no | |
| period_end | DateField | no | |
| period_type | CharField choices | no | CALENDAR_YEAR, FINANCIAL_YEAR, QUARTER, MONTH, POINT. |
| value | DecimalField(18,4) | no | |
| unit | CharField | yes | Inherits indicator.unit if blank; override for edge cases. |
| source | FK → Source | no | on_delete=PROTECT. |
| vintage | CharField | no | Release edition / restatement id. **Non-null on purpose** (see gotcha 2 in §5). |
| status | CharField choices | yes | PROVISIONAL, REVISED, FINAL. |

**Constraints:** `UniqueConstraint(["indicator", "place", "period_start",
"period_end", "source", "vintage"], name="uq_place_obs")`
**Indexes:** (place, indicator, period_start), (indicator, period_start).

### 3.7–3.12 Organisation cluster (deferred)

Organisation (thin: name, org_type, founded, status), OrganisationIdentifier
(scheme+value unique — the entity-resolution anchor), OrganisationSite (org ↔
place, one primary via partial unique), ActivityClass (§3.10, implemented),
OrganisationClassification (org ↔ activity, one primary), and
OrganisationObservation (identical shape to PlaceObservation, subject swapped to
org). See the entity list; these are specified for a later phase.

### 3.10 ActivityClass — the deep tree (implemented)

| Field | Type | Null | Notes |
|-------|------|------|-------|
| id | AutoField PK | no | |
| code | CharField, unique | no | SIC code or bespoke code. |
| name | CharField | no | |
| parent | FK → self | yes | on_delete=PROTECT. |
| scheme | CharField | no | e.g. SIC-2007. Lets multiple taxonomies coexist. |
| level | PositiveSmallIntegerField | — | Cached depth (0 section, 1 division, ...). |

**Recommendation:** if subtree queries and a drag-and-drop tree admin become
important, migrate this one model to django-treebeard (or mptt). The seed writes
`level` to ease that. Adjacency-list is the zero-dependency V1 choice.

---

## 4. Shared abstractions

- **ObservationBase** — an abstract model holding period_start, period_end,
  period_type, value, unit, source, vintage, status. PlaceObservation (and later
  OrganisationObservation) inherit it and add their subject FK + unique
  constraint. Implemented in `core/models.py`.
- Keep the two trees separate (one may become treebeard, one adjacency) rather
  than forcing a shared base.

---

## 5. Django admin gotchas — consolidated checklist

1. **Self-referencing FKs** (Place.parent, IndicatorDomain.parent,
   ActivityClass.parent): always `autocomplete_fields`, never a plain select over
   thousands of rows.
2. **Composite natural-key uniqueness + NULLs:** on Postgres, NULLs are treated
   as distinct in unique constraints, so a nullable `vintage` would silently allow
   duplicate observations. Keep `vintage` **non-null** (sentinel "unversioned"),
   or on PG15+/Django 5 set `nulls_distinct=False` on the constraint.
3. **Partial uniqueness for "primary":** enforce one-primary-per-org for sites and
   classifications with `UniqueConstraint(..., condition=Q(is_primary=True))`.
4. **Big fact tables in admin:** `autocomplete_fields` on every FK,
   `list_select_related` to kill N+1, `date_hierarchy = "period_start"`, and
   `list_filter` on indicator__domain.
5. **Dual FK to Place** in PlaceCrosswalk: distinct related_names, autocomplete both.
6. **Deep tree admin:** ActivityClass wants treebeard's tree admin at depth.
7. **Aggregation is application logic, not schema:** the crosswalk rolls up
   between tiers **only** where `Indicator.is_additive = True`. Summing a rate or
   per-head figure across places is always wrong — `core.aggregation` enforces this.

---

## 6. What is deliberately NOT in V1

- **Org-to-org relationships** (ownership, funding, partnership) — Phase 2.
- **Geometry / PostGIS** — the crosswalk handles tier-to-tier apportionment
  without polygons, so V1 is pure Django. Add later for point-in-polygon queries.
- **Polarity, scores, indices, rankings** — preserves the explore-only stance.
- **Source ingestion machinery** — the schema only needs to *carry* provenance
  (Source + vintage), which it does.
- **Confidence / method scoring** beyond source+vintage.

---

## 7. Build sequence

1. **Dimensions:** Source, IndicatorDomain → Indicator, ActivityClass.
2. **Spine:** Place (both tiers), then PlaceCrosswalk (seed from ONS lookups).
3. **Place facts:** PlaceObservation — prove the V1 query end to end with one
   indicator (GVA is a good first vertical).
4. **Org cluster:** Organisation → OrganisationIdentifier, OrganisationSite,
   OrganisationClassification → OrganisationObservation.

Prove the thin vertical (one indicator, one region, source → place tiers → trend
query) before loading breadth.
