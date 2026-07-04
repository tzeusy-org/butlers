# JARVIS Pursuit — 2026-07-04

Second run of the relentless JARVIS pursuit (skill: `.claude/skills/butler-relentless-jarvis-pursuit/`),
one day after the 2026-07-03 audit's epic `bu-86c4c` merged 17/19 children. 32 subagents
(workflow `wf_f430e4fb-8ff`): 22 page surfaces + 4 cross-cutting sweeps + **5 new ecosystem
ideation lenses** (connectors, inference flow, knowledge graph, cross-butler interaction,
proactivity) + synthesis. Held to the th-design bar against `about/heart-and-soul/vision.md`
and `docs/frontend/purpose-and-single-pane.md`; deduped against the 07-03 dossier, all 48 open
beads, and in-flight epics. Backward compatibility waived.

**Full per-agent dossiers** (verdicts, JARVIS gaps, ideal designs, all findings with file:line
evidence, ecosystem proposals with integration points) live in
[`2026-07-04-jarvis-pursuit-data.json`](2026-07-04-jarvis-pursuit-data.json) — query one agent with
`jq '.audits[] | select(.page=="<key>")' <file>` (ecosystem lenses use keys `eco:*`, sweeps `cross:*`).

## Headline

The 07-03 epic genuinely landed — timeline, issues, audit-log, spend, approvals, butler-detail, and secrets all climbed a tier and nothing broke — but every mechanism it shipped (judgment layer, command spine, event bus, state contract, keyboard triage) stopped at one or two surfaces, so the console reads as islands of excellence in an unmigrated sea. The deeper truth is one layer down: the frontend truth amnesty never reached the backend, where roughly a dozen aggregation endpoints still zero-fill on failure (butlers board, memory fan-out, notifications, approvals, settings, spend, secrets), letting dead sources render as healthy zeros under the very verdict banners built to prevent it. And the ecosystem's most consequential machinery — memory decay, the cross-butler discovery catalog, the insight broker's anti-spam — is fully built and effectively switched off.

## North star

Unchanged from the 07-03 dossier (see `2026-07-03-jarvis-audit.md` §North star): the operator
console for a sovereign one-person AI household staff — five-second fleet verification, earned
calm (nothing fabricated, failure never impersonates health), every clause a door on an unbroken
trace spine, keyboard-first, one instrument built by one hand. This run extends the pursuit past
the pane of glass into the machinery: perception (connectors), inference economics, the knowledge
graph's lifecycle, cross-butler collaboration, and calibrated proactivity.

## Tier board (movement vs 2026-07-03 baseline)

- **dashboard** — solid — unchanged — landings hold (attention list, KPI error states, event bus), but two liveness models, two dialects, and two decision contracts keep it from world-class
- **butlers-roster** — solid — unchanged — canonical board + needs-you strip hold; edge fabrications (stripe zero-fill, false OVERDUE) and stale restore remain
- **butler-detail** — solid — improved — .18 console consolidation verified (one tab vocabulary, unified run verb, real doors); Errors-KPI fabrication is the residue
- **sessions** — functional — unchanged — list page near the bar, but the deep-link hub /sessions/:id is a pre-redesign relic missing the entire trace spine
- **timeline** — solid — improved (from weak) — .9/.10 rebuild verified: server heartbeat classification, honest degraded_sources, live tail; URL contract half-built
- **notifications** — functional — unchanged — .3 trace/session out-links verified, but degraded-200 fabrication, dead 'Retried' filter, and event-bus exclusion persist
- **issues** — functional — improved (from weak) — ack-until-recurrence, remedy verbs, real audit links landed; group→occurrences drill-down still structurally impossible
- **audit-log** — functional — improved (from weak) — .4 link fixes verified; reader still withholds result/error/metadata so success and failure rows are indistinguishable
- **approvals** — solid — improved (from functional) — keyboard triage, ranked rail, per-approval URLs, autonomy ledger all hold; decided dossier omits the decision itself
- **calendar** — solid — unchanged — richest workspace, still mouse-only, poll-only, verdict-less; two timezones on one surface
- **health** — functional — unchanged — .2 reached the trackers but missed the leading trend/chart/nutrition surfaces; overview still can't reach its six sub-pages
- **spend** — functional — improved (from weak) — .11 merged One Spend with real by_butler data and saved_7d receipts; live MTD double-counts and four sections still error-as-empty
- **memory** — solid — unchanged — house-ledger voice and provenance spine hold; fan-out silently deflates fleet numbers and the re-embed verb mis-aims
- **entities-plex** — solid — unchanged — signature surface; ranking failure still fabricates a calm household and the verdict is a frontend heuristic
- **entity-detail** — functional — unchanged — .1 truth fixes hold; duplicate detection false-negatives past 100 queue items, ~14 subsections lack error states
- **settings** — functional — unchanged — KPI strip fabricates zeros on subsystem failure; inherited permission cells still dead controls
- **secrets** — solid — improved (from functional) — passport structurally excellent post-amnesty; WhatBreaks slug mismatch is the one critical lie left
- **education** — functional — unchanged — .1/.16 fixes verified but the page remains the clearest pre-Dispatch island: raw hex charts, nine hue families, dead useTeachingFlows
- **chronicles** — functional — unchanged — right editorial skeleton; every day-step blanks the page, zero keyboard surface, refresh doesn't refresh the half it governs
- **qa** — functional — regressed (from solid) — old criticals ('Escalated to user' fabrication, UTC journal) never landed AND .19's pulse strip shipped new bugs (findings_dispatched renders clean-green, silent 25-row rail cap)
- **system** — functional — unchanged — .17 verdict holds at the happy path but asserts 'Instance healthy' over errored sources and is blind to connector liveness
- **ingestion** — solid — unchanged — live bones verified; aggregates freeze at mount and the trace spine is one-directional
- **circles** — functional (thin evidence) — improved (from weak) — .19 Circles lens landed; only residue found is arbitrary-hex label badges with unguaranteed contrast
- **cross:shell-discoverability** — functional — unchanged — one-command-spine landed, but triage keys advertised nowhere, palette invisible at empty query, '/' collides on /memory
- **cross:visual-language** — functional — unchanged — Dispatch layer real but the DEFAULT base layer (root font, page shell, tokens) is still shadcn; lint (.6) can't flip defaults
- **cross:interaction-speed** — solid — improved — speed policy + bus are the right primitives; zero prefetch, three legacy sockets, and poll zoo are the adoption gap
- **cross:accessibility** — solid — improved — .16 floor is real (lint, tokens, reduced-motion CSS); command palette is a fake modal and axe covers 3/38 pages
- **eco:connectors** — n/a — new — perception audit: finance/travel/chronicler promises structurally unfulfillable with email-only sources
- **eco:inference** — n/a — new — every LLM call is a cold CLI spawn; latency unmeasured; discretion spend unattributed
- **eco:knowledge-graph** — n/a — new — lifecycle machinery shipped but dormant: decay never runs, 4/9 butlers never consolidate, catalog has 0 rows
- **eco:cross-butler** — n/a — new — hub-and-spoke fan-out with no return path; no butler can ask another a question
- **eco:proactivity** — n/a — new — RFC 0011 anti-spam governs a minority of actual proactive traffic; quiet hours default open fleet-wide

