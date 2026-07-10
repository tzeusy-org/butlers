## Why

The chronicler can reconstruct a day, but the dashboard exposes a thin editorial
slice and its quantitative surface actively misleads. The clearest symptom: the
time-allocation pie chart reports "calendar = 5h" for a Saturday the owner spent
elsewhere. That is not a math bug — `aggregate/by-category` faithfully unions
episode durations. It is a **model** bug: the chronicler treats a *scheduled
calendar block* (an intention) as *lived time* (an activity), directly against
its own rule that calendar blocks are not attendance assertions
(`butler-chronicler/spec.md` §4.15).

The deeper gap: the owner cannot use the page to understand the **narrative** of
their life — how much they sleep, exercise, work, and play; **who** they spent
time with; **where** they went; and crucially **how each of those was inferred
from the data they already provide**. A remarkable spread of evidence is already
ingested (sleep, workouts, location, music, gaming, sessions, calendar, meals,
presence) — and a large vein (comms: Gmail / Telegram / WhatsApp / Discord) is
ingested but never projected, so the owner's *who* is largely invisible.

This change reframes the chronicler around three layers — **Intent**,
**Evidence**, **Activity** — so that only corroborated, inferred activity counts
as lived time; makes every inferred activity carry a clickable **evidence
chain** and a **confidence** grounded in independent corroboration; and replaces
the misleading pie with a legible day-reconstruction + longitudinal balance
surface.

## What Changes

- **Intent / Evidence / Activity layering (core reframe).** Episodes are
  classified into three layers. *Evidence* is the raw, butler-agnostic signal
  read from other schemas (the chronicler still owns none of it). *Intent* is
  what was planned (calendar, scheduled blocks). *Activity* is the inferred
  story. **Only the Activity layer is counted** in any time/balance aggregate.
  A calendar block contributes **zero** lived time unless independent evidence
  corroborates it — fixing "calendar = 5h".

- **Activity lane taxonomy** replaces the current source-shaped categories.
  Top-level lanes become life-balance lanes: **Sleep, Exercise, Work, Play,
  Social, Travel, Eat, Rest**. `music` / `gaming` / `calendar` stop being
  top-level slices — they are evidence or intent that *feeds* a lane.

- **Two-tier inference engine.** Tier 1 deterministic projectors emit *candidate*
  activities from evidence with no LLM (e.g. sustained HR + gym dwell →
  Exercise). Tier 2 is the existing once-daily `day-close` LLM, upgraded from
  "summarize" to **reconcile**: merge duplicate candidates across sources,
  resolve intent-vs-evidence conflicts, label ambiguous blocks, write narrative.
  The no-per-event-LLM invariant (§4.8) is preserved.

- **Confidence + evidence chain.** Every Activity stores `confidence`
  (`high` = 2+ independent evidence kinds, `medium`, `low` = single weak signal)
  and `evidence_refs[]`. The API exposes the chain so the UI can answer "why?".

- **Comms → Social projection.** A new deterministic adapter projects
  already-ingested message bursts (Gmail / Telegram / WhatsApp / Discord) into
  Social activities, resolving participants via `relationship.entity_facts`.

- **Memory write-back loop (doctrine-amended, narrow).** The chronicler imports
  the **memory module** and synthesizes durable insights ("sleep debt building",
  "haven't seen Alex in 3 weeks", "weekends skew to gaming") into **its own
  schema**, and *proposes* entity-enrichment facts to the `relationship` butler
  **over MCP**. It still never ingests externally and never notifies the owner.
  See the doctrine amendment in `butler-chronicler` delta.

- **New page surface (replaces the pie).** Day Ribbon timeline with a faint
  ghost Intent track (plan-vs-reality), balance rings annotated **vs the owner's
  usual**, a **who-you-were-with** panel, a **where-you-went** map trail, and
  low-confidence **correction prompts**. A zoom-out **week/month** lens shows
  balance trends, streaks, and social cadence.

- **Stale "memory butler" terminology fix.** Correct "memory butler" → "memory
  module" in the 4 live specs that carry it.

## Capabilities

### New Capabilities

- `chronicler-intent-evidence-activity`: the three-layer classification, the
  Activity lane taxonomy, the confidence model, the deterministic candidate
  projectors + day-close reconciliation contract, the comms→Social adapter, and
  the memory write-back loop.

### Modified Capabilities

- `butler-chronicler`: doctrine amendment permitting own-schema synthesized
  insights + MCP proposals to `relationship`; addition of the Activity layer and
  confidence to the storage shape; calendar-not-counted-unless-corroborated.
- `chronicler-api`: `aggregate/by-category` counts the Activity layer only and
  adopts the lane taxonomy; new balance, trends, who-you-were-with, and
  evidence-chain read surfaces.
- `dashboard-chronicles`: pie chart removed; Day Ribbon, balance rings,
  who/where panels, correction prompts, and the zoom-out trends lens added; the
  category-taxonomy requirement is re-pointed at the Activity lanes.

## Impact

- **Backend** (`src/butlers/chronicler/aggregations.py`): category mapping →
  Activity-lane mapping; counting restricted to the Activity layer.
- **Backend** (`src/butlers/chronicler/adapters/`): candidate-activity emission
  in existing deterministic adapters; new `comms.py` (Social) adapter; new
  `confidence` derivation.
