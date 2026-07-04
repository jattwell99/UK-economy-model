# Comparison-over-time tool — scoping brief

Save at `docs/comparison_tool_scoping_brief.md`. This is a **scoping** brief, not a
build brief: its first job is to force one design decision (§3, conservative vs peer)
before any code is written. Everything else follows from that call.

Companion to the map + time slider brief — these two are halves of one idea: the map
answers **where**, the comparison answers **how it's changed and versus whom**.

## 1. Purpose
Let a user compare a metric over time across several regions — pick an indicator, pick
a set of places, see each as a line over time so the shapes can be read against each
other. This is the "explore trends across comparable places" ask.

## 2. What "comparable" must mean (the structural rules — non-negotiable)
The engine already encodes these; the tool must enforce them or it produces
category errors:

- **Same tier.** Don't put a WPC turnout line and a LAD employment-rate line on one
  axis. The picker is scoped to one tier at a time.
- **Matching boundary vintage.** Comparing 2024 Islington North to 2019 Islington
  North is comparing two *different areas* that share a name — the false continuity we
  deliberately refused to stitch in the civic data. Same rule here: a comparison set
  is within one boundary version.
- **Normalised metric.** Comparing *total* GVA between the City of London and Cornwall
  is noise (size dominates); comparing GVA per-head or a rate is fair. Restrict the
  comparison indicators to **non-additive / normalised** ones (`is_additive = false`),
  or at minimum hard-warn when a total is charted across differently-sized places. Use
  the existing `is_additive` / `value_type` flags.

Within those three rules, the user picks places freely. The rules are about preventing
dishonest comparisons, not about which places are "similar" — that's §3.

## 3. THE DECISION TO MAKE FIRST — conservative vs peer
"Comparable" has two meanings, and they're a real fork. Decide before building.

**Path A — conservative (structurally valid).** The tool lets the user pick any places
freely, enforces the §2 rules, and warns on unfair comparisons. No classification, no
opinion about which places are alike — the user decides who's comparable; the tool
just stops category errors. Fully consistent with "no rankings/opinions in the data
layer."

**Path B — peer ("actually similar").** Add a "similar places" notion so "compare to
peers" means something automatically — group LADs by type (urban/rural, size band,
region, economic type) so a user can ask "how does Blackpool do against places *like*
Blackpool?" Much closer to the question people actually ask, and how ONS's own
local-area tools work. **But** it requires defining and storing a classification, which
is a data-modelling commitment and a mild step toward editorialising (someone decides
Blackpool's peer group).

**Recommendation:** build **A first** — it's genuinely useful, fully in keeping with the
project's principles, and it's the substrate B sits on anyway. Treat **B as a later,
separate decision**, and if taken, **adopt a published classification** (e.g. the ONS
area classifications or the rural–urban classification) as a *sourced, provenanced
dimension* rather than inventing one — that keeps it on the right side of the
facts/opinions firewall (it's a cited external fact, not the engine's judgment).

Note B is not just UX: it's a data-layer add (ingest a classification as a place
attribute/dimension, with a Source and vintage) *then* the UX. Scope it as its own
small phase if chosen.

## 4. Relationship to the map
The powerful version is **linked, not standalone**: the map is how you *select* the
comparison set — brush/click regions on the choropleth and they join the comparison
panel — so you build a peer set spatially instead of hunting a 380-name dropdown. The
comparison panel is where the trend lines live.

This is why sequencing matters (see the chat note): the map's click/brush-to-detail
plumbing is the same mechanism this tool rides on. A standalone list-picker version can
be built without the map, but the map-linked version is the one worth having.

## 5. The read path
- Reuse the explore surface's per-place series query (latest-vintage-per-period, one
  clean series per indicator) — run it for N places and return N series.
- Draw as N lines on one chart (Chart.js, already in the stack), or small-multiples if
  N is large. Shared axis only within the §2 rules.
- No new query machinery — it's the existing `series_payload` logic, parameterised by
  a list of places instead of one.

## 6. Honesty constraints (same discipline as the rest)
- **Missing data stays missing.** If a place lacks the indicator for some periods (or
  entirely — NI on GB-only labour, non-England on England-only outcomes), its line has
  gaps or is absent with a coverage note — never zero-filled, never interpolated across
  a real gap.
- **No ranking.** The tool plots lines for comparison; it does not order places
  "best→worst", colour them by rank, or compute a composite. Neutral series colours.
- **Warn, don't silently allow, unfair comparisons** (a total across sizes; mixed
  vintages). If Path A, the warning is the main guardrail.

## 7. Definition of done (Path A, V1)
- Pick a tier, a normalised indicator, and 2–N places (from a list and/or by
  selecting on the map).
- See N trend lines on a shared, honestly-comparable axis, with a legend and
  provenance.
- Structural rules enforced: single tier, single boundary vintage, normalised metric;
  a clear warning (or block) if the user tries to break them.
- Missing/partial coverage shown as gaps/notes, never zero-filled.
- No rankings or composites anywhere.
- Existing pages unregressed; tests cover the multi-place query and the
  non-comparable warnings.

## 8. Out of scope for the first build
- **Peer/"similar places" grouping (Path B)** — separate decision + a sourced
  classification dimension if taken.
- Cross-tier or cross-vintage comparison (the rules forbid it).
- Any normalisation the engine doesn't already publish (don't invent per-capita
  versions on the fly beyond what's stored/derived).
