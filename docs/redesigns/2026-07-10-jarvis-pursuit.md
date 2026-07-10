# JARVIS Pursuit — 2026-07-10

Third run of the relentless JARVIS pursuit (skill: `.claude/skills/butler-relentless-jarvis-pursuit/`),
six days after the 2026-07-04 pursuit epic `bu-qvnce` merged all 15 moves. Per owner direction this run
broadened to a **three-bar audit**: the th-design bar (UX), the th-engineering quality bar (code health,
tests, boundaries, cruft, docs), and the th-projects mandates (knowledge architecture, spec–code
reconciliation, direction). 35 subagents (workflow `wf_2c5a288c-e09`): **4 QC agents grading the shipped
07-04 moves** (landed-as-designed / landed-with-gaps / regressed, per the ui-maturity-audit taxonomy)
+ 15 page surfaces + 4 cross-cutting design sweeps + 5 engineering lenses + 3 governance lenses +
3 ecosystem lenses + synthesis. Deduped against both prior dossiers, all open beads, and ~100 merged
PRs since 07-04. Backward compatibility waived.

**Full per-agent dossiers** (verdicts, JARVIS gaps, ideal designs, findings with file:line evidence,
QC landing grades, ecosystem proposals) live in
[`2026-07-10-jarvis-pursuit-data.json`](2026-07-10-jarvis-pursuit-data.json) — query one agent with
`jq '.audits[] | select(.page=="<key>")' <file>` (QC agents use keys `qc:*` and carry a `landings`
array; engineering lenses `eng:*`, governance `proj:*`, ecosystem `eco:*`, sweeps `cross:*`).

## Headline

The 07-04 epic largely landed — five of its moves landed-as-designed and the QC squad source- or live-confirmed the primitives (DegradedSources, DispatchVerdict, one board verdict, one SessionDossier, URL doors, the event-cache registry) as real and multiply adopted — but its signature honesty work stopped one layer short of the pixels: three backend degraded flags ship with zero frontend readers, and the surfaces the sweep never reached (sessions, issues, calendar, ingestion connectors) still render outages as calm. The sharpest new findings are live production failures the console cannot see: the daily insight cycle is crashing on a cooldown pkey collision while its own ledger narrates the outage as quiet-hours discipline, the secrets lifecycle push has never once delivered, main's check job has been red since 07-06 on a calendar time-bomb test, and seven merged migrations sit undeployed leaving the attention ledger dark. The eleven new engineering/governance/ecosystem lenses move the frontier from pixels to plumbing — boundaries, reliability, and proactivity-quality all grade weak: upward core→api imports hide a cross-process event bus that structurally drops every daemon-originated event, and the engagement signal meant to drive the vision's disengagement ratchet is poisoned by connector noise. The ranked list accordingly mixes verticals as the owner asked: five UX honesty batches, four engineering spine fixes, three governance loops (decision desk, prompt unification, archive cadence), and three ecosystem arteries.

## QC verdict on the 07-04 epic

Per the QC squad the epic is substantially real: qvnce.3 (memory lifecycle wiring), .4 (dashboard coherence on one board verdict), .5 (one SessionDossier), .11 (shortcut/palette/list-triage spine, broadly adopted), and .13 (URL-backed filters + predicate doors) landed-as-designed — several live-confirmed — and the seven-fabrication hotfix batch (.2) shipped six of seven items clean. Eight moves landed-with-gaps in one consistent shape: mechanism real, last mile missing. qvnce.1's tracker threads all seven routers yet memory pools_failed, spend daily/top-sessions/breakdown, and approvals sources_degraded have zero frontend readers; .2's isDown fix never reached IngestionTimelinePage, the badge's original home; .7 left a switchboard/lifestyle hue collision and an unfinished H1 flip; .9/.10's extraction origins (SystemVerdictBanner, ingestion EventDrawer) still run parallel hand-rolled copies while FloatingChatWidget ships role="dialog" with none of the choreography and the "axe sweep" added zero new coverage (31 of ~33 routes grandfathered); .14 orphaned three consumer-less backend WS routes and left notifications half-wired on the dual-channel liveness contract. The worst landings are ecosystem: .8's attention ledger is live but the insight delivery cycle is crashing in production (broker.py:483 pkey collision, live error 2026-07-10), the secrets lifecycle push has never delivered (deliver.py:455 bare butler_registry; 120 suppressed / 0 delivered rows) while benign-branch-only ledger writers misattribute the outage, and home's four crons bypass the boundary via raw Telegram POSTs; .12's API-direct lane sits disabled in the live catalog with no provenance; .15's dashboard-search consumer never landed; and bu-gxmfx's delegation ledger is zero-row machinery with no producers or readers. Nothing outright regressed, but the run's dominant lesson is that honesty flags and shared primitives can ship backend-only and still count as done.

## North star

Unchanged from the 07-03 dossier (§North star) for the console: five-second fleet verification, earned
calm (nothing fabricated, failure never impersonates health), every clause a door on an unbroken trace
spine, keyboard-first, one instrument built by one hand. This run extends the bar to the codebase and
the project itself: the th-engineering bar (readable hot paths, rigorous honest tests, one-way
boundaries, zero cruft) and the th-projects mandates (specs as planning source of truth, every lifecycle
closed: merged → archived → deployed → decided).

## Tier board (movement vs 2026-07-04 baseline; engineering/governance/ecosystem lenses are new)

