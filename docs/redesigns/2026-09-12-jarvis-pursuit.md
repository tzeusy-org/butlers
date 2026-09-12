# JARVIS pursuit — run 13 (2026-09-12)

Thirteenth recurring generative audit toward a world-class JARVIS-like Butlers system,
feature-exploration first. 24 agents over 8 batches of 3 (≤3 concurrent, staggered across the 8-hour
window; eco/rot/deep/cross = opus/high, surfaces and QC = sonnet/medium, synthesis inline on the
fable orchestrator): 4 UI-maturity QC cohorts (run-12 trust moves, run-12 feature moves, run-11
substrate moves as landed, the run-10 chat epic), 6 surface journeys (command-control,
activity-spine, health-education, life-graph, ops-qa, config-governance), 5 ecosystem lenses
(proactivity, cross-butler, knowledge-graph, connectors, inference), 4 rot lenses over specialist
domains not audited before (relationship-social, education-mastery, sovereignty-resilience,
time-calendar), 2 deep-design agents (spend governance, situation archetypes), and 3 cross-cutting
sweeps (shell discoverability, accessibility, and the first live mobile/on-the-go measurement). No
agent failed or was retried.

Full per-agent structured output lives in `2026-09-12-jarvis-pursuit-data.json`. Access pattern:

```bash
jq '.audits[] | select(.page=="eco: cross-butler")' docs/redesigns/2026-09-12-jarvis-pursuit-data.json
jq '.synthesis.ranked_moves[] | {rank, title, cost}' docs/redesigns/2026-09-12-jarvis-pursuit-data.json
jq '.synthesis.ranked_moves[0].behavior_matrix' docs/redesigns/2026-09-12-jarvis-pursuit-data.json
```

## North star (unchanged from run 09)

> Five-second fleet verification with earned calm: nothing fabricated, failure never impersonates
> health, staleness never wears current-data authority, and every consequential clause is a door
> on an unbroken signal-to-session-to-evidence spine. The interface is keyboard-first, follows
> Dispatch as if built by one hand, respects specialist manifesto ownership, and meets the
> repository engineering and lifecycle bars.

## Tier board and movement

Baselines are the run-12 board in `2026-09-05-jarvis-pursuit.md`. Eight lenses are tiered for the
first time (four rot domains, accessibility, shell discoverability, and the two deep-design lenses,
which are n/a by construction). Mobile was carried weak from run 11 and is measured live here for
the first time. Four QC cohorts audited what the run-12, run-11 and run-10 epics actually produced;
movement on the trust surfaces and the connectors lens cites those verdicts.

