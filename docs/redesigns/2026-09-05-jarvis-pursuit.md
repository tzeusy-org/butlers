# JARVIS pursuit — run 12 (2026-09-05)

Twelfth recurring generative audit toward a world-class JARVIS-like Butlers system. 21 agents over 7
batches of 3 (≤3 concurrent, staggered across the 8-hour window; eco/rot/deep/cross = opus/high,
surfaces and QC = sonnet/medium, synthesis inline on the fable orchestrator): 2 UI-maturity QC
cohorts over the run-11 epic (trust moves 1–6, substrate moves 7–13), 6 surface journeys
(command-control, activity-spine, health-education, life-graph, ops-qa, config-governance), 5
ecosystem lenses (knowledge-graph, connectors, inference, cross-butler, proactivity), 4 rot lenses
over specialist butlers (travel-logistics, health-wellbeing, media-taste, knowledge-capture), 2
deep-design agents (inference-economics, fleet cost-claim contract), and 2 cross-cutting sweeps
(visual-language, interaction-speed). One agent (ops-qa) failed on a usage limit and was retried to
success via Workflow resume; the retry's duplicate config-governance run was ignored. Mobile was not
audited.

Full per-agent structured output lives in `2026-09-05-jarvis-pursuit-data.json`. Access pattern:

```bash
jq '.audits[] | select(.page=="rot: travel-logistics")' docs/redesigns/2026-09-05-jarvis-pursuit-data.json
jq '.synthesis.ranked_moves[] | {rank, title, cost}' docs/redesigns/2026-09-05-jarvis-pursuit-data.json
jq '.synthesis.ranked_moves[0].behavior_matrix' docs/redesigns/2026-09-05-jarvis-pursuit-data.json
```

## North star (unchanged from run 09)

> Five-second fleet verification with earned calm: nothing fabricated, failure never impersonates
> health, staleness never wears current-data authority, and every consequential clause is a door
> on an unbroken signal-to-session-to-evidence spine. The interface is keyboard-first, follows
> Dispatch as if built by one hand, respects specialist manifesto ownership, and meets the
> repository engineering and lifecycle bars.

## Tier board and movement

Baselines are the run-11 board in `2026-09-03-jarvis-pursuit.md` (surfaces and ecosystem lenses),
the run-09 board for visual-language, and none for the four rot lenses and interaction-speed, which
are tiered for the first time. This run DID run a QC pass over the run-11 epic (two cohorts,
verdicts below), so movement on knowledge-graph and the trust surfaces may cite what actually
landed.