- **dashboard** — solid — unchanged — Composed triage cockpit, but the briefing headline runs a third private attention/liveness model and degraded-200s (board flags, spend, notifications, QA breaker) render as calm zeros.
- **butlers-roster** — solid — unchanged — One consolidated board round-trip, but the freshness caption claims 30s against a 5-min reconcile and restore/pause never invalidates the board cell.
- **butler-detail** — solid — unchanged — No dedicated auditor this run; roster audit flags Overview fabrications (dead status-dot vocabulary, $0.00 on failed spend source, flat zero stripe over 'data unavailable').
- **sessions** — functional — unchanged — Flight-recorder shell is strong, but all four cross-butler reads use silent fan_out (partial outage → 'No sessions failed' / 404s real sessions) and the drawer breaks on pinned/deep-linked rows.
- **timeline** — solid — unchanged — Honest degraded banner and ?event= doors shipped, but notification clauses are spine-dead, saved views cross-contaminate with ingestion, and the chronicle has no keyboard triage.
- **notifications** — functional — unchanged — URL filters and verdict opener landed, but source_available is read only by the opener — a Switchboard outage still renders green 0.0% tiles and an empty feed.
- **issues** — functional — unchanged — The failure surface itself violates the degraded convention (DB exception → calm 'No issues recorded') and unreachable-butler acks un-ack themselves within seconds.
- **audit-log** — functional — unchanged — Outcome trio and occurrences shipped, but request_id is a dead-end, pagination survives pivot links into lying empties, and the ledger is mouse-only with exact-match search.
- **approvals** — solid — unchanged — Pending loop is strong; decided dossiers omit the whole decision record (decided_by/at, deny reason, execution_result) and meta.sources_degraded has zero readers.
- **calendar** — solid — unchanged — Feature-rich but every secondary source fails-to-benign ('Tomorrow is clear' on fetch error), source_freshness is never rendered on the canvas, and precision reschedule is dead code.
- **health** — functional — unchanged — Insight doors switch on a category vocabulary the jobs never emit (all land on /measurements), adherence fabricates red for new/PRN meds, and BP trend is structurally empty.
- **spend** — functional — unchanged — Best-composed analytics page, but the displayed MTD is not the enforced MTD, the forecast has no degraded envelope, and a fleet-halting ceiling breach renders as nothing.
- **memory** — functional — regressed — Fading is a dead data contract (sweep writes metadata.status, everything reads validity — structurally 0 forever), forgotten rules count as live beliefs, and pools_failed has zero consumers.
- **entities-plex** — functional — regressed — Plex canvas near the bar, but a halo outage renders an identical calm sky and the cluster verdict is dragged down by detail-page trust defects.
- **entity-detail** — functional — unchanged — Dead contact-era section permanently shows 'No linked contact' + un-completable owner setup; seven sub-blocks render outages as empty (birthdays silently vanish); archive has no way back.
- **settings** — solid — improved — Console honesty (null→'—' HeaderCounts, per-subsystem degradation) QC-confirmed; remaining gaps are the rolling-30d 'MTD' mislabel and total blindness to credential health.
- **secrets** — solid — unchanged — Strongest credential surface in the fleet; PageSystem band still paints expired/never-probed keys green, headline excludes the system family, and 'last used' is a dead axis.
- **education-chronicles** — functional — unchanged — Chronicles near-bar with three honesty holes (silent source-health swallow, vanishing badge strip, 500-event trail truncation); education is a pre-Dispatch Card page whose error state impersonates 'No curriculums yet' and whose teaching-flow machinery is invisible.
- **qa-system** — functional — unchanged — New critical: circuit-breaker reset fabricates a clean patrol + fake case that repaint the system healthy at its worst moment; case dossiers have no session doors; /system verdict is blind to the QA staffer.
- **ingestion** — solid — unchanged — Rich and mostly honest, except the 'failed' status (routing failures) is invisible across chips/ledger/errors-view/histogram and two surfaces assert a retry policy that exists nowhere in code.
- **circles** — functional — unchanged — No dedicated audit; entities audit flags silent 500-row truncation with page-local search.
- **cross:shell-discoverability** — solid — improved — Registries/palette/chords/help-sheet are world-class in design; 10 of 13 verb scopes still lack bindings, ingestion sub-routes re-orphaned, empty-query Actions starved by 13 Trigger commands.
- **cross:visual-language** — solid — improved — Dispatch base layer genuinely flipped; app remains bilingual — solid badge fills in 13 files, ~165 wrong-value var() fallbacks, two type scales, focus contract implemented at 2 of ~104 sites.
- **cross:interaction-speed** — solid — unchanged — All primitives built, each stopped at a beachhead: poll-policy lint covers 9 of ~30 files, prefetch maps 3 routes, no query adapts to bus health, memory registers flash false empties.
- **cross:accessibility** — functional — regressed — Verdict drop reflects deeper scrutiny: excellent infra (choreography, announcer, axe registry, contrast test) adopted in ~a third of the product — tr role=button tables, inaudible j/k triage, alert storms, frozen skip-manifest.
- **eng:readability** — functional — new — Hot paths are the least decomposed: spawner._run 1,408L, pipeline.process 1,395L, calendar.py 10,202L, types.ts/client.ts are the repo's top-two churn conflict magnets.
- **eng:test-rigor** — solid — new — 8,414 mostly-real tests with strong structural guards, but main red since 07-06 on a wall-clock time-bomb, 1,469 unit tests force-marked integration, and the FE/BE contract has zero drift check.
- **eng:boundaries** — weak — new — ~25 upward core→api imports hide cycles, the fleet event bus structurally cannot cross the container boundary, framework imports roster via sys.modules injection, and un-RFC'd cross-schema writes exist.
- **eng:cruft** — functional — new — Real cruft governance but no enforcement: ~3,000+ lines of orphaned FE code, duplicated passport atoms with tests pinning dead copies, dead WS routes, and shipped changes never archived.
- **eng:docs** — functional — new — Strong skeleton, broken high-traffic paths: quickstart starts a deleted postgres service, deployment map is pre-April, and butler CLAUDE.md/AGENTS.md prompt bodies silently diverged so runtime behavior forks by CLI.
- **proj:shape** — solid — new — All five pillars exist and are strong; missing metabolism — AGENTS.md is a ~150-contract shadow spec layer, Source References is dead law (149/176 violate), no staleness machinery anywhere.
- **proj:reconcile** — solid — new — Per-change spec deltas are largely practiced, but the archive/sync last step lags, leaving core-notify/ingestion-policy/memory-discovery-catalog main specs materially false; classification fast lane and ApiAdapter shipped spec-less.
- **proj:direction** — solid — new — Vision markers SC-6/SC-8 are unmeasurable, v1-status is 12 days stale across the fastest fortnight ever, and 14 owner-decision beads have no cadence — decision latency now blocks a P1 fix and a 7-migration deploy.
- **eco:reliability** — weak — new — Prod runs the dev hotreload profile with no deploy pipeline, every infra health signal is pull-only (connectors dead 7+ weeks unnoticed), backups hardcode status=success, reboot drops the egress firewall.
- **eco:proactivity-quality** — weak — new — Attention plumbing is real but the engagement signal is poisoned by non-owner ingress (ratchet can never fire), the ledger is write-only, and notify()-path egress has zero feedback.
- **eco:data-quality** — functional — new — memory_catalog is effectively insert-only (retracted/expired facts circulate fleet-wide forever), provenance severs by design in 7 days, confidence is hardcoded 1.0, no cross-butler contradiction sweep.