## Systemic themes

### The truth amnesty stopped one layer above the backend: aggregation endpoints zero-fill on failure

bu-86c4c.1/.2 fixed frontend error-as-empty, but the fan-out/aggregation layer beneath it still collapses failures into confident zeros returned as HTTP 200 with no degraded marker — so the very verdict banners the epic built assert calm over dead sources. This is the dominant NEW+NOT-LANDED defect class of the run, reproducing independently on at least 12 surfaces, and it is invisible to any frontend isError sweep because the fetch succeeds.

*Exemplars:* src/butlers/api/routers/butlers.py:497-503 (stripe failure → [0]*24, folded into aggregates); src/butlers/api/routers/memory.py:118-131 (pool failures summed away silently); frontend/src/components/system/SystemVerdictBanner.tsx:61-62 ('Instance healthy' rendered while backups/insights/posture queries are errored)

*Affected:* dashboard, butlers-roster, notifications, approvals, spend, memory, settings, secrets, system, entities-plex, entity-detail, health, qa, ingestion, sessions

### Built once, never generalized: every epic mechanism has an adoption cliff

The epic shipped correct mechanisms — command registry, keyboard triage, QueryBoundary, FetchingDim, event-cache registry, modal choreography, real-page axe tests — and each stopped at 1-3 consumers. Only Approvals and Issues register palette commands; the approvals j/k/a/d/x loop exists nowhere else and is advertised nowhere; FetchingDim has 3 consumers vs 46 forbidden animate-pulse sites; the event registry misses the notifications page's own query keys and calendar/education aren't on the bus at all; axe covers 3 of 38 pages. Nothing structural makes the primitive cheaper than the fork, so drift regenerates.

*Exemplars:* frontend/src/lib/command-registry.tsx:104 (useRegisterCommands: 2 importers repo-wide); frontend/src/hooks/event-cache-registry.ts:100-104 (notificationPatch skips ['notifications']/['notification-stats']); frontend/src/components/ui/fetching-dim.tsx:5 (3 consumers vs 46 animate-pulse sites)

*Affected:* calendar, memory, health, secrets, qa, spend, education, chronicles, settings, butler-detail, notifications, dashboard, cross:shell-discoverability, cross:interaction-speed, cross:accessibility

### The judgment layer landed on 2 surfaces; 13+ open with static copy or raw counters