| Surface / lens | Run 11 | Run 12 | Movement |
|---|---|---|---|
| command-control | solid | **solid** | unchanged (roster/board/detail honest; one dead field and a read-only Decisions digest remain) |
| activity-spine | solid | **functional** | regressed on new evidence (scheduled home skills emit owner alerts with zero session evidence; 0/100 notifications carry session_id; a trace link resolves to itself) |
| health-education | weak | **functional** | improved, qualified (each tracker is honest on its own; the shipped expected-signals spine never reaches /health; condition_id joins never rendered) |
| life-graph | weak | **weak** | unchanged (attendee cards, chronicles, and commitments still dead-end at the seams) |
| ops-qa | functional | **weak** | regressed on seam evidence (backend pause/run-now has zero UI callers; QA cases carry no connector identity; audited late after a usage-limit retry) |
| config-governance | solid | **weak** | regressed on new evidence (model-tier overrides write no audit row and state no consequence; no per-butler spend ceiling) |
| cross: mobile-otg | weak | weak (carried) | not audited this run (weak carried from run 11, no movement claimed) |
| cross: visual-language | run 09 functional | **functional** | unchanged vs run 09 (token registry still shadcn's, 170 sub-AA destructive sites, five status-badge dialects) |
| cross: interaction-speed | — | **solid** | new under this name (first tiered; solid mechanics undermined by a 598-invalidation replay and no draft primitive) |
| eco: knowledge-graph | weak | **functional** | improved, qualified (QC confirms the run-11 catalog authority fix landed; credence=1.0 at birth and the missing local read ceiling remain) |
| eco: connectors | functional | **solid** | improved (honest liveness verdicts; no vision lane, chat images become the word 'Photo') |
| eco: inference | functional | **functional** | unchanged (guardrail termination discards paid output; DispatchIntent economic fields structurally dead) |
| eco: cross-butler | functional | **weak** | regressed (one delegation stuck 19 days with no terminal; 3 of 5 domain-event contracts undeliverable) |
| eco: proactivity | functional | **functional** | unchanged (deadline expiry is a log line; 100% of live expected signals producer_unknown; reach-out drafts have no send door) |
| rot: travel-logistics | — | **broken** | new (every per-trip endpoint 400s on real data; KPI strip renders zeros over that 400) |
| rot: health-wellbeing | — | **weak** | new (eight vital tools return instructions, not data; refill supply invented) |
| rot: media-taste | — | **weak** | new (Taste tab queries the wrong subject; play evidence discarded at the connector) |
| rot: knowledge-capture | — | **weak** | new (121 auto-vivified collections; containment-only search; export omits the brain) |
| qc: run11-trust | — | **solid** | QC cohort: all five audited run-11 trust moves landed as designed (one live-confirmed) |
| qc: run11-substrate | — | **weak** | QC cohort: run-11 substrate moves 7 and 8 decorative, 9 and 10 partial, 11 and 12 as designed, 13 not landed at audit time and merged since via PR #4015 |

### QC verdicts on the run-11 epic (bu-8cdl1)

**run11-trust**
- Move 1: Server-derived catalog read authority + memory_catalog_fetch: **as-designed** —
  src/butlers/modules/memory/__init__.py:1827-1866 memory_catalog_search takes no max_sensitivity arg
  at all (removed from surface) and resolves ceiling via module._catalog_read_policy()
  (__init__.py:662-675) reading runtime_config.catalog_read_sensitivity — no client-controlled input
  can raise it. memory_catalog_fetch exists at src/butlers/core_tools/_memory_catalog.py:58-124, is
  registered as a universal core tool (src/butlers/daemon.py:154), loads the same server-held
  catalog_read_sensitivity (load_catalog_read_policy), returns status:withheld for above-ceiling
  pointers, and walks provenance by dispatching to the owning butler's memory_get via Switchboard
  route (dispatch_via_switchboard_route).
- Move 2: Physical consequence gate on Home Assistant actuation: **as-designed** —
  roster/home/butler.toml:38-39 [modules.approvals] enabled = true.
  roster/home/modules/actuation.py:13-48 classifies lock.lock/unlock/open and all alarm_control_panel
  services as PROTECTED (requires_approval=True at :17-18). roster/home/modules/__init__.py:1734-1745
  checks risk.requires_approval and parks the call for approval (_park_actuation_for_approval)
  returning status:pending_approval when no approval_context is present, rather than executing.
  Receipts are written at every branch: _start_actuation_receipt before the call,
  _settle_failed_actuation on pre-send/HTTP failure (status='failed', :1955,1967),
  _settle_ambiguous_actuation on uncertain outcomes (status='unverified', :2001,2013), and
  _settle_actuation_receipt with status='succeeded'|'unverified' based on real post-condition
  verification (:1893-1899). Receipt columns actor/session_id/approval_id/status exist per migration
  roster/home/migrations/002_home_actuation_receipts.py:21-26 with a CHECK constraint restricting
  status to attempting/succeeded/failed/unverified. Frontend ButlerHomeDevicesTab.tsx:320-337 renders
  unverified/failed/legacy receipt status, so failure never renders as success.
- Move 3: Honest absence — public.expected_signals with unmeasurable semantics: **as-designed** —
  src/butlers/core/expected_signals.py:16-19 defines PRESENT/ABSENT/UNMEASURABLE tri-state.
  evaluate_expected_signal (:81-143) computes measurability via _connector_measurability (:42-70)
  which JOINs public.v_qa_connector_state on connector_type+endpoint_identity and checks
  state=='healthy' AND liveness not stale (is_liveness_stale, 300s TTL) before ever comparing elapsed
  cadence; if the connector is dead/unregistered/stale it returns UNMEASURABLE with a reason,
  short-circuiting before the absence check. roster/health/jobs/health_jobs.py:399-413 calls
  upsert_expected_signal, checks state is UNMEASURABLE and `continue`s (suppresses the owner nudge)
  rather than firing 'you haven't logged weight' on a dead scale integration. core_210/211 migrations
  create the table with measurability column and RLS. Same pattern reused across
  src/butlers/jobs/home.py (energy digest, device health, environment report all check
  ha_source_unmeasurable) and briefing.py.
- Move 5: Currency-honest money spine: **as-designed** — Live GET
  http://127.0.0.1:42200/api/finance/spending-summary?period=monthly returned
  {"currency":null,"total_spend":"441.99","by_currency":[{"currency":"SGD",...},{"currency":"USD",...}],"legacy_aggregate_degraded":true,"degraded_reason":"multiple_currencies_unconverted"}
  — a real two-currency household is honestly denominated, top-level currency is null (not
  fabricated), degraded flag is true. Source: roster/finance/api/router.py:621-679 groups by
  t.currency, sets currency=None when currency_count>1, computes by_currency array and
  legacy_aggregate_degraded. roster/finance/tools/overview.py:405-472 (net worth) and :540-681
  (cash-flow/savings_rate) both group by currency and exclude category IN ('transfer','uncategorized')
  from income/expense aggregation (:563-565, :631-633). Per-account feed staleness is selected and
  surfaced: router.py:508-533 computes feed_degraded/feed_degraded_reason from last_synced_at vs a 24h
  freshness cutoff, and frontend ButlerFinanceFinancesTab.tsx:630-638 renders never_synced/stale
  distinctly with a relative Time display.
- Move 6: Surface honesty batch (a-f): **as-designed** — (a)
  src/butlers/api/routers/model_settings.py:723 (create) and :869 (delete) both call audit.append;
  SettingsModelsPage.tsx:356-405 shows a cascade-delete confirmation dialog with a live override_count
  fetched from an impact-check endpoint (blast radius). (b) src/butlers/api/routers/audit.py:94
  privileged-kind filter includes "OR action LIKE 'spend.%'". (c) StatusBoardCell.tsx:40,317 imports
  and uses formatCostUsd for the SPEND KPI cell (not raw toFixed). (d) ReviewTimeline.tsx:101,111
  reads isError per review-source query and computes reviewsError from it. (e) TimelineTab.tsx:105,857
  renders the canonical AttentionStrip component from ./connectors/AttentionStrip rather than a fake
  link. (f) roster/relationship/api/router.py:6682-6745 sets degraded=true with a fixed content-blind
  reason (chronicler_activity_unavailable, :6408) whenever the chronicler call fails; frontend
  ActivitySparkline.tsx:38-54 and EntityDetailPage.tsx:1982-1986 both branch on data.degraded to
  render a degraded state instead of treating chronicler outage as inert/inactive entity.

**run11-substrate**
- Move 7 — Fleet Case File: **decorative** — core_217 migration docstring self-declares schema-only;
  zero 'fleet_case' references in broker.py, attention_ledger.py, core_tools, or frontend
- Move 8 — Public entity graph + dossier: **decorative** — core_215 migration docstring
  self-declares substrate-only; zero 'entity_graph_edges'/'entity_graph_walk' references outside
  migration/tests; no dossier route in any api router
- Move 9 — Friction ledger + outcome columns: **partial** — src/butlers/core/sessions.py:744-825
  computes succeeded/failed/by_error_marker live from sessions.success/error columns (real,
  deterministic, S1 landed); no sessions_friction table exists anywhere (grep zero hits) so typed
  friction episodes (S2) are absent; src/butlers/api/routers/butlers.py:576-590 only consumes by_model
  from the same call, so even the landed S1 fields have no dashboard/console consumer (S3 absent)
- Move 10 — Forward obligation ledger + cancellation door: **partial** — finance_013 migration adds
  cancellation_url/notice_period_days/cancel_by (nullable, no derivation);
  roster/finance/tools/subscriptions.py and roster/finance/api/router.py fully CRUD-wire the three
  fields; roster/finance/tools/alerts.py has zero references to any of them
- Move 11 — Owner-scoped presence: **as-designed** — src/butlers/jobs/context_producers.py:166-230
  correctly scopes at_home to owner-linked entity ids loaded from state key
  home:presence:owner_entities and judges freshness against HA's own last_updated clock (not
  captured_at), returning None when unconfigured rather than falling back to any-entity