## Systemic themes

### Honesty flags ship backend-only — emitted degraded signals with zero pixels listening

The 07-04 epic's core contract — a failed source must never render as calm — was fulfilled by the backend and then abandoned at the wire on multiple surfaces. The DegradedSources tracker threads seven routers, but memory's pools_failed has zero frontend consumers (the /memory 'is remembering working' verdict renders confident totals over half-failed fan-outs), spend's daily/top-sessions/breakdown flags are dropped on the floor, approvals' sources_degraded is unread so 'No approvals waiting.' can render over a downed pool, notifications' source_available is consumed only by the verdict opener while the KPI tiles directly below show a green 0.0% during a total delivery-plane outage, and the timeline isDown fix stopped one page short of the badge's original home. A flag the backend emits with no reader is a new fabrication shape: honesty that counts as done without being seen.

*Exemplars:* src/butlers/api/routers/memory.py:308 (pools_failed, grep frontend → 0 hits); src/butlers/api/routers/spend.py:813,934,1163 vs frontend/src/pages/SpendPage.tsx:84-92; frontend/src/components/notifications/notification-stats-bar.tsx:24-27,113-123

*Affected:* memory, spend, approvals, notifications, dashboard, ingestion (IngestionTimelinePage isDown), qa (PatrolPulseStrip), settings

### Error-as-empty second wave — surfaces never swept under the degraded convention render outages as calm

Beyond unread flags, whole endpoint families never received the convention at all. All four sessions cross-butler reads use the silent fan_out that swallows pool failures ('No sessions failed in the last 24h' during an incident; pool-down 404s real sessions), /api/issues — the surface whose product is failure — returns [] on any exception and renders the all-clear empty state, and dozens of frontend queries never read isError: calendar's day briefing prints 'Tomorrow is clear' on a failed fetch, the connectors roster asserts 'No connectors registered' + all-zero KPI verdict on outage, health/entities/education blocks hide identically for empty and down. The fix is structural (one honest fan-out primitive + contract tests), not another artisanal sweep.

*Exemplars:* src/butlers/api/read_models/sessions_v1.py:373,443,514,545 + src/butlers/api/db.py:242-247; src/butlers/api/routers/issues.py:104-126,329; frontend/src/components/calendar/DayBriefingCard.tsx:147-151

*Affected:* sessions, issues, calendar, ingestion-connectors, health, entities, education-chronicles, memory registers, global search

### Primitives built once, adoption frozen at the beachhead — and no ratchet forces the burn-down

Every shared mechanism from the epics is real and excellent, and every one stalled: useModalChoreography has 4 adopters while FloatingChatWidget ships role=dialog with none of it (and its presence suspends all page shortcuts app-wide); the axe route sweep grandfathered 30 of 32 routes into a skip-manifest that has never lost an entry; poll-policy lint covers 9 files against ~97 raw refetchInterval sites; the prefetch registry maps 3 routes while its handlers are attached fleet-wide; useListTriage skips the board, timeline, audit, ingestion, and memory — the densest triage lists; useOptimisticMutation lives in 4 of ~90 mutation files; extraction origins (SystemVerdictBanner, ingestion EventDrawer) still run parallel hand-rolled copies of the contracts extracted from them. The taxonomy's new shape: a gate ships with ~100% of the surface exempted and the gate's existence is then cited as coverage. Every future primitive needs a born-with burn-down bead or a ratchet test.

*Exemplars:* frontend/src/components/chat/FloatingChatWidget.tsx:351 (role=dialog, no Escape/focus/restore); frontend/src/test/axe/skip-manifest.ts (30 entries, single commit ever); frontend/eslint.config.js:221-243 (POLL_POLICY_FILES = 9 files)

*Affected:* cross:accessibility, cross:interaction-speed, cross:shell-discoverability, timeline, ingestion, memory, sessions, calendar, education, qa, secrets

### The architecture's one-way arrows point both ways — upward imports, a bus that cannot cross the process boundary, un-RFC'd cross-schema writes

Doctrine states the boundaries clearly; the import graph violates them systemically. ~25 core/modules/jobs→butlers.api imports (each self-documented as circular-dependency avoidance) home the audit spine's canonical writer, pricing, and the conversation store in the interface layer. Worse, daemon-side emit_* calls publish session/spend/notification/approval events into in-process queues whose only subscribers live in the separate dashboard-api container — every daemon-originated live event is silently discarded while the Live indicator shows connected, masked only by polling. Framework code imports roster butlers via an import-time sys.modules hack with swallowed failures; the memory module UPDATEs/DELETEs chronicler's schema with no RFC; domain butlers bypass RFC 0011's 'unspoofable origin' MCP entry point by importing the switchboard broker in-process.

*Exemplars:* src/butlers/core/sessions.py:330 + docker-compose.yml:205-214 vs 548-551 (events die at the container boundary); src/butlers/core/audit.py:66-68 (audit writer homed in a FastAPI router); src/butlers/modules/memory/tools/entities.py:1112-1173 (cross-schema writes into chronicler)

*Affected:* eng:boundaries, timeline/live surfaces, spend ticker, approvals stream, notifications, RFC 0010/0011 integrity, eng:test-rigor (cycle-hiding lazy imports)

### One fact, many numbers — displayed values computed from different sources than the enforced or adjacent ones

The number the owner sees is repeatedly not the number the system acts on. /spend's MTD is priced from per-butler sessions fan-out while the spawn-deny gate prices public.token_usage_ledger (the dashboard can show 92% while spawns are already denied); the settings console reports rolling-30d under a 'Spend MTD' label and fires false ceiling alarms; sessions' cost column and dossier Token Usage silently exclude prompt-cache tokens and disagree with the Spend page to the cent; the board cell and the detail Overview show two different 'sessions 24h' computed by different SQL; ingestion's 'events · today' means trailing-24h on the roster and calendar-day heartbeat deltas on detail; per-rule 'Saved 7d' credits fleet-wide model usage to individual rules. Each mismatch trains the owner to distrust both copies.