bu-86c4c.17 put a synthesized verdict on /system and /butlers only. Approvals, sessions, notifications, qa, spend, ingestion, issues, calendar, education, chronicles, health, butler-detail, and audit-log all open with taglines or KPI grids — while the data for their verdicts is already fetched and, in several cases, rendered nowhere (QA's staffer_status/breaker/credentials, spend's projection_confidence, notifications' by_butler breakdown are all discarded on the client).

*Exemplars:* frontend/src/api/types.ts:4967-4990 vs QaOverviewPage.tsx:239-247 (staffer heartbeat fields fetched, never rendered); frontend/src/pages/SpendPage.tsx:80 (projection_confidence typed and discarded); frontend/src/pages/EducationPage.tsx:78-80 (static marketing tagline as the opener)

*Affected:* approvals, sessions, notifications, qa, spend, ingestion, issues, calendar, education, chronicles, health, butler-detail, audit-log

### The trace spine breaks at its hub nodes

The .3/.4 spine work made signals into doors, but the destinations those doors converge on drop the spine: /sessions/:id (deep-linked from 10+ surfaces) renders no trace_id/request_id/parent/stderr; the audit reader deliberately withholds result/error/metadata (core_122 columns) so the ledger of record cannot say whether any action succeeded; timeline events never carry the trace_id sessions already persist; the ingestion event drawer cannot emit a trace outward; issue groups ('Seen 47x') have no path to their occurrences because the audit API lacks a result/group predicate.

*Exemplars:* frontend/src/pages/SessionDetailPage.tsx:134-263 (omits trace_id/request_id/parent/process_log); src/butlers/api/models/audit.py:58-63 ('intentionally left to a later PR'); src/butlers/api/audit_grouping.py:199-204 + routers/audit.py:291-316 (no result= predicate — group evidence unreachable)

*Affected:* sessions, audit-log, timeline, ingestion, issues, qa, entity-detail, memory

### Dispatch is opt-in, not the default: the base layer is still shadcn

The default typeface is the spec-forbidden system-ui/Roboto stack (Inter Tight is per-component opt-in that flagship pages never take); the shared page shell renders text-3xl font-bold h1s to 24 consumers; empty-state is icon+heading+cta against the one-serif-sentence spec; the same semantic gray has two values (--muted-foreground 0.556 vs --mfg 0.46, 1,378 vs 174 sites); butler hues collide 11-into-8 via modulo; chart color speaks four dialects. The in-flight lint (bu-86c4c.6) stops new drift but cannot flip runtime defaults — every new page is born off-language.

*Exemplars:* frontend/src/index.css:8 (root font-family system-ui/Roboto vs spec.md:143); frontend/src/components/ui/page.tsx:106 (text-3xl font-bold, 24 consumers); frontend/src/components/ui/ButlerMark.tsx:104-105 (idx % 8 → qa=chronicler, relationship=education, travel=finance)

*Affected:* cross:visual-language, education, system, issues, audit-log, settings, dashboard, sessions, timeline, ingestion, chronicles

### Component-local filter state severs the inbound half of clause-is-a-door

Outbound doors landed, but the pages they point at hold filters in useState with no URL form, so the system's own attention links dump the owner on unfiltered streams: the dashboard's 'N failed notifications' links bare /notifications, Trust Console's 'Full audit log' drops its privileged predicate, a shared timeline URL loses every facet, and no surface can ever deep-link a filtered view of notifications/timeline/issues. Filtered views are also unshareable, un-bookmarkable, and palette-unaddressable.

*Exemplars:* frontend/src/pages/NotificationsPage.tsx:75 (filters in useState, no useSearchParams anywhere in file); frontend/src/components/overview/model.ts:511 (attention row href='/notifications', no ?status=failed); frontend/src/pages/TimelinePage.tsx:76-78 (facets/view in plain useState while ?event drawer param survives contextless)

*Affected:* notifications, timeline, issues, audit-log, health, dashboard, settings

### Shipped but dormant: the ecosystem's judgment machinery is built and switched off

The substrate-level counterpart of the UI themes: memory decay/fading/expiry code exists and is wired to nothing (0 fading, 0 expired rows live; the spec-required sweep has never run, so every confidence number shown is an un-decayed write-time value); 4 of 9 memory butlers never consolidate (switchboard: 4,148 pending episodes vs 44 facts); the cross-butler discovery catalog has spec+table+tools and 0 rows; RFC 0011's anti-spam budget/dedup governs only insight-scan traffic while finance alone runs five direct-notify cron tasks around it; quiet hours default open and no butler configures them.

*Exemplars:* src/butlers/modules/memory/storage.py:2354 (run_decay_sweep in no job registry, no schedule, no tool); src/butlers/modules/memory/__init__.py:185 + public.memory_catalog count=0 (enable_shared_catalog false everywhere); roster/finance/butler.toml:106-155 (five proactive tasks bypassing the broker via direct notify())

*Affected:* eco:knowledge-graph, eco:proactivity, eco:cross-butler, eco:inference, memory, notifications

## Ranked moves

### 1. Honest aggregation: purge server-side zero-fill and adopt the degraded envelope fleet-wide (ux, L)

**What:** Extend the repo's own aggregates_available convention to every fan-out/aggregation endpoint: butlers board (stripe failure → stripe_unavailable flag, cron failure → cadence 'unknown' not false-OVERDUE), memory fan-out (pools_failed in meta), notifications list/stats (source_available), approvals flat/history (sources_degraded), settings header_counts (nullable fields), secrets breaks-catalogue (degraded flag), spend summary/breakdown (unavailable_butlers). Then gate every verdict/all-clear renderer — SystemVerdictBanner, NeedsYouStrip, plex flanks, spend movers — on isError + degraded flags so calm is never asserted over failed or partial sources.

**Why:** North star: 'no number is ever fabricated; every degraded source names itself; no failed fetch ever impersonates an empty queue.' This is the run's dominant defect class — the truth amnesty (bu-86c4c.1/.2) fixed the frontend while the backend still fabricates calm on ~12 surfaces, invisible to any client-side sweep because the fetch returns 200.

**Evidence:** src/butlers/api/routers/butlers.py:497-503 ([0]*24 on stripe failure) and :480-488 (cron failure → false OVERDUE); src/butlers/api/routers/memory.py:118-131; src/butlers/api/routers/notifications.py:93-111; src/butlers/api/routers/approvals.py:1546-1548; src/butlers/api/routers/settings_console.py:137-282; SystemVerdictBanner.tsx:61-62 ('Instance healthy' over errored sources); NeedsYouStrip.tsx:59-67 ('All N healthy' when liveness is unknowable); SpendPage.tsx:1377-1381 (movers fabricate '+$X · new' from a half-failed comparison).

**Slices:** 1) Shared envelope helper + convention doc (extend CLAUDE.md Degraded-Mode section); 2) per-router sweeps as independent PRs (butlers, memory, notifications, approvals, spend, settings, secrets), each with a contract test that a raising source yields degraded flags, never zeros; 3) verdict/all-clear gating on system/butlers/plex/spend renderers; 4) frontend em-dash + named-source rendering via the existing SourceDegradedNote vocabulary.

### 2. Confirmed-lies hotfix batch: seven verified fabrications, each a few lines (ux, M)