- Move 12 — ha_source_health guard: **as-designed** — roster/home/modules/__init__.py:2456-2492
  upserts ha_source_health on every successful/failed HA contact;
  src/butlers/jobs/home.py:126-165,267,1801-1810,2101-2110,2337-2346 gates energy
  digest/device-health/environment-report jobs on _require_ha_source_healthy;
  roster/home/api/router.py:109-119 reuses the same guard;
  frontend/src/components/butler-detail/ButlerHomeDevicesTab.tsx:89,105-112 renders a
  SourceDegradedNote when haSourceAvailable is false; live GET /api/home/devices and
  /api/home/snapshot-status both returned 200 with populated health_status/entity data against the
  running dev stack
- Move 13 — Viewport/modality contract + client-link honesty + phone entry routes: **not-landed** —
  git show 97af6b4e3 --stat: PR #4010 added only files under
  openspec/changes/amend-dispatch-viewport-modality-contract/ (proposal.md, tasks.md, and a delta
  spec.md) — zero lines changed in the canonical openspec/specs/dashboard-design-language/spec.md,
  which still has zero hits for 'Viewport and Modality'/'coarse-pointer'/'hover-only';
  frontend/src/components/layout/LiveIndicator.tsx:9-20,66 is unchanged from its pre-move form (still
  labels the socket state 'Fleet event stream'); no use-client-link.ts exists on main; gh pr view 4015
  confirms that hook + the LiveIndicator/RootLayout fix are still an OPEN PR; no phone Playwright
  project or entry-route registry found anywhere in frontend/src or playwright.config.ts

## Systemic themes

### 1. Substrate shipped, consumer never

Run 11 filed substrate-first slices; the consumers were deferred and then never scheduled. The
pattern is older than run 11: columns, tables, and enum values that are written (or not even that)
and read by nothing.

- alembic/versions/core/core_217_fleet_case_file.py:7-10 — fleet case file is schema only; zero
  runtime writer or reader
- alembic/versions/core/core_215_entity_graph_edges.py:6-10 — entity_graph_edges has no writer,
  traversal tool, or dossier route
- src/butlers/core/scheduler.py:2286-2300 — scheduled_tasks.last_result is write-only
- live GET /api/finance/expected-signals — 1 signal, producer 'unknown', 100% unmeasurable via
  producer_unknown
- roster/education: 5 mind maps, 0 nodes, 0 review cards ever scheduled; core_002 'place'
  entity_type with zero rows

Affected: qc: run11-substrate, eco: inference, eco: proactivity, rot: knowledge-capture, eco:
connectors, surface: health-education

### 2. Computed, then discarded at the seam

Real signal reaches the code and is thrown away one line before persistence or one line before
render: the field is read, unpacked into `_`, or overwritten with a label.

- src/butlers/api/routers/spend.py:279,834 — four token buckets computed, two unpacked into `_, _`;
  owner sees 28% of tokens bought
- src/butlers/connectors/spotify.py:302-387 — track_id used for dedup then dropped; progress_ms
  never read
- src/butlers/jobs/context_producers.py:329-355 — trip end_date selected then discarded; the bus can
  never say when travel ends
- src/butlers/jobs/flight_status.py:240-256 — estimated departure written to metadata,
  legs.departure_at never updated
- src/butlers/tools/attachments.py:78-92 — image bytes base64'd into a JSON tool result no model can
  see
- roster/switchboard/tools/insight/broker.py:1110-1125 — producer-set expires_at read and dropped

Affected: deep: inference-economics, rot: media-taste, eco: cross-butler, rot: travel-logistics,
eco: connectors, eco: proactivity

### 3. Tools that instruct, or fabricate a denominator

Where the deterministic layer should answer, it either hands the model an instruction to go compute
the answer itself, or invents the missing number so that the arithmetic can proceed.