*Exemplars:* src/butlers/api/routers/spend.py:1251-1283 vs src/butlers/core/model_routing.py:1078-1141; src/butlers/api/routers/settings_console.py:184-188 ('30d' labeled MTD); src/butlers/api/read_models/sessions_v1.py:63-74 (cache columns omitted from cost)

*Affected:* spend, settings, sessions, dashboard KPIs, butlers-roster, ingestion, qc:truth

### Governance stalls at the last step — merged ≠ archived ≠ deployed ≠ decided

Every lifecycle in the project runs hot until its close-out step, then stops. Merged code is not deployed: core_155..161 sit dark in prod (attention ledger, owner-outbound events, discretion classification) behind an artisanal deploy with no drift surface, while the migrations one-shot exits 0 against a stale image. Shipped OpenSpec changes are not archived: switchboard-rule-promotion shows 0/25 tasks with 5/7 beads merged, leaving core-notify and ingestion-policy main specs materially false. Normative contracts accrete in AGENTS.md (~150 '<X> contract' notes) instead of specs. And owner decisions have no cadence: 14 decision beads wait invisibly — a P1 silent-message-loss fix has been blocked on a one-line choice since 07-05, and the deploy itself is one of the ungated decisions. The fix is procedural hooks (epic-close archives, deploy ledger, decision desk), not more sweeps.

*Exemplars:* bd bu-zhfd0 (7-revision gap, migrations exit 0 stale); openspec/changes/switchboard-rule-promotion/tasks.md (0/25 vs merged PRs #2992/#2999/#3015) + openspec/specs/core-notify/spec.md:17 vs src/butlers/core_tools/_notifications.py:227-238; AGENTS.md:816-872 (~150 contract notes)

*Affected:* proj:shape, proj:reconcile, proj:direction, eco:reliability, core-notify/ingestion-policy/memory-discovery-catalog specs, eng:docs

### Cruft regrows faster than manual sweeps — dead code, dead endpoints, and duplicated utilities with no automated gate

The closed cruft epic's debt fully regrew within weeks because only manual passes exist. ~3,000+ lines of orphaned frontend production code (retired relationship UI trio, pre-One-Spend costs pair, six dead hooks with full API verticals), seven duplicated passport atom files whose tests pin non-shipping copies (green tests over dead code), nine orphaned approvals hooks, three consumer-less backend WS routes kept 'for other clients' that don't exist, 13 private formatDuration and 4 formatCost clones despite lib/format-cost.ts documenting the exact bug the duplication reintroduces, and live deprecation shims with zero remaining producers. A knip-style import-graph CI gate plus delete-on-migration discipline converts this from a recurring epic into a build failure.

*Exemplars:* frontend/src/components/secrets/passport/atoms.tsx:877 vs KV.tsx:39 (+7 orphan files with live tests); src/butlers/api/routers/approvals.py:2363-2375 (WS route, zero consumers); frontend/src/hooks/use-approvals.ts:56-185 (nine orphaned hooks)

*Affected:* eng:cruft, approvals, secrets, spend, settings, timeline, frontend hooks/api layer, backend routers

## Ranked moves

### 1. Unbreak and mirror the proactivity spine (ecosystem, M)

**What:** Fix the crashing daily insight cycle (ON CONFLICT on insight_cooldowns + crash-proof post-delivery bookkeeping), schema-qualify deliver()'s butler_registry lookup so the secrets lifecycle push can deliver for the first time, add attention-ledger writes to every terminal branch (no-recipient, exception), route home's four raw-Telegram crons through the notify boundary and delete the raw-POST helper, then give the ledger its first reader — GET /api/attention/ledger + a Trust Console panel that flags suppressed-only sources — and gate the 60-minute engagement proxy on owner-authored ingress with durable rollups.

**Why:** vision.md earned calm — 'failure never impersonates health': the fleet's one daily proactive arbitration job is crashing in production while benign-branch-only ledger writers narrate the outage as quiet-hours discipline; an expired credential has been un-notifiable since 07-05; and the disengagement ratchet (a vision success marker) reads connector noise as owner engagement, so it can never fire.

**Evidence:** roster/switchboard/tools/insight/broker.py:483-487 (plain INSERT, live pkey error 2026-07-10); roster/switchboard/tools/notification/deliver.py:455 (bare butler_registry; ledger shows 120 suppressed / 0 delivered); src/butlers/jobs/home.py:1222-1264; src/butlers/modules/pipeline.py:1837-1851; grep attention_ledger src/butlers/api → 0 readers

**Slices:** 1) ON CONFLICT + transactional bookkeeping + redeliver-across-expired-cooldown regression test; 2) schema-qualify deliver + ledger rows on except/no-recipient branches + public-only search_path integration test; 3) home jobs through the notify boundary; 4) ledger reader endpoint + Trust Console panel (suppressed-but-never-delivered sources flagged loudly); 5) owner-identity gate on engagement + attention_daily_rollup table surviving the 30-day purge.

### 2. Consume the orphaned honesty flags — the batch the epic left one layer short (ux, S)

**What:** Wire every emitted-but-unread degraded flag into pixels: memory pools_failed → SourceDegradedNote under MemoryOverture; spend daily/top-sessions/breakdown unavailable_butlers → chart footnotes and gated empty states; approvals meta.sources_degraded → verdict-opener clause + queue note (re-scoping the two-thirds-stale bu-enep6 to this remainder); notifications source_available → em-dash stats tiles and a degraded feed state instead of 'No notifications found'; isDown on IngestionTimelinePage's LiveStatusBadge; a named degraded line for QA's PatrolPulseStrip instead of vanishing on error.

**Why:** CLAUDE.md fleet degraded-envelope convention + vision earned calm: this is the epic's exact target defect surviving inside its own fix — the backend now tells the truth on all these endpoints and no pixel listens, so partial outages still render as calm on the pages' opening verdicts.

**Evidence:** src/butlers/api/routers/memory.py:308 + grep pools_failed frontend → 0 hits; src/butlers/api/routers/spend.py:813,934,1163 vs frontend/src/pages/SpendPage.tsx:84-92; src/butlers/api/routers/approvals.py:1561,1624 + approvals-verdict-opener.tsx:94-95; notification-stats-bar.tsx:24-27,113-123; IngestionTimelinePage.tsx:70; QaOverviewPage.tsx:338

