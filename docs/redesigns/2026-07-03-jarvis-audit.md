# JARVIS Frontend Audit — 2026-07-03

Full-frontend design audit of the Butlers dashboard held to the th-design bar
against `about/heart-and-soul/vision.md` and `docs/frontend/purpose-and-single-pane.md`.
28 subagents: one per page surface (23) plus four cross-cutting sweeps
(shell/discoverability, visual language, interaction speed, accessibility) and a synthesis pass.
Backward compatibility was explicitly waived — moves propose the ideal design.

**Full per-page dossiers** (current state, JARVIS gap, redesign concept, moves, all 331
evidence-cited findings) live in [`2026-07-03-jarvis-audit-data.json`](2026-07-03-jarvis-audit-data.json)
— query one page with `jq '.audits[] | select(.page=="<key>")' <file>`.

## North star

The Butlers dashboard becomes the operator console for a sovereign, one-person AI household staff — the single pane where the owner verifies in five seconds that the fleet is healthy, sees exactly what needs them (and nothing that doesn't), and clears that queue in under a minute without leaving the screen or touching the mouse. The console leads with judgment, not data: every surface opens with the system's own synthesized verdict ("All 12 healthy, 3 things need you, $3.12 today, chronicler is 2 days overdue against its own schedule") rendered in the Dispatch editorial voice, and every clause of that prose is a door — one keystroke from any signal to its root evidence (the session transcript, the failing connector, the exact audit rows, the quarantine reason) via an unbroken trace-id spine that never drops its context.

Its defining property is earned calm. The console is structurally incapable of showing confidence it cannot prove: no number is ever fabricated, no failed fetch ever impersonates an empty queue or a healthy fleet, every degraded source names itself in its own row, and "quiet" appears only when every source verifiably loaded. State arrives as a live event stream, not a 30-second poll — the owner can never catch the dashboard not knowing something the daemon already knows — and every action the owner takes (approve, dismiss, restore, retry, correct) applies in the same frame with a loud, undoable rollback on failure. Detect, diagnose, and act complete on one screen: signals carry their first remedy inline, so a quarantined butler shows its reason and its Restore button, a dead-lettered episode shows its retry, and an approval teaches the system a standing rule as it is granted.

Finally, it reads as one instrument built by one hand. A single design language (Dispatch: hairline rules, mono eyebrows, serif voice, three state colors with one meaning each, butler hue as genuine identity) enforced by machine rather than discipline; one command palette that fuzzy-matches verbs and nouns from a single registry that also drives the sidebar, chords, and tooltips; one vocabulary per concept across every page. The result honors the vision's success criterion directly: the system is boring, the console is trusted, and the owner stops thinking about the machinery — because every time they glance at it, it has already told them the truth and handed them the lever.

## Tier board

- **World-class** (0): —
- **Solid** (7): dashboard, butlers-roster, calendar, memory, entities-plex, qa, ingestion
- **Functional** (15): butler-detail, sessions, notifications, approvals, health, entity-detail, settings, secrets, education, chronicles, system, cross:shell-discoverability, cross:visual-language, cross:interaction-speed, cross:accessibility
- **Weak** (5): timeline, issues, audit-log, groups, costs
- **Broken** (0): —

## Systemic themes

### Fabricated evidence: the console invents data and presents it as measured

Multiple surfaces render hardcoded, guessed, or mislabeled values with the full visual authority of real telemetry: a pseudo-random '7-day trend' sparkline on the home page, per-day butler cost stripes computed by smearing period aggregates uniformly across days, a Reviews tab that displays every non-mastered concept as exactly '50%', a security surface whose sublines ('refresh failed · 2d', '0ms', 'Everything else verified within the hour.') are string literals, provenance reveals that can show another fact's provenance, loan amounts rendered as raw cents, and tool names regex-guessed from result prose. This is the single worst sin on a trust-the-system dashboard (design-bar bias 1; the pill-honesty doctrine) — each instance corrupts the calm-confidence contract that is the product's reason to exist.

*Exemplar:* frontend/src/components/costs/CostWidget.tsx:52-62 — sparkline bars are `20 + ((i * 37 + 13) % 80)` with a comment admitting it is a placeholder, rendered under a '7-day trend' label; secrets spine-builder.ts:76-81 hardcodes 'refresh failed · 2d' for every expired credential; education ReviewTimeline.tsx:49-51 fabricates mastery as (mastered ? 1.0 : 0.5) * 100.

*Affected:* dashboard, costs, secrets, education, entity-detail, sessions, timeline, ingestion

### Failure impersonates health: errors render as calm empty states almost everywhere

The dominant structural defect in the codebase: hooks are consumed as `data ?? []` with isError never threaded through, so an API outage renders as 'No pending approvals.', 'Nothing waiting.', 'The ledger is empty.', 'No groups found.', an empty health record, 'No curriculums yet', a confident '$0.00' total, a green '0/0 reporting' pill, or a topology map silently missing all connectors. On a monitoring console this is worse than a crash — the owner reads silence as health, which is exactly the failure mode several components' own docstrings warn against. The degraded-envelope convention (aggregates_available) exists in the repo's own doctrine but is applied on only a few endpoints.

*Exemplar:* ApprovalsPage.tsx:913-926 — the trust-critical human-in-the-loop queue destructures no error state, so a fetch failure renders 'No pending approvals.'; use-butler-status-board.ts:335 sums (costToday ?? 0) into an authoritative '$0.00' footer when the spend source errors; HealthOverviewPage trackers render 'No measurements logged yet' when the backend is down.

*Affected:* dashboard, butlers-roster, butler-detail, approvals, groups, health, memory, education, costs, timeline, system, sessions, qa, secrets

### Dead-end signals: drill-down breaks at nearly every joint

The bar's core promise — instant drill-down from any signal to root evidence — fails pervasively: trace-id links navigate to unfiltered timelines with the trace discarded (twice, independently), the issues 'View' link emits params the audit log ignores, butler index rows and KPI cells are inert, attention-strip items render as underlined spans with no navigation, group member counts cannot be opened despite the endpoint existing, approval evidence lines and audit request_ids are plain text, quarantined butlers hide their quarantine_reason at the exact moment of decision, and rows are div-onClicks that defeat cmd-click/middle-click. The Detect→Diagnose loop the single-pane doctrine mandates dead-ends one hop in on almost every surface — usually a presentation-layer failure over data the backend already returns.

*Exemplar:* SessionDetailDrawer.tsx:288 — the Trace ID 'link' navigates to /ingestion?tab=timeline with no trace param (the timeline has no trace filter at all); notification-feed.tsx:179-186 repeats the same broken pattern; audit_grouping.py:195-199 builds ?butler=&operation= links that AuditLogPage.tsx:35-43 silently ignores.

*Affected:* dashboard, butlers-roster, butler-detail, sessions, notifications, issues, audit-log, approvals, groups, health, costs, memory, entity-detail, settings, system, ingestion, qa, education, chronicles

### Keyboard-first is doctrine, delivered nowhere

purpose-and-single-pane.md names keyboard-first navigation as in-scope, yet the hottest owner loops — approve/deny triage, session forensics, issue acknowledgment, calendar navigation, ledger scanning — have zero shortcut surface. The shell's command layer is split-brain: Cmd+K opens EntityFinder while the header button and '/' open a different legacy palette; both are noun-only route switchers with no verbs; the shortcut registry is duplicated and drifted (g-h points at a pre-redesign route); modifier chords die whenever focus is in an input; the '?' help sheet has no keyboard binding. Individual pages ship rich but undiscoverable keymaps (entities index) or exactly one binding (calendar). Fragments of excellence exist; nothing is unified or advertised.

*Exemplar:* use-keyboard-shortcuts.ts:18-24 — every shortcut including Cmd+K is dead while focus sits in any input; PageHeader.tsx:129-137 — the search button's tooltip advertises Cmd+K but opens the OTHER palette; ApprovalsPage has no keydown handling anywhere and RailItem suppresses focus outlines without replacement (line 199).

*Affected:* cross:shell-discoverability, approvals, sessions, timeline, ingestion, calendar, issues, audit-log, secrets, memory, dashboard, notifications, entity-detail, education, qa, settings

### Three visual dialects: the language exists but is opt-in and forked

The product visibly speaks three languages — stock shadcn (system-ui body font, shadowed cards, raw Tailwind emerald/amber classes), the Dispatch dialect via shared ui/ primitives, and per-redesign private re-implementations of the same Dispatch atoms already drifting apart (7 Eyebrow definitions, 2 Voices, divergent eyebrow sizes). Chart color plumbing is literally broken app-wide (hsl(var(--X)) wrapping OKLCH tokens produces invalid CSS → black/invisible series in the canonical dark theme), the tuned --chart-1..5 palette has zero consumers, butler hue identity collides (11 butlers over 8 slots, doc and code mappings disagree entirely), 47 files use raw status-palette classes so three greens all mean 'healthy', and single screens (entity-detail, dashboard, calendar) mix both dialects. The doctrine has teeth on paper and no enforcement in code.

*Exemplar:* 9+ recharts components use stroke="hsl(var(--primary))" over oklch tokens — invalid CSS, series render black-on-dark (VolumeTrendChart.tsx:68-73 et al.); `function Eyebrow` is defined 7 times across ui/, three Settings pages, QA, and the secrets passport; ConflictRadarBanner.tsx styles the calendar's most safety-critical banner with CSS variables (--fg-muted, --bg-subtle) that are not defined anywhere.

*Affected:* cross:visual-language, dashboard, butler-detail, issues, approvals, calendar, entities-plex, health, settings, education, system, timeline, notifications, costs, audit-log

### The best patterns were built once and never made policy

Every ingredient of a live, instant console exists in the codebase exactly once and was never generalized: three working WebSocket channels (approvals, spend, console) while every core monitoring surface polls at 15-60s; exactly 2 of ~247 mutation sites are optimistic; placeholderData/keepPreviousData appears 3 times app-wide so pagination and filter changes blank the list the owner was reading; zero prefetching anywhere; zero route-level code splitting despite the lazy-tab pattern existing in ButlerDetailPage; auto-refresh toggles that govern only one of three queries on their own page. The interaction-speed gap is not capability, it is generalization — exceptions instead of defaults.

*Exemplar:* use-approvals-stream.ts:146-152 implements the correct WS→targeted-invalidation pattern, built once and never reused; use-issues.ts is the only optimistic-mutation template among ~247 useMutation sites; SessionsPage's AutoRefreshToggle governs the table while the KPI strip and stripe chart keep polling on their own intervals (SessionsPage.tsx:100,130).

*Affected:* cross:interaction-speed, sessions, timeline, notifications, butlers-roster, dashboard, audit-log, ingestion, groups, issues

### The single pane is fragmented: orphaned routes, duplicate surfaces, dead code

The product contains parallel versions of itself: two Timelines (the redesigned ingestion dispatch ledger vs the abandoned pre-redesign /timeline), two spend surfaces (/costs and /settings/spend) with different vocabularies and zero cross-links, a sessions drawer and /sessions/:id detail page that are split-brain (the permanent URL shows less than the drawer), an operator/resident mode toggle hiding half the butler console, and approvals' standing-rules governance page with zero inbound links anywhere in the product. /costs, /groups, /approvals/rules, /qa/investigations, and all six health sub-pages are unreachable from sidebar and palette (both index only nav-config). Dead components shadow live ones (ActivityFeed, RecentMoments, legacy approvals quartet, SecretsTable trio, the fetching WhatBreaks that was never imported).

*Exemplar:* repo-wide grep shows zero inbound links to /approvals/rules — the record of what the fleet may do unsupervised is invisible; /costs appears in no sidebar section and no palette entry (nav-config.ts:57-93), reachable only via one dashboard widget link; ingestion TimelineTab vs timeline/UnifiedTimeline are two products wearing one word.

*Affected:* timeline, costs, settings, sessions, butler-detail, approvals, groups, qa, health, cross:shell-discoverability, secrets, notifications

### Data without judgment: filing cabinets where briefings should be

Pages present raw counters and chronological tapes where an operator console should lead with synthesized verdicts and urgency ordering: /system opens with version/uptime trivia and never answers 'is my instance OK?'; the roster judges every butler by a flat idle/running binary with no notion of expected cadence (so 'chronicler is 3 days overdue, expected daily' — the most JARVIS-relevant fleet fact — is unexpressible); the approvals queue is arrival-ordered with no risk or expiry weighting; sessions/costs surface no anomalies (failure spikes, cost movers, sessions 10x their median); notifications lead with all-time counters that one bad week poisons forever; education hides its butler's five-phase state machine entirely; butler-detail opens with counters instead of 'what it did, what it needs from you, what's next.' The data for judgment is almost always already fetched — the composition layer is missing.

*Exemplar:* butlers-roster: 'IDLE' means the same thing for a butler that runs hourly and one that runs weekly — no join against the scheduler's cron expectations; SystemPage.tsx:93-111 renders eight equal cards with no aggregate verdict while stale heartbeats sit mid-grid at identical visual weight; ApprovalsPage rail ignores summary.expires_at entirely, so an expiring approval looks identical to a fresh one.

*Affected:* system, butlers-roster, approvals, sessions, costs, notifications, butler-detail, education, issues, ingestion, timeline

### Accessibility assurance is theater; care is real but per-surface and unenforced

The automated a11y gate tests hand-written stub DOM instead of shipped components, so axe can never catch a real regression; there is no eslint-plugin-jsx-a11y; contrast was never measured and demonstrably fails where the design leans hardest (light-theme --amber on --bg at 2.01:1, --dim at 3.49:1 carrying the entire content of 'fading' memory facts at 9-11px mono). The hottest drill-down loop (ingestion ledger row → event drawer) drops keyboard and screen-reader users at both hops: unsemantic div rows with Enter-only activation, a drawer with no focus move-in and no Escape. Shared primitives ship wrong ARIA that propagates (Pill announces filter chips as switches). Meanwhile the calendar grid, sidebar inert handling, and EntityFinder show genuine craft — quality varies wildly per surface because nothing enforces a floor.

*Exemplar:* ButlersPage.a11y.test.tsx:49-124 renders CellStub/ActivityStripeStub 'mirroring' the real DOM — the axe suite structurally cannot catch regressions in the components it claims to cover; TimelineTab.tsx:882-907 — every ledger row is a div with tabIndex and aria-expanded but no role, Space does not activate, and load-bearing reasons live only in hover title attributes.

*Affected:* cross:accessibility, ingestion, memory, audit-log, groups, education, entities-plex, calendar, butlers-roster, sessions, entity-detail

## Ranked moves

### 1. Truth amnesty: purge every fabricated datum and make degraded states structural (medium)

A one-sprint honesty sweep, because trust is the product. Delete the fake dashboard sparkline, the smeared per-day cost stripes, the fabricated 50%/100% mastery badges, the hardcoded secrets sublines/'0ms'/'verified within the hour' voice, the wrong-provenance fallback (candidates[0]), the cents-as-dollars loans, and the regex tool-name guessing — each either renders real data or renders nothing with an honest 'unavailable' note. Simultaneously, establish the structural rule that no query error may ever reach an empty-state branch: a shared three-way state contract (loading / error-with-retry / genuinely-empty) applied to every hook consumer, plus per-source degraded rows following the repo's existing aggregates_available convention. This resolves the two most-cited critical findings across 14+ pages and is the precondition for everything the console claims to be.

*Pages:* dashboard, costs, secrets, education, entity-detail, sessions, approvals, groups, health, memory, butlers-roster, timeline, system, butler-detail

### 2. The drill-down contract: every signal is a door, and trace_id is the spine (large)

Systematically close the Detect→Diagnose loop. Add a trace filter to the ingestion timeline and make both broken trace links (sessions drawer, notifications) land pre-filtered; fix the issues→audit-log param contract; make butler index rows, KPI cells, attention-strip items, heartbeat rows, egress actors, group rows, message threads, and audit request_ids/actors/targets real links (proper <a>/Link elements — cmd-click works, SPA navigation, no full reloads) that carry their full predicate (importance_min, butler, hour, severity) instead of dumping the owner on a generic page. Include the quarantine_reason/quarantined_at surfacing on restore decisions. Most of this is presentation-layer work over data the backends already return — high leverage, wide blast radius.

*Pages:* sessions, notifications, issues, audit-log, dashboard, butlers-roster, butler-detail, system, ingestion, memory, approvals, groups, entity-detail, health, costs, settings

### 3. One language, enforced by machine — starting with the broken chart plumbing today (large)

Day one: repo-wide replace hsl(var(--X)) → var(--X) (9+ charts currently render invalid CSS / black-on-dark series) and add a CI grep banning the pattern. Then make Dispatch the only dialect by construction rather than discipline: flip the body font to var(--font-sans) once; restyle the shadcn primitives themselves (Card→flat hairline surface, instant tooltips, 250ms motion) so unmigrated pages inherit the language for free; collapse the 7 Eyebrow / 2 Voice private forks onto components/ui/ re-exports; route all chart series through --chart-1..5 via one shared helper; extend the butler hue ramp past 8 slots and regenerate the doc mapping from ButlerMark as single source of truth; eslint rules banning raw status-palette classes, local primitive re-declarations, and hex in JSX; merge the byte-identical design-language docs into one canonical file. The chart fix alone is small and critical; the enforcement layer is what stops the fourth dialect from being born.

*Pages:* cross:visual-language, dashboard, butler-detail, issues, approvals, calendar, entities-plex, health, settings, education, system, costs, audit-log

### 4. One command spine: unified palette, single registry, working keyboard floor (medium)

Merge the split-brain palettes into one cmdk surface (EntityFinder's DNA absorbing the legacy palette's page/butler/session search) wired identically to Cmd+K, '/', and the header button; build a single command/route registry (id, label, path, chord, scope) that generates the sidebar, palette index, g-chords, tooltips, and the '?' help sheet — indexing ALL routes so /costs, /groups, /approvals/rules, and the health sub-pages can never be orphaned again; add an Actions group (trigger butler, approve next, acknowledge issue) with a per-page command registration API; fix the keyboard floor (Cmd+K works from inputs, chords shown inline, EntityFinder inside the Dialog primitive). This one move resolves the shell's two critical findings and unlocks per-page keyboard work everywhere else.

*Pages:* cross:shell-discoverability, costs, groups, approvals, health, qa, calendar, secrets, memory, education

### 5. The fleet event bus: live-by-default, polling as safety net (large)

Generalize use-approvals-stream.ts — the correct pattern, already built three times — into a single multiplexed /api/events WebSocket (session started/ended, notification, ingestion event, issue, approval, spend, heartbeat) mapped declaratively to targeted react-query cache patches. Demote all 15-60s polling to a 5-minute reconciliation sweep; make the shell's Live indicator reflect actual socket health and fix the ingestion Live badge to decay on a clock so it can never show stale green. The owner should never watch a butler finish in Telegram before the dashboard notices. This structurally kills the stale-accumulator timeline bug, the per-page auto-refresh incoherence, and the roster's poll-shuffle at the same time.

*Pages:* cross:interaction-speed, dashboard, butlers-roster, sessions, timeline, notifications, ingestion, issues, approvals

### 6. Close the Act loop: signals carry their first remedy inline (large)

Convert the console from detect-only glass to an operator surface, page by page along the hottest queues: approve/deny/defer executable from the dashboard's attention list and fully keyboard-driven on /approvals (j/k, a/d/x, per-item pending state); Restore-with-reason-and-undo on the roster; acknowledge-until-recurrence (not dismiss-forever) plus 'run schedule now'/'ping butler' on issues; retry-consolidation and retire-rule on memory's rail; inline retry/scoped-ack on failed notifications; 'trigger tick' on stale butlers from /system; episode corrections on chronicles (a manifesto-binding promise); log-interaction/gift-idea/draft-reach-out verbs on entity-detail and the plex. Every action optimistic where reversible, undo-toast over confirm, verb-labeled. This is what turns 'a JARVIS that can only point' into one that acts.

*Pages:* dashboard, approvals, butlers-roster, issues, memory, notifications, system, chronicles, entity-detail, entities-plex, health, butler-detail

### 7. One Timeline: rebuild /timeline on the ingestion ledger and fix its broken record (medium)

The product already contains the JARVIS-grade version of this surface — the bu-4utdw dispatch ledger. Rebuild /timeline on that component system (hour groups, hairline rows, URL-backed drawer, saved views, live tail with 'N new events' pill) with source facets for sessions/notifications/errors, and fix the trust-breaking backend defects in the same stroke: event_type filtering pushed into SQL (the error filter currently under-reports and kills pagination), composite (timestamp,id) keyset cursor (same-second events currently vanish), server-side heartbeat classification via trigger_source (the substring sniff currently swallows real owner events), correct rollup copy, and a per-source degraded flag in the envelope. Delete UnifiedTimeline and the dead ActivityFeed. One word, one surface.

*Pages:* timeline, ingestion, notifications, sessions

### 8. One Spend surface: merge /costs into /settings/spend, lead with posture (medium)

Two disconnected spend surfaces with different vocabularies fragment the owner's money question. Merge into a single nav-visible 'Spend' page in the Dispatch language: posture first (MTD vs ceiling meter, projected EOM, live burn from the existing WS stream), honest per-butler-per-day stacked chart (extend /api/spend/daily to keep the butler identity it currently computes and discards), a ranked movers strip (deltas vs trailing baseline), and an on-page evidence layer — window-scoped top sessions and by-schedule costs, every bar and butler row drilling through. Kill the dead scrubber, the fabricated stripes, and the silent-zero failure modes as part of the merge; fix the two adjacent contradictory 'MTD' figures on the settings console. Registers in sidebar and palette via move 4.

*Pages:* costs, settings, dashboard, sessions

### 9. One Trust Console: approvals + standing rules + URLs for every decision (medium)

Merge the orphaned /approvals/rules into /approvals as an always-visible Autonomy panel (per butler × tool trust spectrum with live use counts and inline revoke); give every approval a URL (/approvals/:id) so notifications can land the owner on the decision; rank the queue by expiry and blast radius instead of arrival; wire the dossier to originating session/trace evidence; make history rows open read-only dossiers; render approved-but-never-dispatched in amber, never success-green; and let decisions teach ('approve — and always allow this shape?' inline). This is the surface where autonomy is either governed or quietly corrupted — today the governance half is literally unreachable.

*Pages:* approvals, sessions, notifications, dashboard

### 10. Make the exceptions policy: optimistic mutations, never-blank lists, prefetched drill-downs (medium)

Three mechanical sweeps that generalize patterns the codebase already contains exactly once each: (1) extract use-issues.ts's onMutate/rollback into a shared useOptimisticMutation and sweep all ~247 mutation sites, classifying each as optimistic (toggles, acks, dismissals) or honest-pending (trigger, replay, secrets); (2) enforce placeholderData:(prev)=>prev plus an isFetching dim overlay on every cursor/filter-keyed list query so pagination never blanks what the owner was reading; (3) prefetch detail queries on row hover/focus and seed drawers from the cached list row so drill-downs open populated. Add AbortSignal + 15s timeout to apiFetch and lazy-load the heavy leaf routes (@xyflow, calendar, chronicles). Debounce the keystroke-fires-a-query filter inputs (sessions, notifications, audit-log) in the same pass.

*Pages:* cross:interaction-speed, sessions, timeline, notifications, audit-log, groups, issues, butlers-roster, entities-plex

### 11. Accessibility floor: real tests, contrast-locked tokens, one operator row grammar (medium)

Burn the fake assurance layer: delete the stub-DOM a11y tests and run axe against the real routed pages; add eslint-plugin-jsx-a11y to CI; add a pure-math token-contrast unit test (oklch→sRGB→WCAG, both themes) and fix the verified failures (darken light --dim, mint a text-safe amber, readable 'fading' facts). Ship DisclosureRow/RowLink primitives (role, Enter+Space, aria-expanded/controls, focus ring, ≥24px targets) and recompose the ingestion ledger, memory registers, audit-log rows, and status board onto them — one fix, four surfaces, enforced for every future ledger. Give the event drawer real focus choreography (focus-in, Escape-out, announcement), rebuild CommandPalette semantics via move 4, and add the single global prefers-reduced-motion rule.

*Pages:* cross:accessibility, ingestion, memory, audit-log, butlers-roster, groups, education, entities-plex, cross:visual-language

### 12. Judgment layer: verdict lines and cadence-aware health on the monitoring core (large)

Give the three fleet-health surfaces the synthesis they're missing, using data already fetched: /system opens with a computed verdict banner ('Instance healthy: v0.4.2, up 12d, backed up 3h ago, all 9 beating') or a ranked problem list, with one canonical liveness model shared by the topology graph and every list (they currently disagree); the roster joins the scheduler's cron expectations so the board can say 'silent 3 days, expected daily' instead of a flat IDLE, freezes its poll-shuffling sort into stable roster order with a needs-you strip on top, and collapses its 2N-query fan-out into one GET /api/butlers/board; sessions pins running (ticking elapsed) and recent failures (inline error excerpts) above the chronological flow with cost as a first-class dollar column. Deterministic composition — no LLM cost.

*Pages:* system, butlers-roster, sessions, costs, notifications, butler-detail

### 13. One butler console: kill the mode toggle, unify the run verbs (medium)

Collapse butler-detail's resident/operator split into one tab set (Overview, Activity, Approvals, Spend, Memory, domain tab, System) and delete the mode toggle, its auto-promotion machinery, and the dead CRM base tab; unify Force Run / Trigger / Prompt — three names for one concept — into a single prompt-first command bar; fix the approvals KPI to use meta.total instead of the page-size cap; replace the header's port/uptime trivia with 'last run · next scheduled · cost today'; and make every Overview signal a door (events → session drawer, stripe bars → filtered sessions, approvals → deep link). The most-visited detail surface in the product currently requires knowing a mode vocabulary before the right controls exist on screen.

*Pages:* butler-detail, sessions, approvals

### 14. Orphan and cruft purge: retire the vestiges, delete the dead code (small)

Aligned with the repo's own prefer-cruft-cleanup doctrine: retire standalone /groups into an entities 'Circles' lens (wired to the existing unused getGroup endpoint); fold /qa/investigations into /qa with URL-persisted filters and link patrols from the overview; surface the six health sub-pages as a ledger index + sidebar children; delete the dead components shadowing live surfaces (ActivityFeed, RecentMoments, NextList, the legacy approvals quartet, SecretsTable/modal trio, MOCK_INVENTORY barrel export, the flag-off legacy ingestion pages) and wire the one dead component that should be alive (the fetching WhatBreaks). Regenerate the stale IA doc from nav-config so the navigation contract tracks reality. Small effort, and it stops every future audit from re-finding the same ghosts.

*Pages:* groups, qa, health, secrets, approvals, timeline, ingestion, dashboard, cross:shell-discoverability