- **Backend** (`roster/chronicler/api/router.py` + `api/models.py`): new
  endpoints (balance, trends, who-you-were-with, evidence-chain) + reshaped
  `by-category`.
- **Backend** (`roster/chronicler/butler.toml`): enable the memory module;
  upgrade the `day-close` prompt to reconciliation; add comms projection jobs.
- **Migration** (`roster/chronicler/migrations/`): `layer` + `confidence` +
  `evidence_refs` columns (or a derived view); chronicler-schema memory tables
  via the memory module's migrations.
- **Frontend** (`frontend/src/components/chronicles/`, `pages/ChroniclesPage.tsx`):
  remove `AggregatePieChart`; add Day Ribbon, balance rings, who/where panels,
  correction prompts, and the week/month trends lens.
- **Doctrine** (`roster/chronicler/MANIFESTO.md`, `about/heart-and-soul/v1.md`):
  narrow write-back amendment.
- **Terminology** (`openspec/specs/{relationship-facts,entity-identity,module-calendar,dashboard-permissions}/spec.md`):
  "memory butler" → "memory module".

## Sequencing

- The terminology fix and the Intent/Evidence/Activity reframe (counting fix)
  are independent and can land first — the counting fix alone retires the
  "calendar = 5h" defect.
- Confidence + evidence chain depends on the layer reframe.
- The comms→Social adapter depends on the layer reframe + confidence.
- The memory write-back loop depends on the doctrine amendment landing first.
- The new page surface depends on the new API read surfaces.

## Out of Scope

- **Proactive notifications / coaching.** The chronicler stays retrospective and
  silent; it never nudges the owner. (Broad "life-coach" doctrine expansion was
  explicitly declined.)
- **External ingestion / owning a connector.** New evidence still arrives only
  through existing connectors and read surfaces.
- **New connectors** (Google Photos, screen/app usage, Strava, reading). They
  are named in the data-wiring roadmap (`design.md`) as future evidence
  producers; each is its own change, and the substrate boundary means they
  require zero chronicler changes to start counting.
- **Real-time / live updates.** Reconciliation remains a once-daily day-close
  pass plus deterministic projection cadence.
- **Mutating evidence.** The chronicler never edits another butler's data;
  corrections remain a non-destructive overlay on its own rows.

## Archive Note (partial archive, 2026-07-10)

This change was archived after §§1–7, §9, §10 shipped, but **§8 (memory
write-back loop + doctrine amendment)** was **not** shipped — it is parked under
`bu-93y4rt`, blocked on an owner architectural decision (`bu-w6jca`). To keep the
authoritative specs true to `main`, the following §8-only delta content was
**removed before archive** and is intentionally **not** reflected in
`openspec/specs/`:

- `chronicler-intent-evidence-activity` delta: the entire `Requirement: Memory
  Write-Back Within Own Schema` (and the "synthesize durable insights" clause of
  the Purpose).
- `butler-chronicler` delta: the `Requirement: Retrospective-Only Scope`
  MODIFIED block — its only change from the authoritative requirement was the §8
  doctrine amendment (own-schema synthesis + MCP entity-enrichment proposals) and
  its two §8 scenarios; the authoritative retrospective-only requirement is left
  unchanged.

The proposal/design narrative above still describes the full intended scope
(including §8) as historical record. When §8 ships, re-introduce that spec
content via a **new** OpenSpec change.

### MODIFIED-requirement reconstruction (2026-07-10, review bu-4gg9u)

The IEA delta's `## MODIFIED Requirements` blocks for **Storage Shape**
(`butler-chronicler`) and **Chronicler Aggregations** (`chronicler-api`) were
authored as slim replacements. Because OpenSpec `MODIFIED` replaces the *entire*
requirement, archiving them would have deleted pre-existing scenarios that
describe behavior **still shipped on `main`**. Each dropped scenario was
re-verified against code and the two requirements rebuilt as the UNION of
(still-true old scenarios + the new IEA additions). Deliberate, code-verified
exceptions:

- **Storage Shape — dropped `Scenario: Derived owner column preserved during
  transition`.** The `chronicler.episodes.entity_id` column it described was
  dropped by migration `016_drop_episodes_entity_id`; owner attribution now lives
  solely in `chronicler.episode_entities` (`role='owner'`). The scenario is stale
  and is intentionally NOT restored.
- **Storage Shape — `Scenario: Episode entity link table contract`** was restored
  with a minimal update: its parenthetical justification referencing the now-dropped
  `episodes.entity_id` convention was removed; the FK-absence rule and its
  boot-safety rationale are unchanged.
- **Chronicler Aggregations — `Scenario: Unmapped active source surfaces as warning
  bucket` was rewritten** (now `Unmapped active source is dropped with a warning`).
  Under the IEA lane taxonomy there is no `other` lane, so an unmapped
  `activity`-layer episode is dropped from the lane buckets rather than summed into
  an `other` bucket. The warning log + OTel span attribute
  `chronicler.aggregate.unmapped_source` still fire (verified in
  `roster/chronicler/api/router.py`).

All other pre-existing scenarios were restored verbatim after confirming they
still hold on `main`. This reconstruction is mirrored between
`openspec/specs/` and this archived change's MODIFIED blocks so the archive is
self-consistent.