**Slices:** One S slice per surface, each with a vitest asserting degraded-200 renders a named note, never bare zeros or all-clear; land memory and notifications first (opening-verdict surfaces).

### 3. One honest fan-out primitive + flag-consumption coverage tests (engineering, M)

**What:** Delete silent DatabaseManager.fan_out (fan_out_with_status becomes the only API); thread degraded meta through sessions (list/aggregate + 404-vs-pool-down split on detail), issues (audit-groups and acks sources), and global search; fold the session drawer onto the global detail endpoint (fixing the pinned-row/deep-link break and deleting the butler-scoped path); add tests/contracts/test_degraded_envelope.py plus a frontend flag-consumption registry test so an emitted degraded field with zero readers fails CI.

**Why:** vision 'failure never impersonates health' made structural: the convention's own reference primitive swallows failures by default, and the two surfaces whose whole job is failure (sessions, issues) fabricate all-clear during partial DB outages — the coverage tests prevent this run's dominant new-defect class from recurring.

**Evidence:** src/butlers/api/db.py:222-247 vs :249-268 (fan_out_with_status unused by sessions); sessions_v1.py:373,443,514,545; src/butlers/api/routers/sessions.py:495-496 (pool-down → 404 'Session not found'); issues.py:104-149,329; search_v1.py:387-391 (return {} on bare Exception); SessionsPage.tsx:288-289 (drawer butler resolution)

**Slices:** 1) primitive rename + caller migration; 2) sessions meta + verdict/KPI gating + 404/503 split + drawer consolidation; 3) issues DegradedSources + gated empty state; 4) search flag + consumer note; 5) contract test + FE flag registry seeded with move-2's flags as burn-down entries.

### 4. Un-red main: time-bomb sweep + marker taxonomy repair (engineering, S)

**What:** Fix the wall-clock time-bomb entity-activity binning tests with relative fixtures (or clock injection), sweep the top frozen-date fixture files for the same shape, add a nightly faketime (+45d/+120d) CI leg, and make roster/conftest respect explicit unit markers so 1,469 mocked tests leave the Docker job and run in the unit lane.

**Why:** Earned calm applies to the meta-loop: a red check on main unnoticed since 07-06 trains the fleet to ignore the gate, and a marker taxonomy that silently overrides authors' declared intent makes green runs unattestable (th-engineering test-rigor bar: deterministic or quarantined).

**Evidence:** roster/relationship/tests/test_entities_api.py:58,1990-2009 (frozen _NOW) vs roster/relationship/api/router.py:6302 (wall-clock window); gh runs 28888877607/28762155318 red at the testcontainers step; roster/conftest.py:42-44 (force-marks all roster tests integration); ci.yml:119,223

**Slices:** 1) fix binning tests + verify at simulated +30/+90d; 2) conftest unit-marker respect + pinning meta-test; 3) frozen-date sweep (chronicler aggregations/editorial, finance pattern-recognition, briefing); 4) nightly faketime matrix leg.

### 5. Surface routing failures on the ingestion ledger + purge fabricated policy prose (ux, S)

**What:** Add the live 'failed' status (routing failures after ingestion) to the FE vocabulary end-to-end — status chips, ledger filter, built-in errors view, RowStatus words, bulk/replay eligibility, and the backend histogram statuses — and rewrite the invented '3 retries with exponential backoff' prose in gate-state and the EventDrawer to the actual single-attempt + manual-replay truth with code citations.

**Why:** vision 'failure never impersonates health' + 'nothing fabricated' on the house's sensory ledger: the single most important thing this surface exists to show — a dispatch that failed after ingestion — cannot be seen, filtered, or replayed from the UI, while two surfaces assert a retry policy that exists nowhere in code.

**Evidence:** src/butlers/core/ingestion_events.py:788-797 (mark_failed live write path), :190-197,702-709 (histogram drops 'failed'); frontend/src/components/ingestion/TimelineTab.tsx:177,221-229,2036; StatusBadge.tsx:141-166; gate-state.ts:77-79 + EventDrawer.tsx:527-529 vs src/butlers/modules/pipeline.py:1982 (attempt hardcoded 1)

**Slices:** 1) status vocabulary + filters + replay affordance, with a repo-wide test-fixture grep first (known cross-file fixture hazard); 2) _HISTOGRAM_STATUSES fix; 3) policy prose rewrite citing the real mechanism.

### 6. QA breaker truth: first-class reset state, no forged history, session doors on the dossier (ux, M)

**What:** Replace the circuit-breaker reset's synthetic clean-patrol + fake-case INSERTs with a dedicated breaker-reset record consulted by dispatch admission; backfill-delete existing forged rows; derive closed/tripped/unknown honestly in the toolbar and butler-detail chip (unknown on loading/error); show the five failing attempts at confirm time; and add healing_session_id/session_ids doors to the QA case dossier so the trace spine reaches the investigation session.

**Why:** Earned calm at the worst moment: today's reset repaints the system healthy exactly when it has failed five consecutive times — fabricated history on the surface whose identity is honesty about failure — and the flagship trace-spine surface dead-ends one hop short of its own evidence.

**Evidence:** src/butlers/api/routers/qa.py:3161-3195 (synthetic INSERTs), :1284-1292,1530-1535 (summary/staffer_status consume the forgery); QaOverviewPage.tsx:496 ('closed' on summary error); qa.py:1974-2022 + frontend/src/api/types.ts:5236-5246 (dossier omits session fields)

**Slices:** 1) breaker-reset table + admission check + delete synthetic path + backfill; 2) tri-state toolbar/chip + evidence-bearing confirm dialog + palette verb while tripped; 3) dossier SELECT + type + session-door rendering; 4) force-patrol toast branches on triggered.

### 7. Deploy spine: drift sentinel, deployments ledger, one-command deploy (ecosystem, M)

**What:** Hourly alembic-head vs per-schema DB-revision vs deployed-SHA comparison surfaced as a red clause on /system and escalated via QA when stale >24h; a public.deployments ledger (sha, migration head, result); `butlers deploy` — build with GIT_SHA, run the migrations one-shot, recreate under an explicit prod profile that removes the hotreload/scale-0 depends_on trap, verify /health, record; follow-on slices add the infra-state QA discovery source + external deadman and honest backup verification (status is hardcoded 'success' today).

