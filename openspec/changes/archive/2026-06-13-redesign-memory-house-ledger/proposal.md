# redesign-memory-house-ledger

## Why

The current `/memory` page is functionally complete and structurally mute:
seven equal-weight card sections render three different memory kinds (Episodes,
Facts, Rules) through one rectangular tabbed table. The current
`dashboard-domain-pages` spec codifies that implementation at MUST level — a
fixed three-section layout, green/amber/destructive **health badges** on tier
cards, **confidence progress bars**, colored permanence/validity/maturity
**word badges**, **per-tab search** inputs, and page size 20. Five concrete
pains follow (verified against `frontend/src/pages/MemoryPage.tsx` and the
brief, `docs/redesigns/2026-06-12-memory-brief.md` §0):

1. **The lifecycle is invisible** — consolidation status, last-run time, and
   dead-letter count surface nowhere. A stalled pipeline silently corrupts
   everything downstream.
2. **Belief renders dishonestly flat** — confidence is a static progress bar
   and decay has no visual presence, so a fresh fact and a fading one read at
   identical weight.
3. **One table shape, three times** — directly against
   `about/heart-and-soul/design-language.md:59-62` ("Not a uniform information
   feed … Forcing them into one rectangular table view is a regression"); and
   provenance (`fact —derived_from→ episode`) exists in the data model and
   nowhere in the UI.
4. **Two duplicated search affordances** — a search box per tab violates
   one-affordance-per-signal.
5. **Housekeeping renders at the same hierarchy as knowledge** — retention,
   compaction, and re-embed shout as loud as the facts themselves.

This change rewrites the `/memory` page and its three detail-route requirements
to the **house-ledger grammar** defined in
`pr/overview/memory-redesign/MEMORY_LANGUAGE.md` (an extension of the canonical
Dispatch design language), with binding design intent captured in
`pr/overview/memory-redesign/VISION.md` and brief §0. It also enumerates the
read-side backend deltas (`dashboard-api`) so every new affordance has a
verified wire and no dead buttons ship (per prior FE→BE reconciliation
experience). It is a **doctrine-correcting** change: the patterns being retired
are explicitly the ones heart-and-soul doctrine bans.

## What Changes

- **BREAKING (UI contract):** retire the current `/memory` requirement set —
  three-section layout, tier cards with health badges, the tabbed
  table browser with per-tab search and confidence progress bars, colored
  permanence/validity/maturity word badges, and the right-sidebar activity
  card at knowledge hierarchy. They are replaced by the four-band house-ledger
  layout. (`MODIFIED` + `REMOVED` in `dashboard-domain-pages`.)
- **NEW page grammar (`dashboard-domain-pages`):** `/memory` becomes one
  1280px column in four bands top-to-bottom — **overture** (eyebrow / display
  headline / one Voice sentence / 4-cell KPI strip), **pipeline** (the
  lifecycle as a single mono line of numerals with `─→` connectors; the
  dead-letter numeral is the page's only in-band state color, and only when
  `> 0`), **registers + rail** (`grid-template-columns: 1.4fr 1fr`: one search
  input + kind pills + the focused register on the left; the attention rail +
  recent activity on the right, where all page state color lives), and
  **housekeeping** (a quiet bottom band).
- **NEW three register shapes (`dashboard-domain-pages`):** the unified table is
  replaced by three kind-specific shapes — the **ledger** (Facts, default), the
  **standing orders** (Rules), and the **daybook** (Episodes) — each with its
  own row template and rhythm. Metaphor governs *form*, never nouns: UI labels
  stay Episodes / Facts / Rules.
- **NEW belief typography (`dashboard-domain-pages`):** confidence is rendered
  as a mono tabular numeral (never a bar/donut/percent); decay dims the row
  foreground to `--dim` at the fading threshold (never color/strikethrough);
  permanence is a two-letter mono tag; consolidation state is a glyph
  `{◦ • ✕}`; rule maturity is a lowercase mono word; importance is conveyed by
  ink weight. The fading threshold uses **effective (decayed)** confidence.
- **NEW one-search rule (`dashboard-domain-pages`):** exactly one search
  affordance, kind-scoped via pills, backed by `/api/memory/inspect`; results
  render in the register shape of their kind. Page size is **50** (offset-based).
- **NEW attention rail (`dashboard-domain-pages`):** five condition rows; the
  rail and the pipeline dead-letter numeral are the only places state color
  appears. The **"write-up overdue" rail row is action-less** — it MUST NOT
  carry a "run consolidation now" affordance, ever (cost guard, brief §4).
- **MODIFIED detail pages (`dashboard-domain-pages`):** Fact / Rule / Episode
  detail pages adopt one editorial skeleton (eyebrow / content-as-heading /
  state line / KV band / kind section / provenance / commit footer); the fact
  detail page states decay arithmetic in one mono line and carries
  Confirm/Retract commit pills **gated on backend endpoints** (absent endpoint
  means absent affordance, never a dead button).
- **PRESERVED:** the `butlerScope` prop is retained on the rewritten
  `MemoryBrowser` (now the `/memory` house-ledger registers host) for a future
  butler-scoped mount. `ButlerMemoryTab` is reworked to be self-contained — it
  no longer depends on `MemoryBrowser`, drawing from its own per-butler hooks —
  so the butler-scoped tab cannot break when `MemoryBrowser` is restyled.
- **MODIFIED `dashboard-api` (read-side deltas, already shipped / scoped):**
  `GET /api/memory/stats` gains `last_consolidation_at`,
  `last_consolidation_facts_produced`, `dead_letter_episodes` (additive,
  backward-compatible); `GET /api/memory/episodes` gains a `status` enum filter
  (legacy `consolidated` bool preserved, `status` takes precedence);
  `GET /api/memory/facts` gains `source_episode_id` and `importance_min`
  filters; `GET /api/memory/facts/:id` gains a `superseded_by` reverse-lookup
  field; `POST /api/memory/facts/:id/confirm` and
  `POST /api/memory/facts/:id/retract` are added.
- **NEW table, additive-only (`dashboard-api` / data plane):**
  `public.consolidation_runs` is an audit table written once per successful
  consolidation run; it is the source for `last_consolidation_facts_produced`.
  **No changes to existing memory tables.** This keeps faith with VISION's
  no-migration intent (additive audit table only).

## Impact

- **Affected specs:**
  - `dashboard-domain-pages` (MODIFIED + REMOVED + ADDED) — the `/memory` page
    and its three detail-route requirements are rewritten to the house-ledger
    grammar; the health-badge / progress-bar / word-badge / per-tab-search /
    three-section-layout MUSTs are retired.
  - `dashboard-api` (MODIFIED + ADDED) — the read-side memory endpoint deltas
    backing every new affordance, plus the additive `public.consolidation_runs`
    audit table.
- **Affected code:**
  - `frontend/src/pages/MemoryPage.tsx` — fully replaced by the band layout.
  - `frontend/src/components/memory/` — `MemoryTierCards` (card grid), the old
    `MemoryBrowser` tab/table/badge chrome, the standalone `InspectSection`, and
    per-tab searches are retired; `MemoryBrowser` is rewritten in place into the
    `/memory` Band-3 registers host (search + register pills + focused register /
    results). `ButlerMemoryTab` is decoupled and no longer imports it.
  - `frontend/src/pages/memory/*DetailPage.tsx` — restyled from card to band.
  - `src/butlers/api/routers/memory.py` — `/stats` extension; `status`,
    `source_episode_id`, `importance_min` filters; `superseded_by` reverse
    lookup; new `/facts/:id/confirm` and `/facts/:id/retract` endpoints.
  - New Alembic migration for `public.consolidation_runs` (additive table) and
    a write-on-completion hook in `consolidation.py`.
- **Affected doctrine:** none requires amendment; the change *corrects* drift
  toward `about/heart-and-soul/design-language.md:44-62` (not a uniform feed,
  not a marketing surface) and aligns with the single-owner read-mostly
  observability surface. No butler MANIFESTO claims this page.
- **No new dependencies and no new LLM cost.** Brief §4 grades every affordance
  green: the Voice line is templated from stats, search is pure Postgres
  tsvector, re-embed uses a local model, and the consolidation pipeline (a
  pre-existing 6-hourly cron) is unchanged — the redesign adds no run-now
  affordance and no cadence change.

## Source References

- Non-Negotiable Rule (single-owner sovereignty) — `about/heart-and-soul/vision.md`
- Doctrine: dashboard is read-mostly, not a uniform information feed —
  `about/heart-and-soul/design-language.md:44-62` (esp. lines 59-62)
- Binding design intent — `pr/overview/memory-redesign/VISION.md`,
  `docs/redesigns/2026-06-12-memory-brief.md` §0
- House-ledger grammar — `pr/overview/memory-redesign/MEMORY_LANGUAGE.md`
- Canonical Dispatch design language —
  `pr/overview/memory-redesign/DESIGN_LANGUAGE.md`