**What:** One sprint of ≤S fixes for lies confirmed at decision points: QA PRPanel 'No PR. Escalated to user.' rendered for every in-flight case + pulse strip coloring findings_dispatched patrols clean-green (status string mismatch) + silent 25-row rail cap; butler-detail Errors KPI structurally hardwired to 0 (counts a trigger_source value that cannot exist); secrets PageSystem green plaque over failing state + fabricated 0ms probe latency + audit link that 422s (missing s: prefix); notifications 'Retried' filter that can never match a row; spend live-MTD double-counting against its own polling baseline; timeline dead-API-impersonates-idle (stale strip when head poll fails after first paint).

**Why:** Each is a confirmed fabrication — the purest violation of earned calm — at the exact moment the owner decides whether to act, and each fix is mechanically small. Doctrine: trust defects outrank everything at this cost.

**Evidence:** PRPanel.tsx:26-31 + CaseDossier.tsx:150-157 (stage available, unused); QaOverviewPage.tsx:292-296 vs src/butlers/api/routers/qa.py:1631-1637 ('dispatched' vs 'findings_dispatched'); ButlerActivityTab.tsx:301-307 vs core/sessions.py:25; secrets pages.tsx:1648-1649, :1139/:1762 (latencyMs:0), :1928; NotificationsPage.tsx:40-47 vs notifications.py:149-186; SpendPage.tsx:1243-1260; use-timeline-ledger.ts:131-133.

**Slices:** File as one batch bead with seven independent commits; each carries a regression test pinning the honest behavior (e.g. state='failed' system credential renders red plaque; findings_dispatched dot renders amber; MTD counter resets on forecast resolution).

### 3. Wire the memory lifecycle: decay sweep runs, consolidation is module-default, backlog drains (ecosystem, M)

**What:** Register run_decay_sweep as a scheduled-job handler and have the memory module self-register default maintenance schedules (decay, consolidation, episode cleanup, superseded purge) on startup for all 9 butlers — toml overrides cadence, not existence. Drain the 6,500+ pending-episode backlog (switchboard 4,148, finance 1,294, education 1,137) with a bounded catch-up job, and swap the ivfflat lists=20 indexes (built on empty tables) to HNSW while touching migrations.

**Why:** Substrate-level fabrication: docs/modules/memory.md and the memory-retention-policy spec promise a decay sweep that has never run anywhere, so every confidence value the console displays is an un-decayed write-time number masquerading as a maintained one, and 4/9 butlers promote nothing to durable facts. Turning on shipped machinery is the highest value-per-cost ecosystem move available.

**Evidence:** src/butlers/modules/memory/storage.py:2354 (run_decay_sweep absent from _MEMORY_MAINTENANCE_JOB_HANDLERS at scheduled_jobs.py:378-381, no toml schedule, no tool); live dev DB: 0 fading, 0 expired rows; roster grep: only 5 of 9 butlers schedule consolidation; migrations/001_memory_schema.py:84,203,330 (ivfflat lists=20 at empty-table creation).

**Slices:** 1) memory_decay_sweep handler + schedules in the 5 already-cron'd butlers (ships alone); 2) module-default schedule registration in on_startup + delete copy-paste toml blocks; 3) bounded consolidation backfill respecting dead_letter states; 4) HNSW migration + nightly synthetic-scale recall harness; spec delta to memory-retention-policy declaring jobs module-default.

### 4. Dashboard coherence: one liveness model, one decision safety contract (ux, M)

**What:** Move the Overview onto GET /api/butlers/board's canonical server verdict (delete model.ts's private status sets, 5-minute threshold, and the two dead queries — the event bus already live-patches the board key), and fold the approvals undo window (scheduleDecision/UNDO_WINDOW_MS) into the shared useApprovalDecisionMutations hook so dashboard rows, /approvals, and the upcoming chat widget inherit the identical grace-window contract.

**Why:** 'One instrument built by one hand': the flagship page currently renders contradictory liveness verdicts against /butlers in the same instant (and its KPI contradicts its own attention list), and the identical approve verb is undoable on /approvals but fires an irreversible tool execution on a single click on the page the owner lands on first.

**Evidence:** frontend/src/components/overview/model.ts:151-153,271-311 (private HEALTHY/DEGRADED/OFFLINE sets + own stale threshold) vs use-butler-status-board.ts:1-15 (canonical cadence-aware verb); event-cache-registry.ts:79-93 (patches ['butlers','board'] but not the dashboard's keys); DashboardPage.tsx:139-141 (direct mutate) vs ApprovalsPage.tsx:1308-1325 (undo window).

**Slices:** 1) model.ts accepts BoardRow[] (pure-function tests first); 2) DashboardPage swaps hooks, deletes dead queries, KPI consumes board verdict; 3) extract scheduleDecision into use-approval-decisions behind an option, ApprovalsPage consumes it behavior-identically; 4) dashboard rows opt in with inline 'Approving in 5s · Undo' state.

### 5. One session dossier on the trace spine, live for running sessions (ux, M)

**What:** Merge SessionDetailPage and SessionDetailDrawer into a single Dispatch-grade SessionDossier rendering every SessionDetail field the store holds — trace_id → /timeline, request_id → /sessions?request=, parent_session_id link, complexity, resolution_source, cost, and process_log stderr/exit_code as the named root evidence for failures. Always fetch via global getSession (delete the ?butler= dual path and the 'try adding ?butler=name to the URL' copy), collapse to one bus-invalidated query key so the palette's own trigger→session flow stops landing on a frozen 'Running' page, and mirror list selection to ?selected=.

**Why:** The trace-id spine is only as strong as its busiest hub: 10+ surfaces deep-link to /sessions/:id, where the owner arrives asking 'why did this fail' and finds no trace, no stderr, no parent, and a page that never updates while a session runs — detect works, diagnose dead-ends.