| Surface / lens | Run 12 | Run 13 | Movement |
|---|---|---|---|
| surface: command-control | solid | **solid** | Held solid. Heartbeat/schema/push honesty flags and keyboard triage still hold; one new crack: prepared-action origin (#4120) never reaches ApprovalSummary/ApprovalDetail, so every proactive draft wears the red push_failed banner built for undelivered approvals (approvals.py:362-372, models/approval.py:183-211). |
| surface: activity-spine | functional | **functional** | Held functional. Timeline density/seek, failure strip, useListTriage traversal and session-to-chat deep link all landed as designed; the run-12 critical (home butler scheduled skills alert with zero session evidence) is unchanged, and a live parked-approval action_id is reachable only by transcribing a truncated error string (notification-feed.tsx:251; _routing.py:1379,1475). |
| surface: health-education | functional | **functional** | Held functional. No code under the surface changed since run 12 beyond the three ledgered ships; condition_id still null on every live symptom/research row, SymptomTracker still renders the condition as inert text, no education sources index route. No new findings. |
| surface: life-graph | weak | **weak** | Held weak. The three door PRs (#4052/#4057/#4058) are still open; EntityDetailPage still never queries the populated chronicler episodes for the entity, MeetingPrepRail attendee cards still carry entity_id only as a React key. No new findings. |
| surface: ops-qa | weak | **weak** | Held weak. Pause/run-now still unwired from the connector page and QA cases still carry no connector identity (connector_type silently ignored); a verified fix for the routing-rule deep link (commit 570fc0c1e) exists only on a never-pushed local branch. |
| surface: config-governance | weak | **weak** | Held weak, root cause corrected. The generic dashboard-audit middleware does write a row for model-override and runtime_config PATCHes, but the privileged allowlist regex (audit.py:86-98) matches neither family, so consequential autonomy widening is invisible in the default Audit Log view; 'widen a permission' is two unlinked control planes. |
| cross: mobile-otg | weak (carried from run 11) | **broken** | Weak → broken (first live phone measurement). 3,404 of 3,574 interactive targets under the written 44px floor across 38 routes, 20 routes titled 'frontend', and both Telegram-minted routes non-functional on a phone: /secrets?focus= renders in a 46px column (DirectionPassport.tsx:505-508), /approvals/:id opens 1,792px above its dossier (ApprovalsPage.tsx:2092-2096). The Viewport and Modality Contract has never entered the spec base. |
| cross: accessibility | — | **functional** | First tiering: functional. Primitives exist but none is load-bearing: text-destructive paints --red at 3.84:1 on 177 sites, text-white on amber at 1.82:1 on the 'Stale'/'Corrupt' badges, the axe gate renders 30 pending-fetch skeletons with color-contrast disabled, the shared triage cursor is invisible to assistive tech on 11 of 12 surfaces, and bare-key a/d/x have no off switch (WCAG 2.1.4). |
| cross: shell-discoverability | — | **functional** | First tiering: functional. One typed capability manifest covers 48 destinations of roughly 250 addressable states; the butler console is a second unmanaged route table (195 ?tab/&section states, zero capabilities); manifest keywords are dropped before the finder; no catch-all route; 303 standing orders have no listing surface. |
| eco: knowledge-graph | functional | **weak** | Functional → weak. The injected memory block carries no ids so memory_confirm/mark_helpful are unreachable (context.py:152-164); 'effectiveness: 0.00' over a zero denominator; relationship_assert_fact's unchanged branch skips graph projection (~7 live edges over 626 entities); three backfills have no caller; core_groups drift (relationship missing graph/delegation/fleet_cases). |
| eco: connectors | solid | **functional** | Solid → functional. Vision lane, captionless media and the OAuth scope registry landed, but lifetime counters are Prometheus _created epoch timestamps (Gmail 'ingested' 1,789,149,468 messages; heartbeat.py:343-370), ingest.v1 has no source-event clock (ingest.py:793 stamps received_at=now()), retirement leaves live credentials, and the complete backfill subsystem has no door. |
| eco: inference | functional | **functional** | Held functional. The prompt editor says 'No system prompt configured.' while ~21KB of roster identity runs and one save replaces it (butler_management.py:160-201); discretion-tier calls on private WhatsApp/Telegram resolve to gpt-5.6-sol not local qwen; no prompt provenance/digest; approval gating invisible on the tool surface. |
| eco: cross-butler | weak | **broken** | Weak → broken. Finance and Relationship expose zero cross-butler tools because the 2026-04-07 runtime_config core_groups seed overrides git (daemon.py:1251-1282); 5 of 6 domain-event deliveries ever are failed_permanent; the dispatcher treats an empty route result as routed (one owner question stuck 26 days); fleet evidence refs are unopenable; zero fleet cases after RFC 0032. |
| eco: proactivity | functional | **functional** | Held functional. Three run-12 substrates (prepared actions, situation attention, continuity ledger) have zero live producers/adopters; the insight budget is permanently clamped to one per day by an engagement writer with no caller (broker.py:417-465, 531-580); the dashboard's pause-task button calls a nonexistent MCP tool; TOML sync reverts owner schedule edits at every restart. |
| rot: relationship-social | — | **weak** | First tiering: weak. 40 of 66 Relationship tools unregistered (butler.toml enables 4 of 9 groups); Dunbar denominated in contact rows not people; 'unmeasurable' conflates not-tracked with cannot-see; Plex rail says 'ranking source unavailable' over HTTP 200; Gmail never ingests SENT so email-only people tier 1500; the reach-out draft is a constant string. |
| rot: education-mastery | — | **broken** | First tiering: broken. Curriculum shows 'Ready' on an empty mind_maps row; zero review schedules and zero quiz responses ever; mastery graduates on user_answer NULL rows (mastery.py:122-190); velocity from updated_at; two graders with different demotion rules; 'Avg Mastery 0%' over zero; concept entities unjoinable; channel='telegram' hardcoded. |
| rot: sovereignty-resilience | — | **weak** | First tiering: weak. The restore drill cannot pass (psql ON_ERROR_STOP with a role barred from ownership; pg_dump.sh lacks --no-owner); public.fleet_cases excluded from dumps while fleet_case_evidence (NOT NULL FK) is dumped; GET /api/system/backups gunzips seven artifacts synchronously; the green Backups badge never consults restore_drill; plaintext butler_secrets in dumps. |
| rot: time-calendar | — | **functional** | First tiering: functional. Provider recurring masters collapse to one instance (calendar.py:2961 singleEvents=False; live Housekeeping fortnightly once); a 4-day hold renders as 106 h/day (conflicts.py:265-289); flights count as meetings; find-time drops the error envelope and says 'No open slots'; reconcile_day verdicts are discarded. |
| deep: situation-archetypes | — | **n/a** | Design lens, tiered n/a. Found the RFC 0032 spine stranded: switchboard's frozen core_groups seed lacks fleet_cases so open_case is never registered on the sole writer; the opt-in gate is inverted; no title/kind column; RFC 'Implemented' while the only consumer is the lapse sweep. Six-slice design from unstranding to answerable-question registry. |
| deep: spend-governance | — | **n/a** | Design lens, tiered n/a. 84.9% of the month-to-date bill ($113.81 of $134.07) is the QA staffer at Complexity.SPECIALTY on gpt-5.6-sol, filed under travel by butler and ungovernable by any existing object; the singleton ceiling has never been armed; max_tool_calls has never been reachable; Consequence is consumed at one line. Seven-slice design from scoped truth to a weekly governance receipt. |
| qc: run12-trust | — | **solid** | QC cohort over run-12 trust moves 1, 2 and 4: travel repair and cache-honest spend as-designed and live-verified; health truth partial (honest medication supply has no UI field; vitals rollup replaced by in-request aggregation). Two new failure shapes named (13 substrate-substitution, 14 honesty guard permanently tripped). |
| qc: run12-features | — | **functional** | QC cohort over run-12 feature moves 7, 8, 10, 11, 13: vision lane as-designed; journey identity, taste ledger, prepared actions and blind-spot/continuity all partial at the substrate-to-producer boundary. Live critical: /api/lifestyle/taste/summary reports ledger_available:false and zero counts over 6,337 works and 57 verdicts (router.py:77-104 bare except). |
| qc: run11-substrate-landed | weak (run-12 qc: run11-substrate) | **functional** | Weak → functional. Friction ledger and obligation ledger are closed loops verified live; fleet cases and entity graph have real RLS-correct read APIs and zero frontend consumers; entity_graph_walk/path have zero observed invocations across 12 schemas; commuting/ETA has never fired (OWNTRACKS_PLACE_REFERENCES unconfigured). |
| qc: run10-chat | — | **solid** | QC cohort over the run-10 chat epic: solid. NOTIFY wake, phase events, reconnect refetch, docked rail and a11y batch all real; 'real token streaming' honestly not delivered (claude_code.py:488-499 blocks on proc.communicate()). |

### QC verdicts on prior epics (run 12 bu-2jtfw, run 11 bu-8cdl1, run 10 chat)

**qc: run12-trust**
- Move 1: Travel data-path repair (#4038, bu-2jtfw.1): **as-designed** —
  roster/travel/tools/bookings.py:392-405 passes `meta` (a dict) directly into `$13::jsonb`, not
  json.dumps'd (the earlier json.dumps call at :343 is only a pre-insert serializability validation
  whose result is discarded); roster/travel/tools/_helpers.py:_row_to_dict is the single converter
  used by router.py's five model converters (router.py:79-106); src/butlers/jobs/flight_status.py:243
  `l.metadata ? 'flight_number'` now matches real jsonb objects, and :365 sets last_error='no
  selectable legs' (not NULL) on a zero-leg pass;
  frontend/src/components/butler-detail/ButlerTravelTripsTab.tsx:169-274 KpiStrip renders UNAVAILABLE
  plus a degraded banner (data-testid travel-kpi-degraded) instead of a numeral when isError. Live:
  GET /api/travel/trips, /api/travel/trips/{id}, /api/travel/trips/{id}/legs,
  /api/travel/upcoming?within_days=90 all return 200 with decoded metadata objects, including a live
  flight_number ('SQ 800') on the PEK/DD94XR journey.
- Move 2: Health truth (#4036, bu-2jtfw.2; #4115 medication supply): **partial** —
  src/butlers/modules/google_health.py: grep for 'instruction' returns exactly one hit, a docstring
  describing the old anti-pattern — all eight tools now return computed numeric aggregates (e.g.
  health_sleep_history at :439-500). roster/health/tools/reports.py:13,28-52 unions
  VALID_MEASUREMENT_TYPES with a live `_live_measurement_types(pool)` query.
  roster/health/jobs/health_jobs.py:487-495 refuses to fabricate a refill candidate when
  quantity/quantity_updated_at is unset. Gaps vs the packet: (a) no health.vitals_daily table/rollup
  job/GET /api/health/vitals/daily anywhere (grep empty); (b) no expected_signals UNMEASURABLE
  emission for the no-quantity medication case, just a bare `continue`; (c)
  MedicationForm.tsx/MedicationTracker.tsx have zero 'quantity' references — no UI door to record a
  dispense or see 'supply unknown'.
- Move 4: Cache-honest spend (#4039, bu-2jtfw.4; slice-4 digest PR #4043 still open):
  **as-designed** — src/butlers/api/routers/spend.py:232-240 `_sum_tokens` returns four distinct named
  buckets (no more `input_tokens, output_tokens, _, _` discard); :243-253 cache_hit_rate returns None
  (not 0.0) at a zero denominator. src/butlers/core/sessions.py:1100-1174 schedule_costs joins
  `st.enabled` and forces `projected_monthly_runs=0.0` for disabled schedules; spend.py:1509 sorts
  by-schedule with a retired schedule's None projected_monthly_usd treated as -1.0. Live: GET
  /api/spend/summary?period=7d returns total_cached_input_tokens=170436544,
  total_cache_creation_tokens=0, cache_hit_rate=0.873912; GET /api/spend/by-schedule head row is
  chronicler_day_close, a live enabled schedule.
  frontend/src/pages/SpendPage.tsx:282-283,1037,3090,3163 consumes cached_input_tokens and renders the
  divergence_source_error banner. NOT landed on main (matches ledger's known-open PR #4043, not
  re-reported as new): prompt-composition columns and resume_outcome absent from sessions.py/spawner
  (grep empty).

**qc: run12-features**
- Move 7 — vision lane / content-block attachment tool: **as-designed** — telegram_bot.py:146-1595
  real getFile->BlobStore->IngestAttachment pipeline; src/butlers/tools/attachments.py
  attachment_view() returns a real fastmcp Image content block with MAX_INLINE_BASE64_BYTES=64KB
  contract enforced separately for get_attachment(); spawner.py:1316-1324 derives VISION requirement
  from image attachments and folds into dispatch_intent;
  tests/connectors/test_telegram_bot_connector.py:203-335 cover caption-is-verbatim and
  one-attachment-per-photo/document.
- Move 8 — journey identity + connection integrity: **partial** — Substrate
  (booking_records/travellers/leg_passengers/airport_minimum_connect/travel.connections,
  migrations/003_journey_identity.py) and derivation (tools/connections.py
  compute_connection_verdict/holds-tight-broken-unknown, recompute wired from both bookings.py:879 and
  src/butlers/jobs/flight_status.py:326 on delay) are real and reachable via direct tool calls. But
  live GET /api/travel/trips/{id} for the packet's own DD94XR fixture and for a trip created after the
  ship date both show party:[] and connection_reason:null, because the LLM-facing record_booking
  contract (roster/travel/.agents/skills/tool-reference/SKILL.md:23-41) never emits
  booking_record_id/segment_index/traveller fields.
- Move 10 — taste ledger: **partial** — works/taste_signals/verdicts tables,
  connectors.spotify_track_plays writer (spotify.py TrackObservation/TrackPlayEvidence,
  GREATEST()-based max_progress_ms upsert), and honest degraded-envelope code in router.py are all
  real; decorative weekly-digest stub confirmed retired (ButlerLifestyleTasteTab.tsx:16 comment). But
  live GET /api/lifestyle/taste/summary returns ledger_available:false and all-zero totals while
  /taste/works and /taste/verdicts simultaneously prove 6337 and 57 real rows exist — the Taste tab's
  own KPI strip and error banner are actively lying to the owner right now.
- Move 11 — prepared actions: **partial** — park_prepared_action
  (src/butlers/modules/approvals/park.py:126-149) never calls emit_approval_push, confirmed;
  relationship_jobs.py:554-616 is a real, wired producer using dedup keys;
  insight_candidates.prepared_action_id plumbed through broker.py:194-886 into the digest door render
  (render_prepared_action_door); reach_out_draft fully retired (zero repo hits). But grep finds no
  finance subscription-cancellation or email-reply-preparation producer anywhere — only relationship
  (named) plus travel's connection-risk door (not named in the packet) call park_prepared_action.
- Move 13 — blind-spot preamble + task continuity: **partial** — fetch_blind_spot_preamble
  (spawner_context.py:371-403) is called on the real spawn path (spawner.py:1956-1981) with a
  confirmed fail-closed path (expected_signals.py:20,67,280-324, BLIND_SPOT_QUERY_FAILED_TEXT). Task
  continuity is fully wired at the dispatch seam (scheduler.py:2330-2338). But
  blind_spot_declarations.py's own docstring states only Health has a declared pattern registry entry,
  and no roster butler.toml sets continuity=true anywhere in the repo.

**qc: run11-substrate-landed**
- Move 7: Fleet Case File consumers (#4024 read API, #4031 contribution tools, #4032
  situation-scoped attention, #4033 lapse sweep, #4034 backfill): **partial** — Backend fully
  as-designed: src/butlers/core_tools/_fleet_cases.py implements all six tools with correct
  Switchboard-forwarding, idempotent evidence (kind,ref) uniqueness, situation-scoped attention bypass
  via fleet_cases.evaluate_case_attention, and three-ledger binding (Slice 7, #4035).
  roster/switchboard/api/router.py:500-691 gives a correct, degraded-envelope-safe read API,
  live-confirmed at GET /api/switchboard/cases (200, empty page -- no cases open in dev). But: zero
  frontend code anywhere references fleet cases (repo-wide grep found none); the packet's promised
  'dashboard /cases surface' (surface_map) / 'S6 dashboard case container' (slice_plan) never shipped.
  The case object remains invisible to the owner exactly as the audit prompt hypothesized.
- Move 8: Entity graph consumers (#4029 entity_graph_walk/path, #4041 graph coverage on catalog
  search, #4093 edge repoint on merge, #4099 cascade on retraction): **partial** — #4093 and #4099 are
  real, verified data-integrity mechanisms (edge repoint on entity merge, cascade on raw fact
  retraction). entity_graph_walk/entity_graph_path (src/butlers/core_tools/_graph.py) are correctly
  implemented zero-LLM recursive-CTE tools, always registered -- but a live cross-schema SQL scan of
  sessions.tool_calls across 12 reachable butler schemas found zero genuine invocations of either tool
  (one apparent hit was a git-log substring false positive). graph_coverage
  (src/butlers/api/routers/memory.py:1471-1542) is computed and returned by the catalog-search API but
  has no frontend consumer. The entity dossier route/panel is confirmed still missing (matches
  ledger's known-gap note, not re-reported as new).
- Move 9: Friction ledger console (#4027, sessions_friction + sessions_summary outcome columns +
  butler console panel): **as-designed** — GET /api/butlers/switchboard/analytics/friction?period=30d
  returned real data: succeeded:737, failed:6, by_error_marker:{other:6}, by_kind all-zero friction.
  src/butlers/api/routers/sessions.py:1015-1054 combines the sessions_friction ledger with
  sessions_summary's succeeded/failed/by_error_marker exactly as the packet specified. Frontend:
  ButlerFrictionPanel (frontend/src/components/butler-detail/ButlerFrictionPanel.tsx) is mounted in
  ButlerSessionsTab.tsx:123 and consumes this exact endpoint via use-butler-analytics.ts. Table schema
  switchboard.sessions_friction matches the packet's CHECK-constrained kind vocabulary exactly
  (degenerate_tool_loop, guardrail_termination, classification_timeout, recovered_error, dead_end).
  Zero friction rows currently exist fleet-wide across all 13 schemas except general (2 rows) --
  consistent with a genuinely clean/low-friction dev fleet, not a broken writer (the reader-side
  succeeded/failed numbers are real and non-zero, proving the pipeline works).
- Move 10: Obligation ledger insights (#4028, cancellation_url/notice_period_days/cancel_by +
  obligation_ledger + dashboard door): **as-designed** — GET /api/finance/obligations returned real
  subscription rows (Rental Property Lease, Endowus CPF OA, etc.) each carrying unknown_door=true
  honestly (enrichment fields genuinely null in dev, not fabricated).
  roster/finance/tools/alerts.py:462-580 register_obligations() derives warn_by = cancel_by -
  notice_period_days only when all three door fields are known, else sets unknown_door and leaves
  warn_by NULL -- matches packet's behavior_matrix exactly. Scheduled via
  roster/finance/jobs/finance_jobs.py:787-889 (_register_obligations called from the finance job
  runner, not dead code). Frontend renders it: ButlerFinanceFinancesTab.tsx:513-567 ObligationDoorLine
  component explicitly branches on unknown_door and renders
  cancel_by/days_remaining_to_act/price_change fields, mounted per-subscription row (line 1079).
- Move 11: OwnTracks commuting/ETA context producer (#4030, context_producers.py + context_bus):
  **partial** — run_commuting_eta_context_producer (src/butlers/jobs/context_producers.py:657+) is
  correctly implemented (freshness window, closing-distance derivation, arrived-clears-immediately,
  at_home-suppresses, unconfigured-leaves-untouched) and is genuinely scheduled
  (roster/travel/butler.toml:127-130, job_name context_producer_commuting_eta). Consumption is
  architecturally real and generic, not per-signal wiring: src/butlers/core/spawner_context.py:338
  calls context_bus.get_active_context()+format_context_preamble() at session spawn, which reads ALL
  active public.user_context rows (no signal-name filtering) and injects them into every session's
  preamble -- so 'commuting' would reach every butler's prompt automatically once asserted, matching
  the packet's intent. However: public.user_context has zero rows for commuting/at_home/in_space in
  this dev stack despite 1,322 real connectors.owntracks_points rows existing, because
  OWNTRACKS_PLACE_REFERENCES is unset in the running process -- the signal has never actually fired
  live, so this is source-confirmed correct but unverified in live behavior (a dev-environment
  configuration gap, not a code defect).

**qc: run10-chat**
- .7 real token streaming + phase events + reconnect (#4118), #4124 jump-to-message parity:
  **partial** — NOTIFY wake real: conversations.py:871-899, chat_stream.py. Phase events real and
  honestly gated: conversations.py:486-725. Reconnect = refetch-on-non-abort-failure (real, tested:
  FloatingChatWidget.test.tsx:767). Token streaming NOT real: claude_code.py:488-499 blocks on
  proc.communicate(); chat_stream.py:13-16 states no producer exists. #4124 jump-to-message (git show
  0614db304) is a clean, well-verified re-implementation.
- .11 chat postures docked rail + /chat routes (#4119), #4138 session-detail deep-link:
  **as-designed** — ChatDock/PageContextProvider mounted once in RootLayout.tsx wrapping <Outlet/>
  (RootLayout.tsx:150-172) so dock state survives route changes by construction. /chat routes
  registered with 'ref-only' context policy (page-context-registry.ts:45-46), avoiding a
  fabricated/circular ContextChip snapshot. Keyboard-operable resize handle at ChatDock.tsx:168-182.
  #4138 (git show 8c59abdd9) is a real, scoped fix.
- .13 chat a11y batch (#4117): **as-designed** — Sentence-batched live region, initial focus moved
  to composer, bare 'c' shortcut with page-claim guard, WCAG 2.4.11 obscure-guard,
  prefers-reduced-motion respected (commit e7b2e1a9e). ChatDock (built same day, after) reuses
  MessageThread/MessageInput directly (ChatDock.tsx:24-25), inheriting this work rather than
  duplicating/regressing it, plus adds its own keyboard-operable resize separator.
- .1 propose-then-act: **as-designed** — Not independently re-traced this run beyond keyword
  presence; no intersection found with the .7/.11/.13 diffs.
- .2 answer_question/cannot_answer lane: **as-designed** — answer_question/cannot_answer symbols
  present and used in src/butlers/modules/pipeline.py and src/butlers/core_tools/_switchboard.py on
  current main; untouched by the .7/.11/.13 diffs.
- .4 page-context v2 ContextChip: **as-designed** — page-context.tsx policy snapshot/ref-only/none
  logic (lines 176-204) intact and correctly extended for /chat routes with 'ref-only'
  (registry.ts:45-46) rather than a circular full snapshot.
- .5 thread integrity (session_id + tool_calls on replies): **as-designed** —
  conversations.py:906-918 still best-effort-stamps session_id via message_set_session_id_if_null;
  #4118 PR body states 'no persistence changes, the reply row stays canonical'.
- .9 conversation_recall + message FTS (#4150 evaluated index reuse): **as-designed** —
  conversations.py:1542-1547+ still implements owner-scoped, cursor-paginated FTS with ts_rank
  ordering; #4150 (known-landed per ledger) evaluated index reuse without changing the query contract.

## Systemic themes

### 1. The frozen seed: runtime_config core_groups overrides git, so butlers lose tools they declare

runtime_config.py seed_if_empty (ON CONFLICT DO NOTHING) captured each butler's core_groups on
2026-04-07 and has governed ever since; daemon.py:1251-1282 reads the row, not butler.toml. Finance
and Relationship therefore expose zero cross-butler tools, Switchboard never registers open_case on
the only role that may write fleet cases, and Relationship runs 26 of its 66 tools. Four independent
lenses (cross-butler, relationship, situation-archetypes, knowledge-graph) each rediscovered the
same row. The fix is one reconciliation with an audit receipt, not four.

- src/butlers/core/daemon.py:1251-1282
- src/butlers/core/runtime_config.py:145-170
- roster/relationship/butler.toml:179-180
- src/butlers/core_tools/_fleet_cases.py:81-105

Affected: eco: cross-butler, rot: relationship-social, deep: situation-archetypes, eco:
knowledge-graph

### 2. Substrate shipped, consumer never (run-12 theme, now the dominant shape)

Fleet cases (read API, six tools, lapse sweep, zero frontend lines, zero cases), prepared actions
(origin NULL on all 50 recent rows, one producer), task continuity (zero adopters), backfill (jobs
table, two tools, six REST endpoints, no door), graph backfills (three, no caller), reconcile_day
verdicts (discarded), review-card scheduler (zero schedules ever), graph_coverage (no consumer),
blind-spot registry (one module), entity_graph_walk (zero invocations across 12 schemas). Run 12's
packets each promised an owner-visible artifact and roughly a third of those artifacts never landed
even where the computation is honest.

- roster/switchboard/api/router.py:500-691
- roster/switchboard/tools/backfill/controls.py:39-120
- src/butlers/core/blind_spot_declarations.py:23-25
- roster/education/.../spaced_repetition.py:266-303

Affected: qc: run11-substrate-landed, qc: run12-features, eco: proactivity, eco: connectors, eco:
knowledge-graph, rot: education-mastery, rot: time-calendar

### 3. Honesty guards that impersonate failure, and failures that impersonate health, on the same page

Find-time renders an upstream error as 'No open slots'; the Plex rail says 'ranking source
unavailable' over HTTP 200; the taste summary reports ledger_available:false over 6,337 rows because
one bare except spans six queries; the spend divergence guard is permanently tripped by wa:*@lid
pseudo-butlers; prepared drafts wear the push_failed banner; the Backups tile is green without ever
consulting restore_drill; the prompt editor says 'No system prompt configured.' while 21KB runs.
Both directions break the north star equally: the owner learns to ignore the red and trust the
green.

- roster/lifestyle/api/router.py:77-104
- src/butlers/api/routers/spend.py:388-417
- frontend/src/components/system/BackupTile.tsx:94-142
- src/butlers/api/routers/butler_management.py:160-201
- src/butlers/modules/calendar.py:11296-11315

Affected: qc: run12-features, qc: run12-trust, surface: command-control, rot:
sovereignty-resilience, eco: inference, rot: time-calendar, rot: relationship-social

### 4. The writer's clock and the zero denominator

Prometheus _created epoch values summed as 'messages ingested'; received_at=now() as the only
ingestion clock; updated_at as mastered_at and as learning velocity; evidence contributed_at with no
basis; the routine label frozen while its window refreshes. And the ratios: 'Avg Mastery 0%' over
zero graded rows, 'effectiveness: 0.00' over zero applications, a restore drill that passes on
count(*)>0, Dunbar tiers denominated in contact rows, an insight budget divided by an engagement
column nobody writes, /api/sessions/aggregate silently ignoring period= so run 12's weekly fleet
numbers were all-time.

- src/butlers/connectors/heartbeat.py:343-370
- roster/switchboard/tools/ingestion/ingest.py:793
- roster/education/.../mastery.py:361-403
- roster/switchboard/tools/insight/broker.py:417-465
- src/butlers/api/routers/sessions.py:503-542

Affected: eco: connectors, rot: education-mastery, eco: knowledge-graph, eco: proactivity, deep:
spend-governance, rot: sovereignty-resilience, rot: relationship-social

### 5. The phone was never designed, and the a11y gate certifies a skeleton

95.2% of interactive targets fail the written 44px floor with zero (pointer: coarse) rules in the
tree; the two backend-minted phone routes cannot render their payload; the Dialog primitive has no
height ceiling; the Viewport and Modality Contract has sat unarchived since 2026-09-03. On the a11y
side the axe sweep renders every route with fetch pending forever and color-contrast disabled, so
text-destructive at 3.84:1 across 177 sites and white-on-amber at 1.82:1 on the failure badges are
invisible to CI. Both are gates as metadata: a floor with no primitive and no check.

- frontend/src/components/secrets/passport/DirectionPassport.tsx:505-508
- frontend/src/pages/ApprovalsPage.tsx:2092-2096
- frontend/src/test/axe/route-pages.a11y.test.tsx:38,50
- frontend/src/index.css:52,396

Affected: cross: mobile-otg, cross: accessibility

### 6. Governance objects too coarse to be armed

One fleet-wide ceiling (never set) cannot express 'cap the QA lane'; the autonomy ladder
fingerprints the message body so the most-approved tool can never promote; the privileged audit
filter is a hand-maintained regex that misses runtime_config and model-override writes; owner
schedule edits are leases that expire at restart. The system computes every fact needed to govern
(purpose, consequence, origin, operation) and discards it at the policy boundary.

- alembic/versions/core/core_094_spend_tables.py:96-104
- src/butlers/modules/approvals/autonomy_tracker.py:41-78
- src/butlers/api/routers/audit.py:86-98
- src/butlers/core/scheduler.py:805-814

Affected: deep: spend-governance, eco: proactivity, surface: config-governance, cross:
shell-discoverability

### 7. Delivery-process seams: finished work that never reached main

A verified, tested fix for the routing-rule deep link has lived on a local-only branch since
2026-09-06; the viewport contract has lived in an unarchived change directory since 2026-09-03;
three life-graph door PRs are open a week on. The lifecycle bar has its own 'computed then
discarded' shape.

- local branch agent/bu-2jtfw.6 (570fc0c1e)
- openspec/changes/amend-dispatch-viewport-modality-contract/
- PRs #4052 #4057 #4058

Affected: surface: ops-qa, cross: mobile-otg, surface: life-graph

## Ranked moves

Ranked by owner value per unit cost, doctrine-weighted: trust and honesty defects outrank features,
features outrank polish. Moves 1–7 are trust defects (1 unblocks everything above it; 5 bundles
fourteen S-sized seam repairs); 8, 9 and 11–15 are features; 10 is the physical-access UX repair.
Every move carries a full Dispatch Readiness Packet in the data JSON (outcome, non-goals, governing
intent, surface map, behavior matrix, doc impact, verification, slice plan); the prose below is the
why and the shape.

### 1. The fleet can speak: reconcile core_groups with git, register the missing tools, and make an empty route a failure (M, trust-defect)

The north star's first clause is 'nothing fabricated'. Today the fabrication is structural: Finance
and Relationship butlers cannot call a single cross-butler tool, and Relationship runs 26 of its 66
declared tools, because a frozen runtime_config core_groups seed from 2026-04-07 overrides what
butler.toml declares. The dispatcher then treats an empty route result as 'routed', so an owner
question sat unanswered for 26 days with the system reporting success, and five of six domain-event
deliveries ever attempted are failed_permanent with no replay. Every downstream feature in this
dossier (situations, prepared actions, occasion drafts, fleet cases) assumes the fleet can talk;
nothing above this move is honest until it can. The owner payoff is the manifesto promise itself:
the Switchboard routes, specialists answer, and a question that cannot be routed is surfaced as a
failure rather than swallowed.

**Outcome.** Every butler's live tool surface equals the union of butler.toml core_groups and roster
tool modules, with a three-way diff (toml / runtime_config / registered) visible on the butler
console and written to the audit log when it is non-empty. Relationship registers 66 tools, Finance
and Relationship expose cross-butler tools, Switchboard registers open_case. A route that resolves
to zero targets returns a typed failure the owner sees on the Command surface, and failed_permanent
domain-event deliveries have a replay verb.

**Slices.** S1 [S] precedence + reconcile audit row + Tool surface card. S2 [S] empty-route typed
failure + Command row + Retry. S3 [S] domain-event delivery replay verb + failed_permanent exposure
on butler console. S4 [S] register the 40 missing Relationship tools by enabling groups in
butler.toml with manifesto review.

Source: eco: cross-butler (M0, M1, M4; findings 0-4); rot: relationship-social (M0; finding 0);
deep: situation-archetypes (S1 unstranding; findings 0-1); eco: knowledge-graph (M4; finding 4)

### 2. Effective-prompt receipt, roster-drift sentinel, and a purpose lane for private content (M, trust-defect)

The prompt editor says 'No system prompt configured.' while roughly 21KB of roster identity runs
every session, and one save from that editor silently replaces the whole composition. The owner
cannot see what any butler was actually told, cannot see when the roster on disk drifts from what
runs, and cannot see that discretion-tier calls over private WhatsApp and Telegram content are
resolving to a remote frontier model instead of the local model the manifesto promises. This is
'staleness wearing current-data authority' and 'failure impersonating health' on the most
consequential surface the owner has: the words that define each butler. The payoff is a receipt: for
any session, the exact effective prompt with its provenance and digest; for any butler, a sentinel
that says the running prompt matches git; for any private-content call, a routing lane that is
visible and enforced.

**Outcome.** Each session row stores prompt_digest and a prompt_provenance list (source, bytes, sha)
and the session detail page renders the effective prompt on demand. The butler console shows
'Prompt: matches git @<sha>' or 'Drifted since <date>' from a sentinel comparing the composed prompt
to the roster tree. A purpose_lane field on dispatch marks private-content sessions and the router
refuses to place them on non-local models unless an audited override exists.

**Slices.** S1 [S] digest + provenance on the session ledger and session detail render. S2 [S]
editor preview + drift sentinel on the console. S3 [M] purpose_lane on dispatch and router
enforcement with audited override.

Source: eco: inference (M0, M4; findings 0-3)

### 3. Proof of recovery: a restore drill that can pass, dumps that can restore, and a Backups tile that tells the truth (L, trust-defect)

The Backups tile is green. It has never consulted the restore drill, and the restore drill cannot
pass: psql runs with ON_ERROR_STOP under a role that is barred from taking ownership while
pg_dump.sh omits --no-owner. public.fleet_cases is excluded from the dump while fleet_case_evidence,
which carries a NOT NULL foreign key to it, is included. The GET /api/system/backups endpoint
gunzips seven artifacts synchronously on every load, and butler_secrets are dumped in plaintext.
This is the purest 'failure impersonating health' in the run: the one surface whose entire purpose
is to be believed in a crisis is the least earned. The payoff is a single owner-facing fact, 'last
proven restore: <date>, <n> tables, <size>', that is computed from a drill that actually ran to
completion.

**Outcome.** pg_dump.sh emits an owner-neutral, secret-redacted dump whose table set is closed under
foreign keys. The drill restores into a scratch database, runs row-count and FK checks, and writes
public.restore_drills(status, started_at, tables, bytes, failure). The Backups tile reads the latest
drill: green only when the last drill within the policy window passed; amber when stale; red on
failure, with the failure text one click away. The backups endpoint reads a manifest instead of
decompressing artifacts.

**Slices.** S1 [S] dump flags + FK-closed table set + secret redaction. S2 [M] drill that restores
to scratch and records restore_drills. S3 [S] manifest endpoint + tile truth. S4 [S]
attention-ledger notification on drill failure.

Source: rot: sovereignty-resilience (M0, M5; findings 0-6)

### 4. Education honesty: Ready only with nodes, review cards that exist, mastery only on owner evidence, and a transitions ledger (L, trust-defect)

The curriculum page shows 'Ready' on an empty mind_maps row; zero review schedules and zero quiz
responses have ever been written; mastery graduates on rows whose user_answer is NULL; velocity is
computed from updated_at; two graders apply different demotion rules; and 'Avg Mastery 0%' is a
ratio over zero. The manifesto promises a tutor that knows what the owner knows. Today it fabricates
progress in both directions. This is the run's only 'broken' domain lens with a coherent repair
path: make each claim require its evidence, make the spaced-repetition scheduler actually produce
cards, and record every mastery transition with the evidence that caused it so the owner can audit
'why does it think I know this?'

**Outcome.** Curriculum 'Ready' requires at least one node and a plan digest. The scheduler runs as
a standing order and writes review_schedules; the review card path delivers through notify()
posture, not a hardcoded channel. Mastery transitions are written to
education.mastery_transitions(concept_id, from, to, evidence_kind, evidence_ref, at) and only
owner-answered rows count. Aggregate stats render 'no graded evidence yet' instead of 0%. One
grader.

**Slices.** S1 [S] Ready gate + KPI empty-state + single grader. S2 [M] scheduler as standing order
+ notify() posture delivery. S3 [M] mastery_transitions migration, writer, and 'Why' panel.

Source: rot: education-mastery (M0, M2, M4; findings 0-8)

### 5. Seam-truth bundle: fourteen S-sized repairs where a computed fact is discarded at the last hop (M, trust-defect)

Fourteen independent lenses each found one or two places where the backend computes the honest fact
and the seam to the owner throws it away: a bare except that turns 6,337 taste works into 'ledger
unavailable'; Prometheus _created timestamps rendered as 'messages ingested'; a pause button calling
a tool that does not exist; an insight budget divided by a column nobody writes; a prepared draft
wearing the push_failed banner; a parked approval id buried in an error string; a verified fix for
the routing-rule deep link stranded on a local branch; the privileged audit filter missing two
families; a medication supply the backend refuses to fabricate and the UI never asks for; a spend
divergence guard permanently tripped by WhatsApp pseudo-butlers; find-time rendering an error as 'No
open slots'; a cadence label frozen while its window refreshes; memory ids stripped from the
injected block so confirm/helpful can never fire. Each is under a day. Bundled, they remove the
largest share of 'failure impersonating health' the run found, and they are the cheapest trust the
fleet can buy.

**Outcome.** Each seam renders the computed fact or an honest failure: taste summary returns partial
success with per-query status; connector counters read real ingest counts or 'unavailable';
pause-task calls a registered tool; insight budget re-based on delivery count; prepared drafts show
a calm 'Prepared' badge; parked approvals link to /approvals?id=; routing-rule deep link merged;
privileged filter includes runtime_config and model-override families; medication form has a supply
field; spend divergence ignores pseudo-butlers; find-time shows the upstream error; cadence label
recomputed with its window; memory ids present so confirm/helpful can fire.

**Slices.** S1-S14, one per evidence line, ordered by owner visibility: taste summary, connector
counters, prepared origin, parked approval link, audit predicate, schedule_toggle, insight budget,
spend divergence, find-time envelope, medication supply field, routing-rule cherry-pick, cadence
label, memory ids, and a closing sweep to record any new shape in the taxonomy.

Source: qc: run12-features (F0); eco: connectors (F0); eco: proactivity (F0, F1); surface:
command-control (F0); surface: activity-spine (F0); surface: ops-qa (F0); surface: config-governance
(F0); qc: run12-trust (move 2 partial, F2); rot: time-calendar (M5); rot: relationship-social (M6);
eco: knowledge-graph (M0)

### 6. Recurring events become occurrences, and day load is honest (M, trust-defect)

The calendar module fetches provider events with singleEvents=False, so every recurring master
collapses to a single instance: the live fortnightly Housekeeping series appears once. A four-day
hold renders as 106 hours per day because conflicts.py spreads multi-day spans without clipping to
the day. Flights count as meetings. The owner's day, which the manifesto calls the butler's primary
object, is therefore wrong on both axes: what is on it and how full it is. This is fabricated load
and hidden commitments on the same screen. The payoff is a day view whose count and density match
what the provider would show, so 'am I free Thursday' is answerable.

**Outcome.** Occurrence expansion lands as a projection table calendar.event_occurrences(master_id,
start, end, instance_key) refreshed on sync with the provider's expanded instances. Day load clips
each occurrence to the day and classifies kind (meeting, hold, travel, all-day) so density counts
only attention-bearing kinds. reconcile_day verdicts are persisted and rendered as a day-level
'Reconciled at <time>: <n> drifts' line.

**Slices.** S1 [S] singleEvents expansion into occurrences projection. S2 [S] day-load clipping +
kind classification. S3 [S] reconcile_day persistence + header line.

Source: rot: time-calendar (M0, M2; findings 0-5)

### 7. The graph self-heals and speaks in names (M, trust-defect)

relationship_assert_fact's 'unchanged' branch skips graph projection, so the entity graph has
roughly seven live edges over 626 entities; three backfills exist with no caller; 'effectiveness:
0.00' is rendered over a zero denominator; fleet evidence references are opaque ids nobody can open;
and relationship's core_groups row lacks graph, delegation and fleet_cases. Run 12 tiered the
knowledge graph functional on the strength of its substrate; run 13 finds the substrate unfed. The
payoff: the graph stays consistent with the facts that should feed it, the owner sees names not ids,
and any 'effectiveness' or 'coverage' number shows its denominator or does not render.

**Outcome.** A standing order runs a graph reconciler that diffs entity_facts against projected
edges and repairs drift, writing a receipt (edges added/removed, duration). Evidence refs resolve to
entity names and open the entity page. Ratio widgets take a denominator and render 'no applications
yet' below a threshold. The core_groups three-way diff from move 1 includes
graph/delegation/fleet_cases for relationship.

**Slices.** S1 [S] projection on the unchanged branch + reconciler as standing order with receipt.
S2 [S] denominator-aware ratio widgets. S3 [S] named evidence refs.

Source: eco: knowledge-graph (M2, M3, M4; findings 1-5); eco: cross-butler (finding 3)

### 8. Situations spine: unstrand RFC 0032 and give fleet cases a dashboard container (L, feature)

RFC 0032 is marked Implemented. The read API exists, six tools exist, the lapse sweep exists, and
there are zero cases, zero frontend lines, and no producer that can write one, because the
switchboard's frozen core_groups seed lacks fleet_cases and the opt-in gate is inverted. Situations
are the manifesto's answer to 'what is going on across my life right now', the one object that can
hold a multi-butler thread (a trip, a health episode, a house repair) with its evidence and open
decision. Move 1 unstrands the writer; this move gives the object a title and a kind, a first
deterministic producer, and the container the owner opens. The payoff is a Situations surface that
starts non-empty on day one because the lapse sweep and the silent-sense producer (move 12) already
know what is wrong.

**Outcome.** public.fleet_cases gains title, kind (archetype enum) and summary; the gate is
corrected and open_case registers on switchboard. A /situations route with capability, keywords and
a list/detail page renders open cases with their evidence, linked sessions, and the owner decision
if any. The lapse sweep opens a case per lapsed source with a title. Closing a case withdraws its
linked prepared actions (move 13 S5).

**Slices.** S1 [S] gate fix + columns + registration (after move 1 S1). S2 [M] /situations route,
capability, list + detail. S3 [S] lapse sweep titles + attention strip count. S4 [S] close_case
withdraws linked prepared actions.

Source: deep: situation-archetypes (S1, S2; findings 0-4); qc: run11-substrate-landed (M0); cross:
shell-discoverability (capability model)

### 9. Spend governance slices 1-3: scoped spend truth, a dispatch_governance object, and reachable max_tool_calls (M, feature)

84.9% of the month-to-date bill is one lane (QA staffer at SPECIALTY complexity on a frontier model)
that no existing object can see or cap: /api/sessions/aggregate ignores its period parameter so run
12's weekly numbers were all-time, the singleton spend_ceiling has never been armed, max_tool_calls
has never been reachable, runtime_failure causes are typed but never grouped, and staffer spend is
filed under the domain butler that spawned it. The manifesto promises an owner who can steer the
fleet's cost and risk without reading logs. The payoff is three facts and one lever: spend by scope
(butler, trigger class, purpose, consequence) with a real period; causes of waste grouped; a
governance object that can say 'QA lane: cap $20/week, degrade to sonnet'; and a tool-call ceiling
that actually stops a runaway session while preserving partial output.

**Outcome.** GET /api/spend/scoped?dimension=&period= and GET /api/spend/causes return honest
windows; SpendPage shows lane and cause breakdowns with staffer spend attributed to the staffer.
public.dispatch_governance (core_232 if unclaimed) holds scoped rules (fleet, butler, trigger_class,
consequence, butler+trigger_class) with ceiling, degrade_mode and a preview endpoint; the singleton
ceiling migrates into it. max_tool_calls is enforced at the spawner with partial-output
preservation. A weekly governance receipt replaces the mis-keyed classification_timeout.

**Slices.** S1 [S] period honesty + scoped/causes endpoints + Spend tabs + staffer attribution. S2
[M] dispatch_governance table, resolver, endpoints, preview, singleton migration, audit + reason. S3
[S] reachable max_tool_calls with partial-output preservation (after bu-2jtfw.5 S2). S4 [S] weekly
governance receipt + classification_timeout re-key.

Source: deep: spend-governance (M0, M1, M2, M6; findings 0-9); surface: config-governance (audit
predicate dependency)

### 10. Physical access: the phone and the screen reader get designed (L, ux-repair)

3,404 of 3,574 interactive targets are under the written 44px floor with zero coarse-pointer rules;
the two routes minted into Telegram deep links cannot render their payload on a phone; Dialog has no
height ceiling; 177 sites paint the destructive colour at 3.84:1 and the failure badges
white-on-amber at 1.82:1; the axe gate certifies 30 pending-fetch skeletons with contrast checks
disabled; the shared triage cursor is invisible to assistive tech; bare-key shortcuts have no off
switch. The north star says five-second verification; the manifesto says the owner reaches the fleet
from wherever they are. Today wherever-they-are excludes a phone and excludes a screen reader. The
payoff is a floor with primitives: a device-band hook, a coarse-pointer lane, a Dialog that fits,
colour pairs graded as pairs, and a gate that measures the product.

**Outcome.** use-device-band and use-entry-focus hooks; DirectionPassport and ApprovalsPage phone
postures (single column, dossier first on deep link). A @media (pointer: coarse) lane that expands
hit areas via ::after with a frozen allowlist guard. Dialog max-h-[calc(100dvh-2rem)] with a
bottom-sheet variant. Graded colour pairs (--red-text on surfaces, --state-fill-foreground on fills)
codemodded across the 202 sites with a registry guard. The axe suite renders populated fixtures with
the shell mounted and colour-contrast enabled in both themes. A lint rule against aria-label on
generic elements. A 'consequential shortcuts' off switch in local settings.

**Slices.** S1 [M] device-band + entry-focus hooks and the two phone postures. S2 [S] coarse-pointer
lane + guard + allowlist. S3 [S] Dialog dvh + bottom sheet + vh ban. S4 [M] graded pairs + registry
foregrounds + codemod + guard. S5 [M] axe gate measures product. S6 [S] aria-label lint +
consequential shortcuts off switch. S7 [S] archive viewport contract into the spec base.

Source: cross: mobile-otg (M0, M1, M2, M5; findings 0-4, 10); cross: accessibility (M0, M1, M2, M5;
findings 0-3, 6)

### 11. Standing Orders page, sub-destinations as capabilities, a browsable finder, and a catch-all route (L, feature)

303 standing orders (191 enabled) run the fleet and there is no page that lists them; GET
/api/schedules returns 404. The shell's typed capability manifest covers 48 destinations of roughly
250 addressable states, so the butler console's 195 tab/section states are invisible to the finder
and to chords; manifest keywords are dropped before the finder sees them; the finder is empty until
a keystroke; and a mistyped URL renders a blank shell with no error element. 'Five-second
verification' requires that the owner can reach anything by name from anywhere. The payoff is one
navigation authority (the capability registry) that the finder, the chords, the '?' sheet and the
not-found page all project from, and a first-class page for the thing that actually runs the house.

**Outcome.** /standing-orders lists every scheduled task with butler, cadence, last/next run, owner
override state (move 13 S3) and enable/run-now verbs, fed by GET /api/schedules; a
dashboard_read_standing_orders tool lets chat answer 'what runs tonight'. Capabilities gain a family
projection and SUB_CAPABILITIES so console tabs and sections are addressable, with a lint that
forbids hand-written nav-link lists. The finder loads keywords and shows browsable families on empty
query. A catch-all route renders NotFoundPage with the nearest capability by keyword.

**Slices.** S1 [M] GET /api/schedules + Standing Orders page + read tool. S2 [M] family projection +
SUB_CAPABILITIES + console tabs + lint. S3 [S] keywords load-bearing + browsable empty finder. S4
[S] catch-all route + errorElement.

Source: cross: shell-discoverability (M0, M1, M2, M5; findings 0-3, 7, 9)

### 12. Connectors: an event clock with provenance, a coverage window with a backfill door, and silent senses that open a case (L, feature)

ingest.v1 has no occurred_at, so received_at=now() is the only clock and a backfilled year of email
lands 'today'; the connector has no notion of what range of the source it has covered, so the
complete backfill subsystem (jobs table, two tools, six endpoints) has no door because there is
nothing to point it at; and when a sense goes dark (the Telegram user client has been silent since
2026-07-18) no deterministic producer says so. The manifesto promises senses the owner can trust.
The payoff is three owner-visible facts per connector: when things happened (not when we heard),
what span of the source we hold, and whether the sense is alive, with a case opened when it is not.

**Outcome.** ingest.v1 carries occurred_at and occurred_at_source ∈ {source_stated,
connector_derived, receipt_only}; a core migration adds both to public.ingestion_events and
timeline/search order by occurred_at with provenance shown. public.connectors.source_coverage
records covered spans per source; GET .../coverage feeds a coverage bar on ConnectorDetailView with
a 'Backfill <range>' verb bound to the existing controls. src/butlers/jobs/source_lapse.py opens a
fleet case (correlation_key source_lapse:{type}:{identity}) when expected_signals lapse.

**Slices.** S1 [M] occurred_at + provenance (contract, migration, timeline). S2 [M] source_coverage
+ endpoint + coverage bar + backfill verb. S3 [S] source_lapse producer (after move 8 S1).

Source: eco: connectors (M0, M1, M3; findings 1, 3, 6)

### 13. Proactivity: a producer contract, owner steering directives, delivery posture, and case-scoped drafts (M, feature)

Prepared actions have one producer and an allowlist of one resolver; the owner has no way to say
'stop suggesting this' or 'you may do this without asking' except by editing TOML that the scheduler
reverts at restart; every proactive message picks its channel by majority vote of past deliveries
and falls back to a literal 'telegram'; and a draft prepared for a situation lives on after the
situation closes. The manifesto's proactivity clause is 'unasked but never unwelcome'. The payoff is
that the fleet can prepare more, the owner can steer it with a durable word, and each thing arrives
where and how the owner asked.

**Outcome.** A producer contract with a generic public.resolve_prepared_action_status SECURITY
DEFINER resolver and five refusal rules, with Finance, Health and Home producers.
public.proactivity_directives (snooze / forbid / grant with ceiling) consulted by producers and the
autonomy ladder; arg_sensitivities so the ladder fingerprints the tool not the message.
public.delivery_posture (channel / hold / dashboard) resolved per delivery with posture_rule_id on
the attention ledger. scheduled_tasks.owner_override JSONB that sync respects, and propose_cadence.
Cases can link prepared actions and closing withdraws them.

**Slices.** S1 [M] contract + generic resolver + refusal rules + three producers. S2 [S]
proactivity_directives + verbs + arg_sensitivities. S3 [S] owner_override + schedule_toggle
registration (with move 5). S4 [S] delivery_posture + attention ledger column. S5 [S] LINK_KINDS
prepared_action + withdraw on close.

Source: eco: proactivity (M0, M1, M2, M3, M5; findings 2-7); qc: run12-features (F4)

### 14. Relationship: a turn-state ledger, circles in people not rows, and an occasion engine (M, feature)

Dunbar tiers are denominated in contact rows, so a person with three identities counts three times
and an email-only friend sits at tier 1500 because Gmail never ingests SENT; 'unmeasurable'
conflates not-tracked with cannot-see; the reach-out draft is a constant string. With move 1
registering the missing 40 tools, the relationship butler finally has hands; this move gives it a
memory of whose turn it is, a circle model over people, and an occasion engine that composes from
real history. The manifesto promises a butler that keeps the owner's people warm without nagging.
The payoff is a weekly 'people' view the owner believes.

**Outcome.** relationship.turn_state(entity_id, last_inbound, last_outbound, whose_turn, basis)
maintained from ingested inbound and SENT outbound (Gmail SENT label added to the connector).
Circles computed over public.entities with identity merge, with 'not tracked' distinguished from 'no
signal'. An occasion engine that drafts from the entity's chronicle and last exchanges through the
prepared-action contract (move 13), never a constant string.

**Slices.** S1 [S] Gmail SENT ingestion + turn_state ledger. S2 [S] circles over people + label
honesty. S3 [M] occasion engine via prepared-action contract.

Source: rot: relationship-social (M0, M2, M3; findings 1-5)

### 15. Autonomy tab, gate-honest tool surface, and detail pages that have verbs (L, feature)

Widening a butler's autonomy is two unlinked control planes (five capabilities on the Permissions
page; tool exposure and core groups on the runtime config card without a reason gate); the tool
surface the LLM sees does not say which tools will stop for approval, so a butler plans an action it
cannot take; ten of twelve detail routes register zero page verbs and usePageActions is used on 8
pages while 21 are half-registered. The manifesto's governance clause is that the owner steers each
butler from one place with a reason recorded. The payoff is one Autonomy tab per butler where
permissions, exposure, model tier and ceiling live under one reason gate, a tool listing that tells
the model 'this will ask', and every detail page carrying its verbs in the shell registry.

**Outcome.** Butler console gains an Autonomy tab composing permissions, tool exposure, core groups
(read-only, from move 1), model tier and spend ceiling (from move 9) under one reason gate writing
privileged audit rows. Tool descriptions carry a gate annotation and a preflight_effect tool reports
what an invocation would do and whether it will stop. usePageActions is enforced by lint on every
routed page and the ten verbless detail pages register their verbs.

**Slices.** S1 [M] Autonomy tab + reason gate + audit. S2 [S] gate annotation + preflight_effect. S3
[S] usePageActions lint + ten detail pages' verbs.

Source: surface: config-governance (M1; finding 2); eco: inference (M1); cross:
shell-discoverability (M3; findings 5-6)

## Dropped (deduped or cut, so nothing silently vanishes)

- Real chat token streaming from claude_code.py invoke() (qc: run10-chat M0): honest non-delivery
  documented; SDK blocks on communicate(), cost not justified this run.
- Typed answer contracts / owner-hold register / declared owner state and capacity / owner
  disposition verbs / fleet ref grammar (eco: cross-butler M2, M3, M5): after move 1, revisit once
  routing is honest.
- Situation archetype slices S3-S6 (answerable-question registry, archetype playbooks): after move 8
  has cases.
- Tier-fit calibration and session effects manifest (eco: inference M2, M3): after move 2's receipt
  exists.
- Last-copy ledger, sealed envelope, incident ledger, dependency map, sovereignty page (rot:
  sovereignty-resilience M1-M4, M6): after move 3 proves recovery.
- Education answer door in chat, concepts as entities, learning intents (rot: education-mastery M1,
  M3, M5): after move 4 makes mastery honest.
- Attendance ledger, travel feasibility, routine constraints, series ledger (rot: time-calendar M1,
  M3, M4, M6).
- Rule practice ledger and context pack (rot: relationship-social M4, M5); disclosure boundary and
  introductions (M1).
- Life-graph doors: PRs #4052/#4057/#4058 remain open from run 12; merge them rather than re-file.
- Spend governance slices 4-6: dispatch SLOs, re-dispatch escalation, consequence-gated verification
  (deep: spend-governance M3, M4, M5).
- Connector per-source cost ledger, photo-library connector, external-action contract, honest
  retirement (eco: connectors M2, M4, M5, M6); retirement leaving live credentials is filed as a
  follow-up bug note in the epic.
- Weekly unasked receipt (eco: proactivity M4).
- Fleet narration announcer and useListTriage focus/announce ownership (cross: accessibility M3,
  M4).
- Tip tap path with hover-only fact baseline, route titles in the capability registry (cross:
  mobile-otg M3, M4).
- Capability index API with manifesto parser; '?' sheet shadow marking (cross: shell-discoverability
  M4, M6).
- Entity dossier route and coverage panel; entity_graph_walk caller (qc: run11-substrate-landed M1,
  M2).
- Stuck-decision escalation on push_failed (surface: activity-spine M1); credential-expiry
  correlation; per-butler spend_ceiling column; min_context_tokens.
- Health surface and life-graph surface: no new findings this run; run-12 items stand.

## Filing

Beads epic (gated, fleet NOT triggered): see the epic and `[HOLD]` gate ids in the final session
report and the run-13 `bd remember` reference memory. Every child carries the packet above as its
design and acceptance sections. Closing the gate bead releases the children to the autonomous fleet
— that release is the owner's move. The run-12 gate bu-e30ar was closed on 2026-09-05 and its epic
bu-2jtfw is executing; the four QC cohorts above are the evidence on what that release and the two
before it actually produced.