- src/butlers/modules/google_health.py:262-520 — all eight vital tools return {'instruction': 'Call
  memory_search ...'}
- roster/health/jobs/health_jobs.py:522-537 — 'Assume a standard refill is 30 days'; three adherence
  denominators disagree (reports.py:180-188, router.py:806-818)
- roster/relationship/tools/loans.py:55,96,289-290 — USD fabricated on every currency-less loan
- src/butlers/jobs/briefing.py:1623-1689 — briefing counts denominate LIMIT clauses;
  use-memory.ts:465-476 KPIs are page counts
- src/butlers/modules/memory/storage.py:1343,1366-1393 — every fact born with confidence 1.0;
  model_routing.py:687-690 cost term is 'not a prediction of any real call's cost'

Affected: rot: health-wellbeing, deep: cost-claim-contract, rot: media-taste, eco: knowledge-graph,
deep: inference-economics

### 4. Failure impersonating health at the read side

The run-11 trust fixes held (QC: all five as designed), but the same sin reappears wherever the
render path is newer than the fix: an error, a stall, or a frozen verdict renders as calm.

- frontend/src/components/butler-detail/ButlerTravelTripsTab.tsx:646-669,168-196 — KPI strip shows
  0/0/0 over a live 400
- live /api/ingestion/connectors/summaries — ActivityWatch liveness 'online' with two months of zero
  ingestion
- src/butlers/core/scheduler.py:1205-1215 — deadline expiry disables the task with only a log line
- live /api/delegation/ledger — a 'routed' question with no terminal state since 2026-08-17
- frontend/src/components/layout/LiveIndicator.tsx:66 — announces a dropped device link as live
  (run-11 move 13 not landed at audit time; PR #4015 merged after the audit as cdee9fdc9 on
  2026-09-05)
- src/butlers/core/expected_signals.py:168-193 + roster/finance/api/router.py:186-228 —
  last-write-wins verdict with no staleness guard on evaluated_at

Affected: rot: travel-logistics, eco: connectors, eco: proactivity, eco: cross-butler, qc:
run11-substrate, cross: interaction-speed

### 5. Governance asymmetry on one surface

Two writes of the same class on the same page or in the same subsystem are governed differently: one
audited, reasoned, and ceilinged; its sibling silent.

- src/butlers/api/routers/model_settings.py:1538,1607 — model-tier override PUT/DELETE write no
  audit row, while catalog create/delete at :723,:869 do
- src/butlers/modules/memory/search.py:659-661,817-860 — local recall has no sensitivity ceiling
  while the catalog enforces a server-held one
- src/butlers/modules/memory/storage.py:61-100 — health facts reach the fleet catalog at NULL
  sensitivity treated as 'normal'
- alembic/versions/core/core_217_fleet_case_file.py:152-176 — fleet_case_evidence.contributor
  self-asserted, vs core_210 producer attested by role
- roster/travel/tools/_helpers.py:14-37 vs roster/travel/api/router.py:103 — two row converters, one
  defensive, one not

Affected: surface: config-governance, eco: knowledge-graph, rot: health-wellbeing, eco:
cross-butler, rot: travel-logistics

### 6. The owner's own knowledge has no verb

The fleet ingests connectors fluently and the owner poorly: there is no capture, correction, rating,
declared-situation, or decision-record verb, and General auto-vivifies a collection from any string.

- roster/general/tools/items.py:35-44 — item_create auto-vivifies collections; live 121 collections
  with hyphen/underscore twins
- roster/general/tools/items.py:106-155 — search is JSONB containment only, no LIMIT, no cursor,
  against a manifesto promising keyword search
- roster/chronicler/modules/__init__.py:349-395 — corrections require an episode a machine adapter
  produced
- src/butlers/api/routers/data_ops.py:113-127 — 'all data' export queries a `memory` schema that
  does not exist and omits the brain
- roster/relationship/api/router.py:3451-3480 — reach-out drafts are inert facts with no send door

Affected: rot: knowledge-capture, eco: proactivity, rot: media-taste, surface: life-graph

## Ranked moves

Ranked by owner value per unit cost, doctrine-weighted: trust and honesty defects outrank features,
features outrank polish. Moves 1–6 and 12 are trust defects; 7–11 and 13–14 are features; 15 is UX
repair. Every move carries a full Dispatch Readiness Packet in the data JSON (outcome, non-goals,
governing intent, surface map, behavior matrix, doc impact, verification, slice plan); the prose
below is the why and the shape.

### 1. Travel data-path repair: un-double-encode legs metadata, make the flight-status poll selectable, degrade the KPI strip honestly (S, trust-defect)

North star: failure never impersonates health. roster/travel/MANIFESTO.md 'Knowing what is changing
and when'. Today every per-trip Travel endpoint returns 400 on real data because legs.metadata is
JSON-encoded twice; the flight-status poll's selection predicate is false for every row so no flight
has ever been polled; and the Trips tab renders 0 active / 0 planned / 0 open actions over that 400.
One small repair restores the whole Travel surface and stops a live lie.

**Outcome.** GET /api/travel/trips/{id} and /legs return 200 for the live DD94XR journey; the
flight-status job selects both of its segments on the next tick; with the upstream errored, the KPI
strip renders 'unavailable' plus a banner naming the endpoint and never a numeral; one row converter
serves both the MCP tools and the API.

**Slices.** S1 (ship alone): drop the json.dumps call sites, backfill string-typed metadata, replace
router.py's five converters with _row_to_dict, add the leg-with-metadata API test. S2: flight_status
predicate now selects; add the both-segments test and stop writing last_error=NULL on a zero-leg
pass (report 'no selectable legs'). S3: KpiStrip/trips-roster error prop + degraded banner;
server-side per-row degraded envelope on /upcoming and /trips/{id}.

Source: rot: travel-logistics (M0 slice 1, M6; findings 1, 2, 3, 11)

### 2. Health truth: a vitals spine with de-stubbed tools, a live measurement vocabulary, and an honest medication supply ledger (M, trust-defect)

roster/health/MANIFESTO.md (Measurements, Medications, 'no judgment, only honesty'). All eight
vital-sign tools return English instructions instead of numbers, so every health answer is model
arithmetic over raw facts; health_summary is blind to any measurement type outside a frozen five;
and the refill insight invents a 30-day supply nobody recorded, so a perfectly adherent owner gets a
critical alarm that self-expires. The deterministic layer must own the numbers.

**Outcome.** health_sleep_history(days=7) returns daemon-computed numbers with no 'instruction' key;
health_summary lists every measurement type present in the fact store and names absent ones with a
reason; a medication with no recorded dispense shows 'supply unknown — record a dispense' and
produces no refill candidate; one expected-dose denominator serves trend_report, the adherence
endpoint and the insight.

**Slices.** S1 migration + rollup job + tests, no consumer changes. S2 de-stub the eight tools
against the rollup tables; contract test bans 'instruction' returns. S3 reports.py reads the live
type registry; spec amendment. S4 failing test pinning the refill fabrication, then dispense
predicate + tools + rewritten refill computation with the unmeasurable branch and fixed expires_at.
S5 unify the three adherence denominators. S6 GET /api/health/vitals/daily and prompt updates naming
the new tools.

Source: rot: health-wellbeing (M0, M4; findings 1, 2, 3, 4)

### 3. Memory trust contract: one read ceiling on every path, a health privacy class that is actually set, calibrated credence with owner assertions dominant, and a knowledge-classed profile pack (L, trust-defect)

vision.md non-negotiable 1 (full sovereignty over one's own data) and roster/general/MANIFESTO.md
'Once you tell us something, it's safe.' Local recall and memory_context have no sensitivity ceiling
while the catalog enforces a server-held one; every health fact reaches the fleet-shared catalog
because NULL sensitivity reads as 'normal' (live: an aortic-valve fact is catalog-searchable); every
fact is born with confidence 1.0 so the credence numeral rendered fleet-wide is decoration; and
Profile Facts, 30% of every session's memory context, degenerates into a receipt feed. Run 11 fixed
the catalog; this closes the other three doors of the same room.

**Outcome.** A confidential owner fact is absent from memory_context under a 'normal' ceiling and
the block reports withheld: 1; condition_add/symptom_log/medication_add produce no
public.memory_catalog row; an llm_inferred write against an owner_asserted incumbent is rejected
with a typed error naming the incumbent; Profile Facts contains only identity/preference-class facts
ordered by credence; the five section fractions sum to ≤ 1.0.

**Slices.** S1 generalize the policy loader and thread it through search/recall behind the existing
catalog default; apply to memory_context's three fetches with the withheld footer. S2 per-predicate
sensitivity default map threaded through health writes (new writes stop leaking immediately) +
reclassification purge of existing catalog rows including relationship's health_concern. S3 additive
credence/evidence_class migration with unknown_provenance read semantics; required arguments on
store_fact and the MCP tool; class-floor default in consolidation_parser. S4 precedence rule inside
the store transaction; assertion + confidence on health write signatures with least-authoritative
defaults; trend/recovery-state weighting. S5 scoring + context line + FactsRegister render;
knowledge_class column + seed; Profile Facts join, confidence floor, fraction correction and the
budget-sum assertion. S6 explicit-filter authorization error and the search.py:580-608
silently-ignored-keys repair; effective ceiling on the memory console; spec and doc amendments.

Source: eco: knowledge-graph (M3, M4, M5; findings 1, 2, 3, 5, 6); rot: health-wellbeing (M5;
findings 5, 6)

### 4. Cache-honest spend: four token buckets end to end, prompt-composition columns on the ledger, and a schedule ranking that only forecasts live schedules (M, trust-defect)

North star: staleness never wears current-data authority. The Spend page shows 28% of the tokens the
owner bought because cache buckets are computed and then unpacked into `_, _`;
/api/spend/by-schedule ranks a deleted schedule #1 at $496/month because schedule_costs never
filters on enabled; 28 of 49 priced models bill cache reads at full input price by silent fallback;
and no query on main can answer 'what did each prompt layer cost'. Every field here is a measured
integer — no prompt is read, nothing is classified.

**Outcome.** /api/spend/summary?period=7d returns total_cached_input_tokens ≈ 1.0e8 alongside
uncached and output; cache_hit_rate is None (never 0.0) when the denominator is zero; a model
without a cached rate shows a no_cache_price badge instead of a silent number; by-schedule's head
row is a live schedule (chronicler_day_close on the dev stack) and a disabled schedule reports
total_runs with projected_monthly_usd absent; each ledger row carries the five prompt-composition
columns and resume_outcome.

**Slices.** (1) pricing.toml cache rates + billing_class + the parity guard (no migration, ships
alone). (2) st.enabled through schedule_costs and the retired marker. (3) four buckets through the
API and SpendPage with the divergence guard. (4) core migration + ComposedPrompt digests +
record_token_usage kwargs + resume_outcome.

Source: deep: inference-economics (M0; findings 1, 2, 5, 6)

### 5. Nothing vanishes silently: session evidence for scheduled skills, partial-output preservation on guardrail termination, a terminal state and sweep for stalled delegations, and an owner signal on deadline expiry (M, trust-defect)

North star: an unbroken signal-to-session-to-evidence spine. Four places on main lose work or
evidence without a trace: scheduled home skills emit owner alerts with zero session rows (0/100 live
notifications carry session_id, and a trace link resolves to itself); a guardrail trip discards the
model's completed output and its episode after the owner paid for it; a delegated question has been
'routed' for 19 days with no terminal and no sweep; and a deadline expiring disables the task with
only a log line. architecture.md § Core Loop step 6 promises the outcome is recorded whenever tokens
are.

**Outcome.** Every notify() from a scheduled skill carries a session_id that resolves on
/api/sessions and /api/timeline?trace=; a session-less notification carries a producer stamp instead
of a silent omission; a guardrail-terminated session's partial output reaches session_complete and
the episode store marked partial; the stuck delegation row becomes status='unanswered' with attempt
evidence on the Overview attention list; a deadline expiry produces an owner-visible attention
event; a domain-event contract with no permitted subscribers is refused at startup.

**Slices.** S1 session evidence for scheduled skills + producer stamp (activity-spine M0/M1). S2
guardrail preservation only: persist partial output + tool digest, session_complete with output,
partial episode (inference M5 slice 1). S3 public.session_handoffs + session-detail rendering of the
partial and reason. S4 delegation 'unanswered' terminal + answer_due_at + sweep with sweep-only
write authority. S5 durable answering wake with bounded retry replacing the two-minute one-shot. S6
API stuck filter + Overview attention surfacing + spec amendment. S7 deadline-expiry attention
event. S8 startup refusal of write-only domain-event contracts.

Source: surface: activity-spine (M0, M1; findings 1, 2); eco: inference (M5 slices 1-2; finding 1);
eco: cross-butler (M5; findings 2, 3, 4); eco: proactivity (finding: deadline expiry log line)

### 6. Surface honesty batch: ten small repairs where a rendered clause is not yet a door or not yet true (S, trust-defect)

Each item is a one-slice fix at a real seam that today either fabricates, discards context on click,
or leaves a consequential edit ungated. Batched so none is lost to ranking noise; each is
independently shippable and independently testable.

**Outcome.** (a) a model-tier override requires a reason, previews old→new $/M tokens, and writes
one audit row per PUT/DELETE. (b) a symptom-trend insight opens /health/symptoms?name=X
pre-filtered. (c) an absent vital with an unmeasurable expected signal renders 'unreachable' on the
/health KPI strip and chart, distinct from '—'. (d) schedule_count is populated from the same query
the board uses, or deleted. (e) a blank-title research note renders 'Untitled note' with an
accessible name. (f) the Viewport & Modality Contract lives in
openspec/specs/dashboard-design-language/spec.md. (g) export scope names only schemas that exist and
the 'all data' label is truthful about what it excludes. (h) a 200-event snapshot replay costs ≤ 5
invalidations. (i) pause / run-now buttons work on the connector detail page with an audit receipt.
(j) a routing-rule row deep-links to /ingestion/filters?rule=<id> and highlights it.

**Slices.** One PR per letter, any order; (f) and (h) first since they close run-11 debt, (i) and
(a) next as the two consequential-edit doors.

Source: surface: config-governance (M1); surface: health-education (M1, M2, M4); surface:
command-control (M2); qc: run11-substrate (M3; finding: LiveIndicator, since merged in PR #4015);
rot: knowledge-capture (finding: export scope); cross: interaction-speed (M0); surface: ops-qa (M0,
M2)

### 7. Make the butlers see: a vision lane from chat transports through a content-block attachment tool (L, feature)

roster/health, roster/home/MANIFESTO.md:30, Lifestyle, Relationship and General all promise to act
on what the owner shows them, and no butler can see a pixel: get_attachment advertises vision input
but returns base64 inside a JSON tool result, every chat connector replaces an image with the word
'Photo', and the ATTACHMENTS prompt promises a lazy-fetch verb that does not exist. This is the
single largest new perception available to the fleet.

**Outcome.** A Telegram photo with caption 'is this mold?' reaches Health as an image content block:
normalized_text is the caption (empty when captionless, never 'Photo'), one blob per media id,
dispatch requires a VISION-capable model, and the reply cites the blob ref. A >cap blob returns a
typed refusal, never a base64 payload.

**Slices.** S1 (trust, tiny): content-block return + typed refusal above cap, ModelFeature.VISION,
truthful ATTACHMENTS block — Gmail images become viewable with no connector work. S2 Telegram bot
photos/documents → blobs. S3 Discord + WhatsApp sidecar media. S4 attachment_materialize for lazy
blobs via Switchboard; retire GmailConnector.fetch_attachment's dead path. S5 spec amendments + the
no-synthesized-media-text contract test.

Source: eco: connectors (M0; findings 1, 2, 3)

### 8. Journey identity and connection integrity: a PNR-keyed booking record with a traveller party, and a journey graph that knows whether the layover still holds (L, feature)

roster/travel/MANIFESTO.md:21-23 ('every confirmation number, PNR, and reservation detail
organized'; knowing what is changing and when). Today one round-trip PNR became two one-day trips,
the return leg is deduped away when both segments share a confirmation number, the same flight is
stored once per passenger, and no code path can say whether a connection still holds after a delay.
Depends on move 1's data-path repair.

**Outcome.** Ingesting the real DD94XR fixture yields exactly 1 trip 2026-10-16..2026-10-25, 2 legs,
4 leg_passengers; GET /api/travel/trips/{id} returns a connections[] block with verdict
holds/tight/broken/unknown and a stated reason; a 40-minute inbound delay flips a holding connection
to broken, raises one alert and one approval door, and recovery withdraws the door.

**Slices.** S1 booking_records + segment identity + ON CONFLICT dedup + _find_matching_trip on
record_locator, with the fragmentation backfill behind a dry-run report. S2
travellers/leg_passengers + party-aware briefing and alert counts. S3 connections derivation +
airport_minimum_connect seeded with the airports present in travel.legs ('unknown' elsewhere) +
alerts on trip_summary and /upcoming. S4 live recompute wired into flight_status including the
departure_at/updated_at repair. S5 approval door + insight category. S6 dashboard party chip and
connection rows; spec amendment; domain-event schema_version 2.

Source: rot: travel-logistics (M0 slices 2-4, M1; findings 4, 5, 10)

### 9. A second brain with a verb: capture(), a collection vocabulary with ownership refusal, and retrieval that works (L, feature)

roster/general/MANIFESTO.md:9-21 promises a second brain with keyword search; today item_create
auto-vivifies a collection from any string (live: 121 collections including banking /
banking-transactions / bank-transaction-alerts / transactions / financial_transactions), search is
JSONB containment with no LIMIT, General hoards shadow copies of four specialists' data, and a
thought captured in a session that dies is lost with no receipt. The owner's own knowledge has no
verb.

**Outcome.** capture() returns a capture_id synchronously and a `held` row survives a dead routing
session; a routed capture's receipt cites a target_row_id that SELECTs in its named table;
item_create('email_records') resolves to the canonical 'email-records' and an unknown name raises
naming the nearest candidates and the declare verb; a bank alert is refused naming Finance and the
tool to use; capture_search('structured note') finds an item by keyword with a bounded cursor;
captures are visible to other butlers through public.memory_catalog.

**Slices.** S1 public.captures + capture() writing held rows + receipt. S2 vocabulary + alias tables
seeded from the live 121 with a generated mapping, read-only, plus /api/general/vocabulary. S3
item_create resolves through aliases; unknown-name refusal + collection_declare. S4 typed routing to
note/fact/preference with the target_row_id contract + /api/captures. S5 content_text +
search_vector + GIN + capture_search; bounded pagination and retirement of unbounded item_search. S6
catalog membership. S7 ownership refusal map wired into capture(); Switchboard `capture`
classification + Telegram/dashboard entry points. S8 merge proposals + owner approval + merged_into
repointing. S9 held-capture lane on Dispatch; extraction from attachments with provenance.

Source: rot: knowledge-capture (M0, M1, M2; findings 1, 2, 3, 9)

### 10. The taste ledger: works, signals and verdicts as tables, full-fidelity play evidence from the connector, and a Taste tab that queries the owner (L, feature)

roster/lifestyle/MANIFESTO.md ('the keeper of your taste'; 'notices when your listening habits
change'). Lifestyle stores taste as 61 prose facts under three divergent predicate allowlists; the
Taste tab queries subject='user' and renders 'no taste recorded' over those 61 rows; KPIs are page
counts over a 200-row window; and the Spotify connector uses track_id for dedup then drops it and
never reads progress_ms, so skip/finish can never be known. The connector already sees everything
the ledger needs.

**Outcome.** A Spotify session summary produces one works row per track uri and one taste_signals
row per play with no LLM; a 5-second play of track A followed by track B closes a play row with
completion_ratio ≈ 0.02 and skipped=true; GET /api/lifestyle/taste/summary returns meta.total from
COUNT(*); the Taste tab renders ledger-backed panels with real totals; the 61 legacy prose facts
survive as verdict_text.

**Slices.** S1 migration 002 + resolver + backfill from the two connector evidence tables, zero UI.
S2 TrackObservation + progress_ms + connectors.spotify_track_plays and grants, connector writes it.
S3 completion/skip derivation + play_only precision. S4 the five MCP tools; registry seeding; fuzzy
suggestion becomes a rejection for lifestyle scope. S5 read surface with honest meta.total and
degraded envelope. S6 replace the three broken Taste tab selectors with ledger-backed panels (fixes
subject, page-count KPIs, decorative digest). S7 projector consumes plays (complete/skip/replay
signals). S8 migrate the 61 legacy prose facts into verdicts with verdict_text.

Source: rot: media-taste (M0, M1; findings 1, 2, 3, 4, 6, 7)

### 11. Prepared actions: proactive drafts parked on the approval spine and bound to the insight that motivated them (L, feature)

roster/messenger/MANIFESTO.md outbound delivery ownership; roster/relationship 'The one who follows
through'. Insight candidates are structurally message-only (no action/target/door column) and
relationship reach-out drafts are inert facts whose docstring says the endpoint 'cannot trigger' a
send. The fleet can notice and can say, but it cannot offer. A prepared action is a draft with a
door, never auto-executed.

**Outcome.** After one relationship insight-scan tick, GET /api/approvals/actions?origin=prepared
returns a parked reach-out with expires_at, GET /api/switchboard/insights shows the candidate
referencing it, the digest renders a door, approving replays the stored tool args and writes
execution_result, and no repo reference to reach_out_draft remains.

**Slices.** S1 pending_actions.origin + park_prepared_action + orphan sweep (no producer). S2
insight_candidates columns + broker validation + digest door, one producer: relationship reach-out.
S3 finance subscription-cancellation prepared action + email reply preparation. S4 retire
reach_out_draft endpoint/tool/frontend; promote the spec requirement to mandatory.

Source: eco: proactivity (M0; findings 8, 10); qc: run11-substrate (move 10 partial)

### 12. Fleet cost-claim contract, slices 1–3: a typed money claim fenced by RLS, every loan becomes a claim and the USD fabrication dies, and a Finance reconciliation sweep with three honest kinds of 'no' (M, trust-defect)

roster/relationship/MANIFESTO.md:32 ('Track loans ... so no awkwardness lingers') and
roster/finance/AGENTS.md:34-36 ('NEVER write settlement state ... in JSONB'). Relationship
fabricates USD on every currency-less loan, settles a loan outside a transaction (the old fact can
be superseded and the new one lost), and keeps money state in memory-module JSONB with no
constraints. Finance cannot see a sibling's claim at all. The design is a six-slice contract; the
first three make the assertion honest and give Finance a verdict that never impersonates a
reconciliation it could not run.

**Outcome.** Creating a loan without a currency raises; a legacy currency-less loan reads null,
never USD; a loan and its claim show on both GET /api/relationship/entities/{id}/loans and GET
/api/finance/cost-claims on a database where finance holds no grant on the relationship schema;
every claim on the current dev stack resolves unverifiable/no_account, not unreconciled; a forged
asserted_by is refused by RLS.

**Slices.** S1 schema + RLS + core tool group + tests (nothing owner-visible). S2 relationship loans
→ claims, required currency, transactional settle, backfill. S3 Finance sweep + bindings + verdicts
+ GET /api/finance/cost-claims (still no owner surface).

Source: deep: cost-claim-contract (M0, M1, M2; findings 1, 2, 3, 5)

### 13. Blind-spot preamble and task continuity ledger: every session is told what it cannot see, and every recurring task remembers what it concluded last time (M, feature)

roster/health/MANIFESTO.md honesty (a health butler reporting stale vitals as current is the
sharpest violation) and every domain manifesto's honesty clause. The expected-signals primitive
shipped (QC: as designed) but nothing tells a session at spawn which of its declared signals are
absent or unmeasurable, so a session confidently reasons over data that is not there; and a
recurring task starts every run from nothing, with the chronicler's day-close continuity a bespoke
hook rather than a general layer.

**Outcome.** With every declared signal PRESENT the composed prompt is byte-identical to today; with
a stale connector the health session's prompt carries a typed block naming the signal, its producer,
last_observed_at and the evaluator's clock; if the blind-spot query itself errors the preamble says
'source health could not be evaluated' rather than omitting the layer; run N+1 of an opted-in
recurring task carries run N's carry_forward block with its age, or an honest 'the last run recorded
no carry-forward'.

**Slices.** S1 generalize expected_signals into the per-butler evaluator (no injection). S2 inject
for health behind the flag and diff outputs. S3 roll to all domain butlers + the fail-closed path;
expose on GET /api/butlers/{name}. S4 migration + public.task_continuity +
continuation_of_session_id (write-only). S5 carry_forward tool; observe adoption. S6 injection for a
briefing and a finance check behind the opt-in with age stamp and gap wording. S7 migrate the
chronicler day-close hook onto the layer after the equivalence test; extend opt-in across the
roster.

Source: eco: inference (M1, M2)

### 14. Life-graph doors: attendee names link to the person, the person page shows shared chronicles, and commitments are scoped to an entity (M, feature)

vision.md 'the amount of mental labor the system reliably absorbs': the owner journey 'who is this,
when did we last meet, what do I owe them' breaks at three seams. Meeting-prep attendee cards carry
entity_id but render the name as plain text; EntityDetailPage never shows chronicler episodes though
GET /api/chronicler/episodes?participant_entity_id exists with real data; and the only commitment UI
fires inside MeetingPrepRail. Run 11 named the theme 'primitives built once, generalized nowhere';
this is the generalization.

**Outcome.** Clicking an attendee name opens /entities/{entity_id} preserving the current query; the
entity page shows recent shared episodes with dates and links, an honest 'no shared episodes yet',
and a distinct degraded state on error; GET /api/relationship/entities/{id}/commitments returns open
commitments in both directions with deadline and escalation, rendered on the entity page independent
of any calendar event.

**Slices.** S1 attendee Link (ship alone). S2 chronicles panel on EntityDetailPage. S3 entity-scoped
commitments endpoint + panel.

Source: surface: life-graph (M0, M1, M2; findings 1, 2, 3)

### 15. Interaction spine: a draft store so unsent words survive, a bus-coverage token the test suite enforces, and one navigation-intent primitive (M, ux-repair)

vision.md sovereignty read in reverse: silently discarding owner input is the destructive twin of
fabricating it. The chat composer and 16 textarea dialogs destroy typed text on close or navigation;
four hooks adopt the 'bus-covered' 5-minute cadence with no bus event that invalidates them; and
intent prefetch is split so table rows warm data but never the chunk. Roughly 60% of this move is
non-defect polish, ranked last on purpose.

**Outcome.** Typing into the chat widget, closing, and reopening restores the text with a quiet
'draft restored' affordance and one-click discard; a secrets-page field is never persisted; a hook
claiming bus coverage without a manifest row fails to typecheck; a pointer pause on a session row
warms both the chunk and the query, and pointerenter+leave inside the debounce fires neither.

**Slices.** S1 draft-store + use-draft + dialog dirty guard with tests. S2 chat composer keyed per
conversation. S3 the six health forms + ScheduleForm + create-rule-dialog. S4 remaining composers;
ESLint rule to error; denylist test. S5 coverage token + the closed-direction sweep. S6
use-nav-intent.ts + both old hooks re-implemented on it; migrate RowLink/DisclosureRow/SessionTable,
then Sidebar/EntityFinder; lint rule + delete old hooks.

Source: cross: interaction-speed (M6, M1, M3; findings 2, 5, 8)

## Dropped (deduped or cut, so nothing silently vanishes)

- Fleet case consumers (broker wiring, MCP tools, dashboard) — known: bu-8cdl1.7 (QC: move 7
  decorative)
- Entity graph first consumer (writers, backfill, traversal tools) — known: bu-8cdl1.8 (QC: move 8
  decorative)
- Subscription cancellation warn-by derivation — known: bu-8cdl1.10 (QC: move 10 partial); finance
  prepared-action rides it in move 11
- LiveIndicator client-link honesty — QC found move 13 not landed at audit time; PR #4015 merged
  after the audit (cdee9fdc9, 2026-09-05), so nothing to file
- DecisionsPage act verb — known: bu-ckkpz
- Situation archetypes / owner-declared situations / situational horizon / case narrator /
  answerable-question registry (eco: cross-butler, proactivity) — depend on fleet case consumers
  landing first
- Session escalation door and bounded continuation for guardrail terminations (inference M5 slices
  3-4) — deferred until max_tool_calls is reachable
- Consequence-gated verification, dispatch SLOs, reachable max_tool_calls (eco: inference) —
  Consequence changes one boolean today; design after move 13 lands
- Volatility-ordered prefix, context yield, two-phase dispatch, shift-session coalescing,
  local-inference adapter (deep: inference-economics) — dropped by the agent itself with measured
  evidence; keep only the measurement slice (move 4)
- Routing-score cost term replaced by ledger p50 (inference-economics slice 2) — next run once move
  4 has a day of composition data
- Expected knowledge / entity-grounded recall / provenance chain UI (eco: knowledge-graph) — after
  move 3's credence and class exist
- Chronicler correction verb for owner testimony (rot: knowledge-capture) — design after capture()
  exists; it is the same verb pointed at an episode
- Rule maturity unreachable (226/226 candidate, applied_count 0) — real, cited (writing.py:521-534,
  storage.py:2760-2827); not filed: needs its own design pass on what 'helped' means
- Education curriculum settles 'completed' against a zero-node mind map; 0 review cards ever — real,
  cited (router.py:986-1024,1134-1139); the education butler needs a rot-lens audit of its own, not a
  slice here
- HA `update` domain / battery / offline alerts; actuation risk map unknown-verb gap (eco:
  connectors) — home-iot lens next run
- Steward butler, acoustic sensing, place registry (zero `place` rows), Slack transport — new
  perceptions ranked below vision (move 7)
- Destination admissibility, timezone spine, execution truth for trip_active, trip intentions, fare
  economics ledger, passport document type, dead prompt-mode travel jobs, spec schedule inventory
  (rot: travel-logistics) — after moves 1 and 8
- Baseline deviation, health intents, consultation dossier, care constraints, nutrition_summary
  denominator, calendar-overlay 'appointment' predicate, recovery_state sealed-room subscribers (rot:
  health-wellbeing) — after move 2; the sealed-room class is covered generically by move 5 S8
- Tonight / social taste / co-occurrence / verdict digest / Steam delta scenarios / owner-persona
  binding (rot: media-taste) — after move 10
- Retention requests, decision records, full sovereignty export, recall at relevance (rot:
  knowledge-capture) — after move 9; export scope honesty is move 6g
- Forward horizon, baseline primitive, owner-question lane, sensory-outage lane, engagement signal,
  briefing attribution, deadline UTC day boundary and reverse-urgency threshold order, broker
  expires_at discard, expected_signals transition history and staleness guard (eco: proactivity) —
  real, cited; the two expected_signals items should be filed with the expected-signals epic owner
  rather than here
- Cost-claim slices 4–6 (owner surface, Finance manifesto amendment, autonomy) — after move 12
- Domain rows on the bus, warmup clause, never-blank contract, timing tokens, elapsed route frame
  (cross: interaction-speed) — polish below the trust line
- @theme Dispatch retokening (L; 170 sub-AA text-destructive sites), status registry, Attention
  primitive, visual inventory gate, icon contract, Section quiet rule, Page shell adoption (cross:
  visual-language) — unchanged since run 09; deferred again, deliberately, until the trust queue above
  clears; the 170-site AA fix alone is a candidate S move next run
- Same-cause cluster, per-butler spend ceiling, credential-vs-ceiling correlation,
  permissions-matrix keyboard traversal (surface: config-governance) — ceiling is real and
  critical-rated; needs a spend-governance design pass, not a slice
- Education sources index, condition_id join UI (surface: health-education) — after move 2
- SessionDetailPage referrer-aware back link (surface: activity-spine) — minor, polish
- QA cases connector-subject axis (surface: ops-qa, M) — real; queue behind move 6i so the re-arm
  door exists before the investigation door
- Delegation target resolution by catalog score; fleet_case_evidence contributor attestation (eco:
  cross-butler) — the second is a one-line RLS fix that belongs in bu-8cdl1.7
- Butler with zero cron schedules renders like a healthy idle one (surface: command-control) —
  subsumed by move 6d once schedule_count is real

## Filing

Beads epic (gated, fleet NOT triggered): see the epic and `[HOLD]` gate ids in the final session
report and the run-12 `bd remember` reference memory. Every child carries the packet above as its
design and acceptance sections. Closing the gate bead releases the children to the autonomous fleet
— that release is the owner's move. Run-11 gate bu-hl8vf and run-09 gate bu-xf54r remain open; the
QC cohorts above are the evidence on what the run-11 release actually produced.