**Evidence:** SessionDetailPage.tsx:134-263 (omits trace_id/request_id/parent/process_log despite types.ts:221-268 carrying all of them); inbound links from SpendPage.tsx:719, ApprovalsPage.tsx:670, TimelineLedger.tsx:76, notification-feed.tsx:174 et al.; event-cache-registry.ts:88-92 invalidates only ['session-detail', butler, id] while GlobalActionsRegistrar.tsx:36 navigates to the un-invalidated global key.

**Slices:** 1) Extract SessionDossier from the drawer content with full field set + stderr disclosure; 2) rebuild SessionDetailPage as a Page-archetype wrapper on it, one global query key, bus-invalidated; 3) running-session elapsed ticker + streaming tool-call tail; 4) ?selected= URL mirroring + j/k/[/]/y keyboard loop on the list.

### 6. The ledger tells the truth: audit outcome trio + issue-group occurrences (ux, M)

**What:** Project result/error/metadata (persisted since core_122) through the audit reader, API model, and table — Outcome column in the three state colors, error text and metadata in the detail row, failure rows linking to their /issues group. Add result= and group= predicates to GET /api/audit-log (or GET /api/issues/{key}/occurrences reusing the shared CTE) so 'Seen 47x' finally opens its 47 occurrences with session/request_id links. Default the page to kind=privileged (backend predicate already exists), fix Trust Console's link to carry it, and render the detail expansion under its row instead of at the table bottom.

**Why:** The audit log is the root of the evidence spine and it currently cannot say whether any audited operation succeeded — while the issues feed built ON result='error' rows offers no path to them. 'Root evidence one keystroke away' dead-ends one hop before the truth on both surfaces.

**Evidence:** src/butlers/api/models/audit.py:58-63 ('intentionally left to a later PR'); routers/audit.py:398,439 (projections omit the trio) and :291-316 (no result/group predicate); audit_grouping.py:199-204 (multi-butler groups emit bare /audit-log); AuditLogTable.tsx:131-253 (detail row appended after the entire map); SettingsPermissionsPage.tsx:459 (privileged predicate dropped mid-drill).

**Slices:** 1) Reader/model/type projection + Outcome column + adjacent detail row (coordinate DisclosureRow with bu-f310e); 2) result=/group= predicate + full-predicate links from audit_grouping; 3) issues rows gain DisclosureRow expansion with occurrences + ButlerMark chips; 4) kind=privileged default + noise toggle + Trust Console link fix.

### 7. Flip the Dispatch base layer: defaults, not opt-ins (ux, M)

**What:** Make the language the default in one coordinated pass: root font-family → var(--font-sans) (killing the spec-forbidden system-ui/Roboto default); rewrite ui/page.tsx headings to Display/Title 500 and ui/empty-state.tsx to the one-serif-sentence spec (~48 consumers inherit conformance); alias the shadcn token names to Dispatch values in index.css (--muted-foreground→--mfg, --card→--bg-elev, --destructive→--red — 1,378 sites snap to canonical values in one diff); explicit 11-slot butler-hue map ending the modulo collisions, spec table generated from the constant; extend chart-colors.ts to four semantic channels (series/butler-identity/category/neutral-density-ramp) and migrate the raw-hex charts; retire skeleton-pulse for FetchingDim + static placeholders.

**Why:** 'One instrument built by one hand' cannot be lint-enforced onto a base layer that defaults off-language — every un-redesigned surface and every 3am page is born wrong. This single move does more for fleet-wide coherence than any per-page reskin, and it hands bu-86c4c.6 a clean baseline instead of day-one suppressions.

**Evidence:** index.css:8 (system-ui/Roboto root default vs spec.md:143; index.css:247-249 admits opt-in); ui/page.tsx:106 (text-3xl font-bold, 24 consumers); index.css:29 vs :109 (two grays for one role, 1,378 vs 174 sites); ButlerMark.tsx:51-105 (11 butlers into 8 slots); MasteryTrendChart.tsx:55-56 #3b82f6, hex-heatmap.ts:33-37 (state ramp repurposed for density); 46 animate-pulse sites vs fetching-dim.tsx's 3 consumers.

**Slices:** 1) Root font + page.tsx heading unification + empty-state rewrite; 2) token aliasing + contrast-test updates; 3) butler-hue registry + collision test + spec table sync; 4) chart registry channels + education/chronicles/topology migration; 5) skeleton retirement + animate-pulse added to the .6 forbidden list.

### 8. One attention ledger: all proactive owner egress through the broker (ecosystem, L)

**What:** Route every proactive owner-facing byte through RFC 0011's machinery: convert the direct-notify prompt-cron tasks (finance's five, health's digests) into insight candidates; add a deterministic owner-level attention policy (quiet hours, per-intent daily budgets, cross-fleet dedup, same-window coalescing into one composed Dispatch-voice message) enforced at the Switchboard/Messenger notify boundary; make the delivery cycle and notify gate consult the context bus (dnd/sleeping) deterministically; add an hourly urgent sub-cycle so priority≥90 means hours, not one daily slot.

**Why:** Earned calm must hold at the phone, not just the console: the anti-spam architecture is structural for a minority of traffic, quiet hours default open and no butler configures them, and the observed calendar double-notify is one instance of the missing arbiter, not its extent. Extends beyond bu-24lu6 (which adds pushes) — this is the governance layer under them.

**Evidence:** roster/finance/butler.toml:106-155 (five tasks instructing direct notify(), bypassing budget/dedup/cooldown); core-notify spec quiet-hours gate defaults-open + zero delivery_preferences rows fleet-wide; grep: context-bus consumed only by spawner_context.py — the notify gate never checks dnd; roster/switchboard/butler.toml:59-63 (one insight delivery slot/day for 'time-critical').