**Why:** vision line 122 'runs for weeks without intervention': seven merged revisions sit dark in prod (attention ledger, owner-outbound events, discretion classification) behind an artisanal deploy ceremony, and nothing can even know it — merged≠deployed drift is structural at fleet velocity, and every infra health signal is pull-only.

**Evidence:** bd bu-zhfd0 (core_155..161 gap; migrations one-shot exited 0 against a pre-core_155 image); docker-compose.yml:678-713 (prod = hotreload bind-mount), :706-712 (depends_on scale-0 trap); `git tag` → empty (release.yml never fired); src/butlers/api/routers/system.py:447-456 (backup status hardcoded success)

**Slices:** 1) drift sentinel + system-page clause (S, ships alone and immediately closes bu-zhfd0's detection gap); 2) deployments table + GIT_SHA + GET /api/system/deployment; 3) deploy script + prod compose profile; 4) infra-state QA source (connector-offline, backup-stale, heartbeat-stale) + deadman sidecar; 5) BackupFacts honesty + weekly restore drill.

### 8. Owner Decision Desk: decision beads become first-class attention citizens (governance, M)

**What:** A decision-bead convention (structured options + default + deadline), a Decisions lane on the dashboard with a verdict opener ('N decisions waiting, oldest Xd') and keyboard triage, routing through the attention ledger + Telegram one-tap close (reusing bu-24lu6.4/.5 inline-keyboard/callback primitives), a weekly decision-review cron, and age-based escalation when a decision blocks a P1 bug or a deploy. Seed queue: bu-zhfd0 deploy, bu-v4ipc weight-gate, api-haiku lane re-enable, delegation-ledger adopt-or-descope, PRODUCT.md adopt-or-delete.

**Why:** purpose-and-single-pane earned calm — nothing waits silently: 14 owner-attention beads wait invisibly today; a P1 silent-message-loss bug has been decision-blocked since 07-05 and five shipped capabilities sit dark behind an ungated deploy choice. Decision latency is now on the critical path of the vision's own success markers.

**Evidence:** bd list: 14 owner/decision beads (bu-v4ipc, bu-zhfd0, bu-4pq0s, bu-wyftz, bu-4qfhl, bu-i4jbj all open since 07-04/05); bu-wzbu9 blocks:bu-v4ipc; grep OWNER|DECISION|assignee frontend/src/pages/IssuesPage.tsx → 0; grep roster/*/butler.toml decision-review crons → 0

**Slices:** 1) convention + options linter; 2) dashboard Decisions lane + useListTriage; 3) attention-ledger/notify routing + one-tap close once bu-24lu6.4 merges; 4) weekly review cron + P1/deploy escalation.

### 9. Memory lifecycle + fleet-catalog integrity: one vocabulary, atomic disownment (ecosystem, M)

**What:** Unify the lifecycle contract — the decay sweep writes validity='fading' (backfilling metadata.status rows) so the dashboard's entire fading axis (pills, attention condition, dimming, pipeline numeral) comes alive; exclude/label forgotten rules in lists and the Proven-rules KPI; unify MCP readers on the columns. Then make catalog disownment atomic: cascade _mark_catalog_stale from forget_memory, decay expiry, and purge; fix consolidation_executor's missing enable_shared_catalog pass-through; add reverse reconciliation to the backfill job and a catalog-drift gauge to stats.

**Why:** Nothing fabricated: the dashboard titled 'What the house believes' counts beliefs the runtime has verifiably forgotten, fading is structurally zero everywhere (a dead data contract), and the just-enabled fleet catalog serves retracted falsehoods to all nine butlers indefinitely — the data substrate a JARVIS acts on autonomously must propagate disownment to every index that serves it.

**Evidence:** src/butlers/modules/memory/storage.py:2620-2627 (sweep writes metadata.status) vs src/butlers/api/routers/memory.py:251,472 (reads validity; grep validity='fading' writers → 0); storage.py:1622 (_mark_catalog_stale single caller), :1944-2018 (forget no cascade), :2707-2712 (purge orphans catalog rows); consolidation_executor.py:110-124

**Slices:** 1) sweep writes validity + one-time backfill + integration test (sweep → API list returns fading row); 2) forgotten-rule exclusion in list/stats + UI label; 3) catalog cascade wiring from all four lifecycle transitions; 4) reverse reconciliation + drift gauge in /api/memory/stats meta.

### 10. Roster prompt unification: one body per butler, divergence test (governance, S)

**What:** Merge the diverged CLAUDE.md/AGENTS.md personality bodies for general/relationship/messenger/lifestyle/qa — keeping BOTH the relationship scope-filter mandate (AGENTS-only today) and the retract-replace correction workflow (CLAUDE-only), plus messenger's staffer identity and WhatsApp tools — make CLAUDE.md a one-line '@AGENTS.md' include as health/finance/etc. already do, and add a roster consistency test so the fork cannot recur.

**Why:** 'One instrument built by one hand' extends to prompts, and this is a live behavior bug, not a docs nit: runtime behavior currently forks by which LLM CLI spawns — a Claude-runtime relationship session never receives the MANDATORY scope='relationship' filter (the exact cross-scope fact-contamination hazard the fleet already paid for once), and Codex sessions append instead of superseding.

**Evidence:** diff roster/relationship/{CLAUDE,AGENTS}.md → scope-filter section AGENTS-only (lines 50-92), Correcting Facts CLAUDE-only (38-61); roster/messenger diff → staffer identity + whatsapp_send_message AGENTS-only; roster/health/CLAUDE.md = '@AGENTS.md' proves the pattern; src/butlers/core/spawner_context.py:119-125

**Slices:** 1) merge + include per butler (check public.system_prompt_history DB overrides first); 2) unit test asserting every roster CLAUDE.md is the include line; 3) spawned-prompt verification via spawner_context tests.

### 11. One budget truth on /spend: ledger-first MTD + fleet-halt visibility (ux, M)

**What:** Price the forecast MTD from public.token_usage_ledger — exactly the number check_monthly_ceiling enforces — collapsing the three divergent MTD computations (spend fan-out, settings console rolling-30d 'MTD', gate ledger) onto one helper; give ForecastResponse a degraded envelope (ceiling_source_error, unavailable series); and surface enforcement: a red 'Monthly ceiling reached — N dispatches denied since <ts>' state wired to the already-served quota_skip attempts endpoint, with an attempts drawer and an attention-ledger push.

**Why:** Nothing fabricated: the page's primary answer is computed from a different source than the gate that halts the fleet (the dashboard can show 92% while spawns are being denied), and the single most consequential thing the spend system can do — stop the household staff — currently renders as nothing at all.

**Evidence:** src/butlers/api/routers/spend.py:1251-1283 (fan-out MTD) vs src/butlers/core/model_routing.py:1078-1141 (ledger-priced gate); settings_console.py:184-188,475-484 (30d labeled MTD, false alarms); spawner.py:1167-1202 (quota_skip DENY); grep quota_skip frontend → 0 hits despite GET /api/dispatch/attempts (model_settings.py:1555-1582)

**Slices:** 1) ledger-first MTD + degraded fields, delete the forecast fan-out; 2) settings-console reconciliation onto the same helper; 3) denied-today count + red attention row + attempts drawer with session doors; 4) route the halt through the attention ledger for an owner push.

### 12. One attention model on the dashboard: briefing derived from the composed verdict (ux, M)

**What:** Classify the briefing headline from the same composed board/attention model the page renders — replacing the every-sent-notification-counts-as-attention feed and the bespoke liveness SQL CASE — add a partial-visibility degraded class so swallowed state-fetch failures can never compose 'All quiet.', stable-sort attention rows by severity across kinds (offline butler above issue rows at 3am), and add the missing QA circuit-breaker and notifications source_available rows.

**Why:** vision 'The system is boring. It works.' requires the console's opening sentence to be provable: today the headline and the attention list beneath it are computed from disjoint definitions of 'needs you' (headline says busy over 'Nothing waiting.'), and all three briefing state fetches swallow exceptions into the calmest possible verdict with a green pill.

**Evidence:** src/butlers/api/routers/dashboard_briefing.py:290-344 (sent notifications count as attention), :393-400 (bespoke liveness CASE), :346-348,372-374,420-422 (swallowed exceptions → quiet); classify.py:76-96; frontend/src/components/overview/model.ts:204-212 (kind-order), :614-649 (breaker unread) vs QaVerdictOpener.tsx:25-28

**Slices:** 1) briefing state sources rewritten onto the board verdict + failed/approvals/QA inputs, with a degraded state class; 2) test asserting headline class implies attention-row-count bounds from shared fixtures; 3) severity-first ordering + breaker/source_available rows; 4) delete the bespoke SQL CASE.

### 13. Cross-process event transport + dead WS route deletion + bus-aware polls (engineering, M)

**What:** Add a Postgres LISTEN/NOTIFY publisher in core so daemon-originated session/spend/notification/approval events actually reach the dashboard-api bus (today they die in in-process queues in another container); delete the ~25 upward emit imports and the three consumer-less legacy WS routes; add useBusAwarePollInterval so bus-covered queries tighten to a fast fallback when the socket drops (notifications gains its missing reconcile floor) and the pre-bus AutoRefreshToggle/fast floors retire.

**Why:** Earned calm + zero-cruft doctrine: the Live indicator shows connected while every daemon-originated event is silently discarded at the container boundary (failure impersonating liveness, masked by polling), a dead socket means 5-minute-to-infinite staleness with no adaptation, and three orphaned WS routes are maintained for clients that do not exist.

**Evidence:** src/butlers/core/sessions.py:330 + src/butlers/core/spawner.py:2237-2260 (emit into in-process queues) vs docker-compose.yml:205-214/548-551 (separate containers); approvals.py:2363-2375, spend.py:139, settings_console.py:653-702 (zero consumers, grep-proven); frontend/src/hooks/use-notifications.ts (no refetchInterval); event-bus.tsx:60-135 (status consumed only by RootLayout)

**Slices:** 1) NOTIFY publisher + API-side LISTEN bridge + two-pool integration test proving daemon→subscriber delivery; 2) delete daemon→api imports and the three WS routes + tests/spec refs; 3) useBusAwarePollInterval + adoption across the 8 bus-covered hooks + AutoRefreshToggle deletion; 4) coverage-manifest gaps (session-stripe, spend breakdown/rules/forecast keys).

### 14. Close the governance loop: archive sweep, lifecycle clause, status cadence (governance, S)

**What:** Archive/sync the shipped OpenSpec changes (memory-catalog-default-on, the attention-ledger pair, entity-keyed-preferred-channel, switchboard-rule-promotion after task true-up) so core-notify/ingestion-policy/memory-discovery-catalog main specs stop asserting falsehoods; add a coordinator-protocol clause that closing an epic archives its OpenSpec change in the same delivery; add a v1-status refresh rule (epic-close or monthly) and bootstrap about/legends-and-lore/ideas-ledger.md from the pursuit dropped-lists with unpark conditions.

**Why:** th-projects: specs are the planning source of truth, and delta specs of unarchived changes are phantom authorities — today the main core-notify spec says channel is required (it's optional with entity resolution) and defines only 'deferred' quiet-hours behavior (code also drops), while the marker-evidence matrix is 12 days stale across the fastest fortnight in project history.

**Evidence:** openspec/changes tallies: switchboard-rule-promotion 0/25 with PRs #2992/#2999/#3015 merged, memory-catalog-default-on 5/5 unarchived; openspec/specs/core-notify/spec.md:17 vs src/butlers/core_tools/_notifications.py:227-238,653-690; about/heart-and-soul/v1-status.md 'Last updated: 2026-06-28'; ls about/legends-and-lore → README + rfcs only

**Slices:** 1) archive/sync sweep with per-change code verification; 2) coordinator epic-close clause in craft-and-care/beads protocol; 3) v1-status refresh rule + first refresh; 4) ideas-ledger bootstrap.

### 15. Dead-code amnesty + knip CI gate (engineering, M)

**What:** Delete ~3,000+ lines of orphaned frontend production code (retired relationship trio, pre-One-Spend costs pair, six dead hooks plus their client-fn/type/re-export verticals, nine orphaned approvals hooks); reunify the duplicated passport atoms so tests pin the shipping copies (two divergent ProviderMarks render on the same page today); consolidate the 13 formatDuration / 4 formatCost clones onto lib/ with an eslint guard; then add a knip-style import-graph gate to frontend CI so an unimported module is a build failure, not a future sweep.

**Why:** th-engineering zero-cruft bar in a single-owner deployment: the closed cruft epic's debt fully regrew within weeks because only manual sweeps exist; tests that verify non-shipping duplicates are fabricated confidence, and every dead vertical (hooks → client fns → mounted routers) is untested surface pretending to be product.

**Evidence:** frontend/src/components/secrets/passport/atoms.tsx:877 vs KV.tsx:39 (+7 orphan atom files, tests pin dead copies); ContactTable/PendingIdentitiesSection/UnlinkedEntitiesSection → 0 importers; frontend/src/hooks/use-approvals.ts:56-185 (nine orphans); frontend/src/lib/format-cost.ts:5 (header documents the violated consolidation); CostChart.tsx:33 (re-rolls the $0.00 bug)

**Slices:** 1) component/hook/vertical deletions with full build + vitest + e2e-spec grep (stale-fixture hazard); 2) passport atom reunification with behavior diff first; 3) formatter consolidation + eslint guard; 4) knip gate wired beside eslint in the frontend CI job.

## Dropped (dedup ledger — nothing silently vanished)

- Approvals decision record + real expiry contract (approvals moves 1+3) — cut at the 15-move cap; strongest next-cycle UX candidate (decided_by/deny-reason/execution_result dossier, scheduled expiry sweep).
- Health insight-door vocabulary fix + adherence denominator clamp — both S-cost and high-value; cut for slots, queue first next cycle.
- Calendar batch (error-as-benign truth pass, masthead freshness plaque, timezone trio, overlap geometry, now-line, ?entry= deep links, 6.5k-line decomposition) — calendar holds solid; deferred whole.
- Sessions live tool-call tail (L) and cache-true token/cost columns — cost-cut; the cache-token axis mismatch is carried in theme 5.
- bu-enep6 re-scope folded into move 2 — two of its three items already shipped; only the approvals remainder is real.
- qc:truth's flag-consumption registry test merged into move 3's coverage-test slice (credited to qc:truth + qc:dispatch).
- Dispatch polish batch (switchboard hue slot, H1 flip completion, BoardHeader stale comment, Kbd/KbMono merge, mover-dot neutrality) — deferred as polish.
- Accessibility adoption batch (FloatingChatWidget choreography + shared dialog-suspension guard, narrated j/k triage, tr role=button lint fence, axe burn-down ratchet, contrast token-family extension) — cut for slots; carried in theme 3 and the regressed a11y verdict.
- Entities cluster moves (contact-era vestige excision, archive lifecycle with undo, one entity-type vocabulary, one fact spine, Telegram provisioning to /secrets) — deferred; entity-detail stays functional.
- Settings/secrets remainder (console credential-health feed, PageSystem six-state band, last_used persistence, permissions rebuild, passport keyboard triage) — deferred; the settings-console MTD mislabel folded into move 11.
- eng:readability decompositions (spawner._run phase extraction, calendar.py package split, api/types+client per-domain split, CalendarWorkspacePage split, qa.py router package) — L-cost, deferred; conflict-magnet churn noted in themes.
- eng:boundaries RFC reconciliations (insight origin spoofability, cross-schema view mediation, switchboard routing promotion into src/, approvals gate into core) — deferred behind move 13's first arrow-fix; lens graded weak to keep pressure on.
- eng:docs remainder (deployment-truth sweep of the broken quickstart, invisible-butlers coverage vertical, frontend spec authority restore, AGENTS.md verify-or-delete compaction) — deferred except prompt unification (move 10).
- proj:shape moves (AGENTS.md contract amnesty, Source References enforce-or-repeal, generated RFC status index, manifesto amendment protocol, spec catalog, snapshot-artifact eviction) — deferred; carried in theme 6.
- proj:reconcile spec-writing moves (switchboard classification fast-lane spec, runtime-api capability spec, discretion honesty vocabulary deltas, core-notify msg_context amendment) — deferred; bu-17axsl should mint them as children.
- proj:direction verticals 3-5 (intervention ledger/days-autonomous gauge, proactivity funnel telemetry, routing-accuracy live scoreboard) — deferred behind moves 1/7/8 which unblock their substrates.
- eco:reliability remainder (catch-up cron staleness policy, reboot survivability kit, external deadman, backup offsite) — deadman/backup folded as later slices of move 7; rest deferred.
- eco:proactivity remainder (telegram reaction/reply capture, per-origin scoring modulation, weekly one-tap relevance digest, LLM insight-quality audit) — deferred behind move 1's ledger mirror + owner-gated engagement.
- eco:data-quality remainder (durable provenance ledger, confidence calibration, cross-butler contradiction sweep, right-to-forget cascade, staleness SLAs, create-time entity dedup) — substrate roadmap after move 9.
- Issues/audit deep moves (windowed occurrences + partial index, group-predicate door with noise-widening, principal/subsystem schema split, heartbeat-store reachability, ledger substring search) — deferred; the degraded-honesty core lands via move 3.
- Spend rule-effects honesty (per-rule application record, saved_7d fix-or-retire, rule-from-evidence 'Cap this' verbs, truthful per-call-cap copy) — deferred; the saved_7d mislabel is carried in theme 5's family.
- Timeline/notifications remainder (saved-views surface-scoping migration, session/trace ids into timeline_v1 + ?id= row addressing, errors-lens covering failed deliveries, jump-to-time, chronicle list triage) — deferred.
- Keyboard-triage adoption batch (board grid, timeline, audit ledger, ingestion ledger, memory registers, secrets spine, sessions focus-sync) — deferred; carried in theme 3.
- Delegation-ledger first consumer and dashboard memory-catalog search wiring, api-haiku lane re-enable, PRODUCT.md adopt-or-delete — all routed to the Decision Desk seed queue (move 8) as owner decisions rather than ranked moves.
- Interaction-speed remainder (poll-policy vocabulary completion to src/**, mutation classification sweep, prefetch registry growth from route-registry, infinite-query head/committed generalization) — bus-aware intervals kept in move 13; rest deferred.
- Education/chronicles moves (truth-and-lifecycle pass, teaching-flow rail, Dispatch conversion, e2e smoke coverage) — deferred; education remains the last pre-Dispatch page, noted in tier board.