**Slices:** 1) Attention ledger table + owner-level quiet hours + notify-path counting (deterministic, no butler changes); 2) context-bus gating of delivery cycle + notify; 3) convert finance's five tasks to candidates; 4) same-window coalescing + urgent hourly sub-cycle; 5) dashboard attention-ledger panel under Trust Console; RFC amending 0011 + Messenger/Switchboard contracts.

### 9. Verdict-opener rollout: a shared, degradation-honest DispatchVerdict primitive (ux, L)

**What:** Extract SystemVerdictBanner's composition pattern into one shared primitive — deterministic template, clauses as typed doors, any degraded input names itself or suppresses the all-clear — and adopt it as the opener on approvals ('3 waiting; nearest expires in 40m; one approved action never ran'), sessions (failure clustering), notifications (windowed by_butler verdict), qa (staffer masthead from fields already fetched), spend (pace + confidence + top mover), ingestion, issues, butler-detail, and calendar. No LLM cost — pure composition from data each page already fetches.

**Why:** The north star's signature property — 'every surface opens with the system's own synthesized verdict, and every clause of that prose is a door' — landed on exactly two surfaces. Nine agents independently proposed the same move because the inputs are already on the wire and, on qa/spend/notifications, literally discarded.

**Evidence:** types.ts:4967-4990 (staffer_status/last/next patrol/breaker/credentials fetched, unrendered at QaOverviewPage.tsx:239-247); SpendPage.tsx:80 (projection_confidence discarded before render); notification-stats-bar.tsx:90-99 (by_butler thrown away); EducationPage.tsx:78-80 (static tagline).

**Slices:** 1) Extract primitive + variants (skeleton/all-clear/problems) with the isError-suppression contract from move 1; 2) adopt on qa + approvals + spend (highest-stakes, data-ready); 3) sessions/notifications (needs small aggregate facets); 4) ingestion/issues/calendar/butler-detail; each adoption is an independent S/M PR.

### 10. One overlay contract + shell announcer: fix the fake-modal command palette (ux, M)

**What:** Extract useModalChoreography (focus-in, trap, Escape, focus-restore, sr-only status region) from the ingestion EventDrawer and apply it to EntityFinder — currently a raw div overlay with no dialog role that leaks Tab focus behind its own scrim and never restores focus — plus TimelineEventDrawer, CalendarAgendaView (aria-modal with no trap: worse than no ARIA), and ButlerManagementTab. Add one shell-level sr-only aria-live region announcing stream state edges, route changes, and new-event counts (and render LiveIndicator on mobile, where the degraded-stream signal currently vanishes). Lint hand-rolled fixed-inset overlays; generalize the real-page axe pattern into a route-registry-driven sweep over all 38 pages.

**Why:** The console's single most important surface fails basic dialog semantics, and 'every degraded source names itself' is currently visual-only — SR users never hear the fleet stream drop. The correct implementations exist in-repo once each; this makes them mechanisms.

**Evidence:** EntityFinder.tsx:431-453 (no role=dialog/aria-modal, Tab leak when activeResult null, no focus restore); CalendarAgendaView.tsx:44-48; LiveIndicator.tsx:56-69 (silent swaps, hidden sm:inline-flex); 3 of 38 pages have axe tests (ButlersPage/ButlerDetailPage/TimelineTab only); EventDrawer.tsx:554-575 as the gold-standard donor.

**Slices:** 1) Extract hook + migrate EntityFinder (keep Tab=hop on entity rows only, fix activeResult tracking); 2) migrate the three other overlays; 3) shell announcer fed by useEventStream edges + Page title effect + NewEventsPill; 4) overlay lint entry; 5) registry-driven axe suite with skip-manifest burn-down.

### 11. Shortcut registry + fleet palette adoption: an unadvertised shortcut becomes structurally impossible (ux, L)

**What:** Add binding metadata to PaletteCommand and ship useRegisterShortcut — one hook that both installs page-scoped keys (with the editable-field guard, subsuming bu-5o22a, extended to SELECT/contentEditable/open-modal suspension) and publishes them to the '?' sheet ('On this page' section) and the palette's inline kbd column. Make the palette browsable (recents + verbs at empty query, shared fuzzy scorer), migrate the approvals j/k/a/d/x and ChatPanel chords onto it, delete MemorySearch's dead colliding '/' handler, extract useListTriage from Approvals for Dashboard/Issues/Notifications rows, and register verbs on the ten verb-mute surfaces (calendar, memory, secrets, qa, spend, health, education, butler-detail, settings, chronicles).

**Why:** 'The first use needs no docs, the tenth needs no mouse': the one-command-spine landed as infrastructure and 2 of ~25 pages adopted it; the product's best interaction (approvals triage) is a secret advertised nowhere; the fleet's richest workspace (calendar) has one keydown handler in 6,472 lines.

**Evidence:** command-registry.tsx:36-45 (no binding field) and :104 (2 importers); ApprovalsPage.tsx:1332-1374 (keys, zero hints anywhere); use-keyboard-shortcuts.ts:41-45 vs MemorySearch.tsx:82-96 ('/' double-fire); EntityFinder.tsx:366-375 (verbs hidden until first keystroke, no recents); CalendarWorkspacePage.tsx:2017 (sole keydown).

**Slices:** 1) binding field + useRegisterShortcut + help-sheet section + palette kbd column; 2) migrate approvals/chat, delete the '/' collision, add u=undo; 3) recents store + fuzzy scorer + empty-query verbs; 4) useListTriage extraction + Dashboard/Issues/Notifications adoption with a shared footer hint strip; 5) per-page verb registration sweep (each page an S PR); complements in-flight bu-86c4c.15 (verbs) without overlap — this is reachability.

### 12. API-direct inference lane + purpose-tagged spend attribution (ecosystem, M)

**What:** Add an 'api' runtime adapter (direct provider tool-use call, no CLI subprocess) and flip the discretion/classification catalog tiers onto it — connector screening and switchboard routing return in model-time instead of paying cold CLI+MCP bootstrap, halving the two-serial-spawn latency of every interactive reply. Simultaneously make that spend honest: add a purpose dimension to public.token_usage_ledger, fix the classifier's trigger_source 'tick'→'classification' (it currently makes spend rules targeting {trigger:'route'} silently never match), and give discretion calls per-connector identity instead of '__discretion__' with NULL session_id.

**Why:** A JARVIS answers in conversational time and accounts for every token: today every thought is a heavyweight subprocess and the highest-volume calls are the least attributed — /spend cannot price one inbound Telegram message, and classification spend masquerades as scheduler ticks. Vision rule 4 intact: judgment stays in LLM calls, only the transport gets cheap.

**Evidence:** core/runtimes/claude_code.py:439 (subprocess per invoke); discretion_dispatcher.py:226-239 (1-turn screens on the same path, mcp_servers={}), :106/:251-258 ('__discretion__', session_id=None); modules/pipeline.py:1979-1984 (trigger_source='tick' on classification); model_routing.py:481-483 (trigger constraints fail-closed on None).

**Slices:** 1) Adapter + tests behind an unreferenced catalog entry; 2) flip discretion tiers, verify connector screening; 3) classifier structured-tool-use fast lane with CLI fallback; 4) ledger purpose column + both write paths + trigger_source rename with fixture sweep; 5) surface purpose in /spend drill-down and rules.

### 13. URL-backed filters + predicate-carrying inbound doors on the triage surfaces (ux, M)

**What:** Migrate filter state to useSearchParams on notifications, timeline (facets/view/butlers — the drawer's ?event already is), issues (plus a default 7d window and severity/butler pills with a capped CTE), and health measurements (?type=); collapse audit-log's dual filter state (the ?actor param silently overriding the visible input) into one URL-serialized state. Then make the system's own inbound clauses carry their predicates: dashboard 'N failed notifications' → /notifications?status=failed, Trust Console 'Full audit log' → ?kind=privileged, stat tiles become filter anchors, and off-page ?event deep links resolve honestly instead of rendering nothing.

**Why:** Clause-is-a-door fails inbound across six surfaces: the console hands the owner links that dump them on unfiltered streams, filtered views are unshareable and palette-unaddressable, and one page shows data its own controls deny. Cheap, mechanical, and it unlocks every future deep-link (chat widget, Telegram alerts).

**Evidence:** NotificationsPage.tsx:75 (useState-only); model.ts:511,587-599 (bare /notifications hrefs); TimelinePage.tsx:76-78 + TimelineLedger.tsx:302-311 (?event silently renders nothing off-page); AuditLogPage.tsx:40-72 (URL overrides input); issues.py:64 (unbounded all-time CTE, no window).

**Slices:** 1) Notifications params + dashboard/tile predicate links; 2) timeline facets → URL + off-page ?event resolution (single-event lookup or explicit notice); 3) issues 7d window + pills + LIMIT; 4) audit single filter state + action-cell pivot; 5) health ?type= feeding the one-scope page.

### 14. Interaction-speed consolidation: one socket, poll-policy tokens, intent prefetch, never-blank floor (ux, L)

**What:** Wrap the singleton event stream in an EventBusProvider with a subscribe(type, cb) API and retire the three legacy per-page WebSockets (approvals stream currently double-invalidates the same keys as the registry); replace the refetchInterval zoo with named policy tokens (bus-covered → 5-min reconcile per the blessed Approvals pattern) enforced by lint; add usePrefetchOnIntent to RowLink/DisclosureRow/palette-highlight via a route-registry prefetch map (zero prefetch call sites exist today); extend placeholderData+FetchingDim to windowed lists (chronicles day-step currently blanks the entire page, calendar week-nav, finance, search); and add a registry↔hook coverage test so a surface can never silently go event-dead again (the notifications-page miss is the proven instance).

**Why:** 'State arrives as a live event stream' and 'moves at the speed of thought': the primitives landed (bus, speed policy, FetchingDim) but adoption stopped, leaving duplicate refetches, 15-30s polls beside the bus, cold drill-downs everywhere, and full-page skeleton flashes on the archive's hottest path.

**Evidence:** use-approvals-stream.ts:147-150 duplicating event-cache-registry.ts:55-61; use-butler-status-board.ts:191 (30s poll on a bus-patched key); repo-wide grep: zero prefetchQuery sites; use-chronicles-briefing.ts:33-43 (no placeholderData → WorkspaceSkeleton per day-step); event-cache-registry.ts:100-104 (misses ['notifications']/['notification-stats']).

**Slices:** 1) Provider + subscribe API + delete useApprovalsStream (pure win); 2) port spend ticker + settings console, delete their sockets; 3) POLL tokens + demote the four bus-covered surfaces + lint on numeric intervals; 4) prefetch hook wired to sessions/timeline/approvals rows then palette; 5) placeholderData sweep (search, chronicles, calendar, finance) + registry coverage test.

### 15. Turn on the cross-butler knowledge plane: memory_catalog enable + backfill (ecosystem, M)

**What:** Flip enable_shared_catalog on fleet-wide (or change the default), add a backfill job upserting the ~3,600 existing facts/rules into public.memory_catalog via its UNIQUE(source_schema,source_table,source_id) key, and land the first consumers: catalog search in briefing context assembly and a fleet-knowledge search surface on the dashboard memory router.

**Why:** There is currently no way for one butler to read another's knowledge short of a full Switchboard LLM round-trip, yet the sanctioned discovery plane is fully built — spec, table, tools — and switched off with 0 rows. Cheapest durable capability in the ecosystem backlog; also the prerequisite sequencing step before delegation/subscriptions are worth their L costs.

**Evidence:** src/butlers/modules/memory/__init__.py:185 (default False; zero toml enables fleet-wide); live dev DB: SELECT count(*) FROM public.memory_catalog = 0; tools/reading.py:113 (memory_catalog_search returns empty for every butler); write-behind already implemented at tools/writing.py:381,531.

**Slices:** 1) Flip default + toml enables (write-behind starts populating); 2) idempotent backfill job + test; 3) briefing context consumption + dashboard cross-butler search; spec delta making catalog default-on with a backfill requirement.

## Dropped (dedup ledger — nothing silently vanished)

- Cross-butler delegation ask/answer ledger — strongest new capability proposal but L-cost and correctly sequenced after the memory_catalog flip (move 15); carry to next run.
- Domain-event subscriptions + cross-domain case files (eco:cross-butler) — premature before catalog/delegation land; both L-cost with dependency chains.
- Deterministic precondition gates on scheduled prompt tasks (eco:inference) — real recurring spend waste, but efficiency-only; below the trust-weighted cutoff this run.
- Latency spine per-phase spawn timings (eco:inference, S) — narrowly cut; file as a standalone bead alongside move 12, it makes that move measurable.
- New connectors (simplefin, flightstatus, activitywatch, weather, parcels) — sound, manifesto-grounded perception expansions; defer as a dedicated connector epic rather than consuming top-15 slots against trust defects.
- Speculative reply drafting, wake-anchored delivery, per-category insight feedback (eco:proactivity) — fold behind move 8's attention ledger and bu-24lu6's decision loop as later slices.
- Secrets scheduled verification sweep + event-bus credential state ('earned green') — valuable L; below cutoff, uncovered by any bead, file for next run.
- Dashboard clause-doored LLM verdict with anchor spans (L) — subsumed by move 9's deterministic verdict primitive; the LLM-elaboration half deferred.
- Butler-detail run-stays-on-console drawer — page-local polish; verdict opener folds into move 9, palette verbs into move 11.
- Health one-scope measurements page, daily_avg lead, condition-color inversion, six-sibling dialect — page-local M work; dialect largely covered by move 7's base flip; file as a health bead cluster.
- Education tutor hero / Dispatch reskin / event-bus / node dossier — reskin covered by move 7, bus by move 14, palette by move 11, verdict by move 9; node-dossier drill-down deferred.
- Chronicles day-scoped evidence coverage strip + keyboard SVG timeline marks — coordinate with the in-flight Chronicler IEA epic (bu-jc6htw/bu-8whey5) so the Day Ribbon inherits the fixes rather than duplicating them.
- Calendar overlay link_ref drill-down, always-on counts masthead, one-timezone sweep, now-line — real gaps but routed under the calendar roadmap epic bu-l3k0zg; keymap/bus halves covered by moves 11/14.
- Plex server-side attention verdict, optimistic retier undo, find fall-through, canvas keyboard parity — verdict folds into move 9; the rest are page-local S/M beads for the fleet.
- Timeline server-side heartbeat exclusion + saved-view divergence honesty + view-delete undo — below cutoff; deep-link half covered by move 13.
- Spend butler-hue on CostStripeChart + keyboard money controls + verdict line — hue folds into move 7's chart registry, keyboard into move 11, verdict into move 9.
- Destructive-without-recovery trio (webhook delete, spend-rule delete, timeline saved-view delete) — file as one S bead applying the models-page DeleteConfirmDialog / undo-toast pattern.
- Settings model-verification evidence spine + DB size-history endpoint + permissions inherited-cell operability — good M beads, below cutoff; permissions spec self-contradiction routes to bu-9q1dx reconciliation.
- QA rail truncation count / j-k rail / claim-evidence focusability — truncation and status-mapping land in move 2; keyboard halves land via move 11's registry adoption.
- New inert-KPI instances (memory overture/pipeline stats, chronicles KPI cells, secrets KPI cells, butler-detail config KVs) — extend bu-9d5vp's scope note rather than a new move.
- Circles arbitrary-hex badge contrast + system text-amber-600 raw utility — extend bu-kx3xc's site list; audit DisclosureRow anchoring coordinates with bu-f310e (move 6 notes this).
- Editable-field guard gaps (SELECT, contentEditable, modal suspension) — scope input to bu-5o22a, subsumed by move 11's useRegisterShortcut guard.
- rule_applications spec drift + 'memory butler' stale references + dashboard-domain-pages vs design-language spec conflict — route to bu-17axsl doctrine-spec-code reconciliation.
- Sessions drawer null-butler hard-fail + ToolCallTimeline raw palette — folded into move 5's dossier merge and move 7's token sweep respectively.
- Entity-detail per-entity duplicates endpoint + two-h1 headline + Telegram wizard relocation — real (duplicates is the page's worst lie) but page-local; file as an entity-detail bead cluster, duplicates endpoint first.
- Ingestion frozen-window aggregates fix — S and real, but ingestion-local; file as a bead (it narrowly lost the last hotfix-batch slot to fleet-visible lies).
- Attention-strip inert links on ingestion timeline + bidirectional trace on event drawer — trace half overlaps move 5/6's spine work; file the one-import link fix as an S bead.
