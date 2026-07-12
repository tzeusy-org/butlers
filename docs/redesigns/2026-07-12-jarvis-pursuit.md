# JARVIS Pursuit — 2026-07-12

Fourth run of the relentless JARVIS pursuit (skill: `.claude/skills/butler-relentless-jarvis-pursuit/`),
two days after the 2026-07-10 program (15 epics, 63 children, gate `bu-oxf6d`) was released and the fleet
merged ~115 commits against it. Per the skill's post-release protocol this run led with QC: 32 subagents
(workflow `wf_f72488fa-313`) — **4 QC graders scoring 46 landings from the 07-10 program** (live dev stack,
read-only verification) + 18 page surfaces + 4 cross-cutting design sweeps + 5 ecosystem lenses (connectors,
inference, knowledge graph, collaboration, owner interfaces — the latter four first-ever audits) + synthesis.
Deduped against all three prior dossiers, every open bead (five 07-10 epics still in-flight had their whole
scope fenced), and the 115 fresh merges. Backward compatibility waived.

**Full per-agent dossiers** (verdicts, JARVIS gaps, ideal designs, findings with file:line evidence and
live/source/inferred confidence, QC landing grades, ecosystem proposals) live in
[`2026-07-12-jarvis-pursuit-data.json`](2026-07-12-jarvis-pursuit-data.json) — query one agent with
`jq '.audits[] | select(.page=="<key>")' <file>` (QC agents use keys `qc:*` and carry a `landings` array;
ecosystem lenses `eco:*`, sweeps `cross:*`; the synthesis object carries the tier board, themes, ranked
moves, and the full dropped ledger).

## Headline

The fleet's two-day execution of the 07-10 program graded remarkably clean — 34 of 46 landings
as-designed, 12 with-gaps, zero regressed or missing — and six surfaces moved up a tier on verified
mechanisms (sessions, notifications, spend, memory, chronicles, qa-system; proactivity-quality climbed
weak→functional on 18 live-confirmed deliveries). But the run's defining discovery inverts the celebration:
**the live stack is serving a frozen 2026-07-11 04:22Z tmp-branch worktree** — everything merged since,
including the tri-state breaker UI and the entire /decisions lane, is dark to the owner, and the drift
sentinel built two days ago to catch exactly this reports `is_drifted=false` because it compares the
running image against itself. The with-gaps grades all share one shape, the successor to last run's
"backend-only honesty flags": *mechanism real, last mile missing* — the deploy ledger has zero readers,
the decision linter validates an empty label set, the restore drill structurally cannot fire, the infra_state
QA source was never enabled. The four first-ever ecosystem lenses all grade weak-to-functional with the same
diagnosis at different layers: **trust plumbing exists, trust traffic is zero** — the context bus has zero
rows in 3.5 months against three hardened consumers, the delegation ledger has never carried a message,
autonomy promotion is mathematically unreachable under exact-args fingerprints, and a revoked Codex token
killed three model tiers for hours behind green badges with verified alternates one priority slot away,
never attempted. The ranked list opens with the redeploy (everything else is invisible until it lands),
then closes the model-selection loop, restores cross-container notification delivery, and repairs the
audit drill spine that 500s under live load.

## QC verdict on the 07-10 program

Across four QC graders, 46 landings from the 07-10 program were graded: 34 landed-as-designed (74%), 12 landed-with-gaps, and 0 failed, regressed, or went missing — an unusually clean shipping record for ~115 commits in two days. The honesty batch is the standout (16/17 as-designed, several live-confirmed against real degraded production data — memory's pools_failed note is being exercised by an actual chronicler pool failure right now), and the QA breaker-truth batch landed 4/4 with the ledger live-verified. The worst landing by far is the deploy spine (bu-9r3hd): all five slices graded landed-with-gaps in the identical shape — mechanism real, last mile missing. The drift sentinel is blind to the stale-image failure mode that spawned it; the deployments ledger has zero frontend consumers and records an arbitrary (memory-chain) migration head; `butlers deploy` has never been used, the live redeploy having been done via the artisanal ceremony it was built to retire; the infra_state QA source was never added to the roster's enabled_sources; and the weekly restore drill is a sleep-first 7-day in-process loop that structurally cannot fire on this restart cadence. Second worst: the decisions lane (bu-ckkpz) — convention, linter, endpoint, page, and cron all real, all inert (zero labeled beads, vacuous linter, no beads mount in the dashboard-api container). bu-10fgt.4's faketime leg detected ≥3 real +45d time-bombs on its first run, then hung to GitHub's 6h kill on both legs, destroying its own evidence nightly. The meta-finding overshadowing all grades: the live stack is pinned to a frozen 07-11 04:22Z tmp-branch worktree, so a large fraction of what was graded 'landed' is not actually serving the owner, and no shipped surface says so.

## North star

Unchanged from the 07-03 dossier (§North star) as extended by 07-10: five-second fleet
verification, earned calm (nothing fabricated, failure never impersonates health), every clause a door on
an unbroken trace spine, keyboard-first, one instrument built by one hand; plus the th-engineering bar
(readable hot paths, honest rigorous tests, one-way boundaries, zero cruft) and the th-projects mandates
(specs as planning source of truth; every lifecycle closed: merged → archived → **deployed** → decided —
this run proved the bolded step is the one that breaks).

## Tier board (movement vs 2026-07-10 baseline)

- **dashboard** — solid — improved — j/k+a/d/x/u triage, inline undo-grace decisions, and per-source error rows verified (source-confirmed). Approvals remains the only voiceless source (failure renders 'Nothing waiting.' over a live 36-deep queue) and the cost band still fabricates $0.00 on source death.
- **butlers-roster** — solid — unchanged — No regressions; standing knowns intact. New: header pill/footer compute 'healthy' by a different formula than the needs-you strip (overdue/unknown count as healthy), and owner Pause is stored+rendered as a reasonless red QUARANTINE.
- **butler-detail** — solid — unchanged — Overview 'review' door is dead (links ?butler=&id= which /approvals never reads — lands on the wrong dossier); parallel status vocabulary and ??0 spend fallbacks persist.
- **sessions** — solid — improved — functional→solid: bu-tpudw.2 live-verified (404-vs-503 pool naming, degraded KPI strip + verdict-opener suppression). New: From=To date filter live-returns 0 of a 97-session day; list-level sources_degraded has zero pixel readers.
- **timeline** — solid — unchanged — No timeline work landed this window; prior mechanisms verified intact. 30/50 live head-page summaries are machine text (XML fences, skill preambles, a QA system prompt); failed notification deliveries invisible to the Errors lens.
- **notifications** — solid — improved — functional→solid: bu-jad4j.2 verified (em-dash tiles, named degraded feed, verdict fold-in). New: acknowledging a failure rewrites history (status overwritten to 'read', retro-greening the failure rate); optimistic ack is visually inert.
- **issues-audit** — functional — unchanged — Degraded-note mechanisms verified FE+BE, but the drill spine breaks under live load: 349k poisoned metadata rows 500 audit pages, top group's occurrences 400/404 (key drift), and two distinct groups share one issue_key so acks cross-contaminate.
- **approvals-decisions** — solid — improved — Approvals mature. /decisions is a genuinely new pixel surface born keyboard-first and degraded-honest — but source-confirmed only (live stack predates the merge) and its data spine is severed: no beads mount in dashboard-api, zero decision-label adoption, title-regex queue wrong in both directions.
- **calendar** — solid — unchanged — All four 07-10 merges verified wired (people linking, durable undo, entry fetch, conflicts.available). Offset by: conflict radar emits 12 phantom overlaps from un-collapsed duplicates (live), and provider sync silently dead since Apr 7 with zero grid-level staleness signal.
- **health** — functional — unchanged — Edge honesty landed (overview degraded notes, tracker boundaries, owner-tz meals), but the freshness spine is structurally dead (sources endpoint reads a metadata key no writer emits; week-old vitals render as current) and ~half of live readings are unchartable by the hardcoded type vocabulary.
- **spend** — solid — improved — functional→solid: degraded footnotes live end-to-end (meta.unavailable_butlers rendered on all four surfaces). New upstream defects: switchboard is a permanent misclassified false alarm, and by-schedule splits schedules per-model, misranking burn and duplicating React keys.
- **memory** — solid — improved — Prior regression cleared: pools_failed verified against a live degraded envelope (chronicler failing now) and bu-mkd5r three-way states across all bands. Fan-out honesty is still /stats-only (registers/search/detail silently drop pools) and the detail trio fabricates 'not in the ledger' on error.
- **entities** — functional — unchanged — bu-hckjv supplementary degraded sections verified on detail. Plex 'Worth attention' rail contradicts the butler's compute_urgency engine on every tier; the retired /relationship/contacts* family still powers a reachable tab that has 404'd for ~3 weeks.
- **circles** — weak — regressed — Live-confirmed hard-broken: FE requests limit=500 against backend le=200 → HTTP 422 on every load; unit tests fully mock the hook. Supersedes the '500-row truncation' known — likely long-broken and previously misdiagnosed.
- **settings-secrets** — solid — unchanged — Landed mechanisms verified (HeaderCounts nulls, secrets degraded paths). Newly graded corners leak: model verification is 23 days stale yet silently governs routing exclusion with green badges; the 'privileged' audit reel is 2/3 llm_api_call noise.
- **education** — functional — unchanged — Prior headline defect fixed (error no longer impersonates 'No curriculums'), but the owner's only curriculum has been dead mid-build for 21 days while the UI says 'the butler is still building it'; 5 of 7 child components still map error→calm; pre-Dispatch dialect.
- **chronicles** — solid — improved — functional→solid: IEA day surface verified wired live (ribbon→drawer evidence chain, balance/trends/companions endpoints with degraded flags). Seam defects remain: 1904 birthday row poisons the archive bound, ManualRefresh invalidates dead query keys, three legacy components render error as empty day.
- **qa-system** — solid — improved — functional→solid: full bu-533qx breaker-truth batch verified live+source (tri-state, evidence-bearing reset, session doors, honest toast). Remaining: failed investigations impersonate in-flight 'detect', MTTR improves as the staffer crashes faster, and the watcher cannot see its own death or revoked runtime token.
- **ingestion** — solid — improved — bu-lkzsf failed-status live-confirmed end-to-end; soft-archive live on the roster. Timeline attention strip is a stale fork resurrecting deliberately-archived identities; archive is one-way in pixels; four audit-hardened lifecycle endpoints have zero reachable pixels.
- **system** — solid — new — First dedicated audit. Drift/backup/liveness mechanisms real and honest about themselves, but the all-clear is structurally unreachable (static dev-posture + never-firing restore drill), the deployments ledger has zero consumers behind a frozen v0.1.0, and the egress catalog is 99.1% internal noise labeled external.
- **cross:shell-discoverability** — solid — improved — Registries adopted, not drifted: /decisions born keyboard-first, chronicles verbs registered. One latent major: the persistent non-modal chat dialog silently suspends every page-scoped shortcut app-wide while open, with the '?' sheet still advertising dead bindings.
- **cross:visual-language** — solid — improved — Verified movement: var() fallbacks 165→134, focus-visible 2→53 files, skeleton-pulse eliminated. But the newest flagship surface shipped a 9-hex non-theming lane palette through the .ts hex-guard blind spot, and new components dodge the state-fill rule via bg-[var(--red)].
- **cross:interaction-speed** — solid — unchanged — 07-10 mechanisms held and matured, but the newest surfaces shipped off-pattern: /decisions and the attention-ledger panel have no update path at all (neither bus nor poll), and the IEA day-stepper flashes a fabricated 'No activity recorded' on every navigation.
- **cross:accessibility** — functional — improved — Culture landing: Tip primitive, jsx-a11y gate, DecisionsPage born with axe tests and roving focus. Still functional: the Chronicles Gantt (day-story centerpiece) is invisible to AT and pointer-only, title= regressed one day after the sweep, and the first new autocomplete shipped without combobox semantics.
- **eng:test-rigor** — solid — unchanged — bu-10fgt.1/.2 + watchdog + owner-tz test batches verified landed. The faketime leg found ≥3 real +45d time-bombs on its first run then destroyed the signal hanging to the 6h kill; the migration-collision guard is detection-only and the race recurred within this fleet run.
- **eng:readability / eng:boundaries / eng:cruft / eng:docs** — functional (boundaries weak) — unchanged — Not audited this run; carryover from 07-10 baseline.
- **proj:shape / proj:reconcile / proj:direction** — solid — unchanged — bu-os64u.1-.4 (openspec sweep, epic-close clause, v1-status refresh, ideas ledger) and bu-1mq1d prompt unification verified landed-as-designed. Open shape defect: the decision-bead convention has zero adopters and its linter validates the empty set.
- **eco:reliability** — weak — unchanged — Deploy spine shipped but all five slices landed-with-gaps, and the live stack is frozen on a non-main tmp worktree with every post-07-11 04:22Z merge dark — the exact failure class the spine was built to catch, undetected by it.
- **eco:proactivity-quality** — functional — improved — weak→functional: deliver() fixes live-confirmed (18 real deliveries incl. post-deploy), silent branches now write honest ledger rows, Trust Console panel live and correctly flagging a genuine 7-day failure. Ceiling: api-container delivery transport is structurally dead and the flagged failure has no remediation path.
- **eco:data-quality** — functional — unchanged — Not directly re-audited; knowledge-graph lens shows decay lacks any reinforcement input (21/4,017 facts ever confirmed) and 3.4% edge density fleet-wide.
- **eco:inference** — weak — new — First audit of the trigger→spawn loop: a revoked Codex token killed the top workhorse/reasoning/specialty tiers for hours with green badges (29 default-closed failovers, verified alternates one slot away, never tried); catalog selection is open-loop; classification runs 28x outside its own <5s p99 SLA at ~29K tokens per routing decision.
- **eco:collaboration** — weak — new — Two of four cross-butler channels have carried zero messages ever: context bus 0 rows in 3.5 months against three hardened consumers; delegation ledger 0 rows with the tool absent from every roster prompt. Insight broker hears 3 of 9 domain voices; chronicler absent from the briefing chorus.
- **eco:knowledge-graph** — weak — new — Trust never compounds: exact-args fingerprints make autonomy promotion mathematically unreachable (95 approvals → 53 fingerprints, max 4 < threshold 5, zero suggestions ever); rule-maturity ladder has 2 candidates and no structural consumer; graph is a star (3.4% edges).
- **eco:connectors** — functional — new — 07-10 roadmap is strong supply-side work but derived from volume skew, not manifesto promises: travel promises pre-announcement flight alerts with zero capture substrate; Gmail already captures bank e-statement PDFs the roadmap routes through a manual drop-folder; ActivityWatch is structurally single-device.
- **eco:interfaces** — functional — new — The owner's primary channel (Telegram) is a one-way, doorless, text-only pipe: voice/photo content-dropped at ingress despite deployed transcription infra, no door back to the trace spine, no deterministic command lane, and the attention ledger stamps delivery failures 'deferred' (live-confirmed) with no retry.

## Systemic themes

### The live instrument went dark mid-program — and the deploy spine built to catch it can't see it

Both live-stack containers bind-mount a disposable integration worktree whose HEAD is not an ancestor of main, frozen at 2026-07-11 04:22Z. Everything merged since — the tri-state breaker UI, the entire /decisions lane, honest-fan-out slices — is dark to the owner, and the drift sentinel reports is_drifted=false throughout because it compares the running image's own migration chains, not main. Every deploy-spine guarantee stops one step short on this topology: ledger unread, drill unfireable, verb unused, discovery source unenabled. The fleet's velocity is currently invisible, and grading 'landed' vs 'serving' has diverged.

*Exemplars:* docker inspect: mounts /home/tze/gt/butlers/.worktrees/compose-latest-integration-20260711 (HEAD 213664055); live GET /api/decisions → 404 (merged PR #3142); deriveBreakerState absent from serving worktree; restore_drill {checked_at:null, result:'pending'}; /api/qa/summary active_sources lacks infra_state

*Affected:* every surface; acutely system, decisions, qa, eco:reliability

### Mechanism real, last mile missing — the run's signature landing gap

All 12 landed-with-gaps grades share one shape: the mechanism ships complete and tested, but the activation step — a roster line, a compose mount, a frontend consumer, a schedule anchor, a label backfill — goes unowned, so the mechanism runs against nothing or reports to no one. This is the successor pathology to last cycle's 'backend-only honesty flags': the fleet has learned to build the whole organ but not to plug it in.

*Exemplars:* GET /api/system/deployments: zero frontend consumers; lint_decision_beads: bd list --label decision → [] (lints the empty set); dashboard-api has no .beads/issues.export.jsonl mount; restore drill sleep-first 7-day loop in a daily-restarting process; faketime leg concludes 'cancelled' not 'failure', suppressing its own alert channel; infra_state source implemented, never enabled

*Affected:* bu-9r3hd (all 5), bu-ckkpz (3 of 4), bu-10fgt.4, eco:collaboration (context bus consumers with no producers)

### Honesty reached the perimeter, not the organs — and the first false alarms now flow through honest pipes

The degraded-flag program verifiably landed and is live-firing, but each surface's core organ still fabricates: the dashboard cost band renders a bold $0.00 on source death, the sessions table shows a partial fleet as complete history under a verdict that names the degraded pool, memory's detail trio asserts 'not in the ledger' on fetch error, education children map error to calm, QA detail routes render outages as 'not found'. Meanwhile fabrication's other direction shipped at fleet scale: /spend permanently names healthy butlers 'unavailable' (classify-before-flagging violated upstream of faithful renderers), the egress catalog presents 1.2M internal actions as an external actor, and an owner Pause renders as a fleet emergency. Both fabricated calm and fabricated alarm erode the same earned trust.

*Exemplars:* CostWidget: no error prop on the headline dollar figure; meta.unavailable_butlers=['messenger','qa','switchboard'] while /api/butlers shows all ok; FactDetailPage drops isError → 'not in the ledger'; egress actors[0]='Other / Unrecognized' 1,197,271 calls; Pause → quarantined with reason NULL

*Affected:* dashboard, sessions, memory, notifications, education, qa, spend, system, butlers-roster

### Cross-container topology silently severs owner loops

Multiple owner-facing loops assume everything runs in one process and break at the container boundary: deliver() resolves butler endpoints to loopback URLs valid only inside butlers-up, so every api-container notification push fails (164 attempts / 0 delivered over 7 days for a broken credential); the decisions endpoint reads a beads export no api container mounts; dashboard chat ingress is unresolvable as owner identity so the disengagement ratchet is blind to the owner's richest channel; and the attention ledger stamps these transport failures 'deferred' — a benign hold that never retries — so the trust surface itself can't distinguish chosen silence from a dead pipe.

*Exemplars:* attention ledger: secrets_lifecycle_check delivered:0 suppressed:143 deferred:21, reason 'delivery_error:...localhost:41104/mcp'; docker-compose: beads mount only on butlers-up services; identity.py has no 'dashboard' channel key; Outcome literal has no 'failed'

*Affected:* proactivity spine, decisions desk, trust console, notifications, eco:interfaces

### Stale data wearing current-data authority

A cluster of surfaces present old state with full confidence and no age signal: the calendar grid renders a 96-day-old snapshot (provider sync silently dead since Apr 7, no error, no escalation, no plaque); model verification is 23 days stale yet silently governs routing exclusion behind ageless green badges; health KPIs show week-old vitals as current above a structurally dead freshness endpoint; education narrates a 21-day-dead curriculum build as 'the butler is still building it'; the chronicle claims 122 years of navigable history off one 1904 calendar row; /system answers 'what code is running' with a frozen v0.1.0. The common fix is the same everywhere: every asserted fact carries its age, and every feeder has a deadman.

*Exemplars:* all 10 calendar source_freshness rows stale since 2026-03-30..04-07; model_catalog last_verified_at=2026-06-19 across 31 entries; /measurements/sources → [] against 206 facts; earliest_date 1904-10-18 with a fabricated 17-hour day; MindMapGraph 'still building it' at 21 days

*Affected:* calendar, settings-models, health, education, chronicles, system

### Trust plumbing exists; trust traffic is zero

The ecosystem lenses converge on one diagnosis: every organ of a self-trusting system has been built — context bus with vocabulary and permissions, delegation tools with a ledger, autonomy fingerprints, a rule-maturity ladder, reference counters on every fact — and none of it carries traffic. The bus has zero rows ever against three hardened consumers; delegate_ask has never been invoked and appears in no roster prompt; 95 real approvals produced zero promotion suggestions because exact-args hashing can never accumulate; recall usage is recorded on every hit and ignored by the decay formula. The vision's core success criterion — progressively earned autonomy — currently has no working mechanism, only disconnected parts.

*Exemplars:* public.user_context 0 rows / set_context 0 call sites; public.delegation_ledger 0 rows; max fingerprint count 4 < threshold 5; rules: 2 candidates, 0 consumers, rule_applications already dropped as write-orphaned; 21/4,017 facts ever confirmed

*Affected:* eco:collaboration, eco:knowledge-graph, eco:proactivity-quality, vision.md success criteria

## Ranked moves

### 1. Repoint the live stack at main and finish the deploy spine's last mile (engineering, M)

**What:** Redeploy from origin/main using the shipped-but-never-used `butlers deploy`; forbid serving from .worktrees/. Add a Deployment card on /system consuming GET /api/system/deployments (zero consumers today) with a 'serving <sha>, N commits behind origin/main' red clause; record the core migration head instead of the LIMIT-1 arbitrary row. One-line roster fix adding 'infra_state' to qa enabled_sources. Make the restore drill due-time persistent (read last drill from audit_log on boot + hourly, run when >7d overdue). Close/supersede bu-zhfd0 with the redeploy-from-main decision.

**Why:** The owner's live instrument is frozen at a 07-11 04:22Z tmp-branch worktree — 16+ merges dark including the breaker UI and the entire /decisions lane — and no surface says so; the drift sentinel is blind to exactly this mode. Every other move on this list is invisible until this lands. Live production breakage outranks everything.

**Evidence:** qc:qa-deploy, live-confirmed: docker mounts .worktrees/compose-latest-integration-20260711 (HEAD 213664055, not an ancestor of main); GET /api/decisions → 404; deriveBreakerState absent from the serving worktree; restore_drill pending forever; active_sources lacks infra_state; deployments ledger migration_head='mem_007'. Supersedes known bead bu-zhfd0 (de facto resolved — schemas at core_165, drift clean).

**Slices:** 1) Redeploy from main via `butlers deploy` + kill the worktree mount; 2) infra_state roster line + drill due-time anchor (backup_health.py sleep-first loop); 3) Deployment card + behind-main clause + core-head recording; 4) close bu-zhfd0 as superseded by the redeploy decision.

### 2. Close the model-selection loop: failover auth vocabulary, stderr matching, catalog breaker, hourly verify (engineering, M)

**What:** Add revoked/refresh-token markers to _PROVIDER_AUTH_MARKERS and a bounded stderr-matching gate for pre-tool-call failures (default-closed contract preserved). Then: a dispatch-outcome circuit breaker on model_catalog entries (N consecutive systemic failures excludes; half-open probe restores) and an hourly automated verify-all sweep so last_verified_ok is never >1h stale; Models tab shows verification age, stored error text, and the routing consequence.

**Why:** One revoked Codex OAuth token silently disabled the workhorse, reasoning, and specialty primaries for hours — health ingestion, reminders, and weekly summaries died while the Models tab showed green and verified same-tier alternates sat one priority slot away, never attempted. Failure impersonating health at the routing layer, live right now, and structurally recurrent until selection becomes closed-loop.

**Evidence:** eco:inference, live-confirmed: session b03d3af4 'refresh token was revoked'; model_dispatch_attempts: 29 suppressed failovers 'default-closed' in 48h; gpt-5.6-luna last_verified_ok=t at top priority despite 20+ consecutive failures; verify-all is manual-only (model_settings.py:617). settings-secrets auditor: all 31 entries last_verified_at=2026-06-19 (23d stale), failure reasons log-only.

**Slices:** 1) Markers + regression test built from the exact live error string (S — ship first); 2) opt-in stderr gate with marker-disjointness tests; 3) breaker state + resolver exclusion + half-open probe + attention-ledger push on open; 4) hourly verify cron + Models-tab age/error/consequence pixels (folds the settings auditor's verification-truth move).

### 3. Reachable delivery for api-container notifications + a real 'failed' outcome in the attention ledger (engineering, M)

**What:** Give deliver() callers running in dashboard-api a transport that works across containers — container-DNS butler endpoints (http://butlers-up:41104) or a switchboard-owned delivery queue table the daemon drains (coordinate direction with in-flight bu-01r64, which covers the daemon→api inverse only). Add 'failed' to the attention-ledger Outcome vocabulary, stamp it on delivery_error/no_recipient instead of 'deferred', and route retryable failures through the existing deferred_notifications flusher so they actually retry.

**Why:** secrets_lifecycle made 164 delivery attempts over 7 days with 0 delivered — a broken SPOTIFY credential silently un-notified because deliver() resolves messenger to a loopback URL valid only inside butlers-up. The Trust Console correctly flags it red, but there is no remediation path, and the ledger stamps these failures 'deferred' — a benign chosen hold that never retries — in the exact surface built to prove silence is chosen.

**Evidence:** qc:proactivity-decisions, live-confirmed: ledger summary secrets_lifecycle_check delivered:0/suppressed:143/deferred:21 with reason 'delivery_error:...localhost:41104/mcp'; general-butler deliveries succeeded the same hours (caller's container, not telegram). eco:interfaces, live-confirmed: row ee590ef0 outcome='deferred' with delivery_error and notification_ref=null (no retry envelope exists). Same root cause renders as 21 identical traceless failed rows on /notifications.

**Slices:** 1) 'failed' outcome + caller migration + Trust Console failed column (S, honest labeling first); 2) retryable failed → deferred_notifications envelope so the scheduler flush redelivers; 3) transport fix: container-DNS registry endpoints or daemon-drained queue (coordinate with bu-01r64's transport work).

### 4. Repair the audit/issues drill spine: normalize 349k poisoned rows, window-independent group identity (engineering, M)

**What:** Tolerant AuditLogEntry.from_record (json.loads fallback for string-typed metadata) plus a one-shot batched repair migration normalizing the jsonb_typeof='string' band; re-key audit issue groups on a hash of the full normalized error_summary (drop the 80-char slug truncation and the window-dependent butler-set component); make the occurrences endpoint accept the feed's window, apply the same cap, and render 'showing 50 of N'.

**Why:** The accountability surface is broken live: typing 'memory' in the audit page's own Actor filter 500s the whole table, the top issue group in today's feed cannot be drilled (400), the second 404s on key drift, and two distinct groups share one issue_key so acknowledging one silently acks both — all while the UI misclassifies deterministic data poisoning as 'temporarily unavailable, try again shortly'.

**Evidence:** issues-audit auditor, live-confirmed: 349,113 string-typed metadata rows (29% of public.audit_log, 2026-06-14→07-05, write path fixed but never repaired); GET /api/audit-log?actor=memory → VALIDATION_ERROR; occurrences 404 via '::switchboard' vs '::multiple' drift; identical key 'runtimeerror-codex-cli-…::multiple' on two rows in one payload (166 vs 2,860 occurrences).

**Slices:** 1) Tolerant deserialization + contract test seeding all three metadata typeofs (unblocks every page immediately); 2) batched repair migration — serialize the core_NNN number against the parallel-collision hazard; 3) hash-keyed compute_issue_key + windowed/capped occurrences + total/load-more in the FE.

### 5. Un-break /entities/circles and gate FE params against the backend OpenAPI contract (engineering, S)

**What:** Fix the limit mismatch (FETCH_LIMIT 500 → 200, or raise le) with a truncation footnote; add a contract test that every api/client.ts path and query param satisfies the live FastAPI OpenAPI schema (which also catches the dead /relationship/contacts* family still wired into a reachable tab); minimal circles e2e.

**Why:** A primary route 422s on every single load and unit tests fully mock the hook, so the FE↔BE contract break is structurally invisible — the same class that left a whole butler-detail tab rendering only error lines against deleted routes for three weeks. One-line fix plus a gate that retires the class permanently.

**Evidence:** entities auditor, live-confirmed: GET /api/relationship/groups?limit=500 → HTTP 422 (le:200); CirclesPage.tsx:61 vs router.py:555; live GET /api/relationship/contacts → 404 with client fns/hooks/ButlerRelationshipContactsTab still consuming it.

**Slices:** 1) One-line limit fix + truncation note + circles e2e; 2) OpenAPI-vs-client contract test in the bu-tpudw.5 contract-test harness; 3) flag the dead contacts family into the deferred contact-era excision cluster.

### 6. Activate the Decision Desk: beads mount, label backfill, non-vacuous linter (governance, S)

**What:** Mount .beads/issues.export.jsonl (ro) into dashboard-api/-hotreload and make the deploy flow materialize the export in whatever directory compose runs from; bd-update the six live owner-decision beads with the decision label + metadata.decision + due; extend lint_decision_beads to FAIL open beads whose titles match decision markers but lack the label, and run it inside the weekly decision_review job via --issues-json-file; pass export mtime through as meta.export_as_of with an as-of plaque.

**Why:** Every shipped piece of the decision loop is structurally inert: /api/decisions will be permanently decisions_available=false in the containerized topology, zero of 5,666 beads carry the label so the linter validates the empty set forever, and the title-regex queue is wrong in both directions (lists the Decision Desk epic itself as an open decision; misses two open P1 owner decisions). One backfill plus one mount turns three landed deliverables into a working pipeline and gives in-flight bu-97qrw/bu-a9p6y actual fields to read — their missing prerequisite, not their scope.

**Evidence:** qc:proactivity-decisions + approvals-decisions auditor: docker-compose beads mount exists only on butlers-up services; bd list --label decision → []; live regex reproduction — bu-ckkpz matches as false positive, bu-w6jca/bu-4pq0s invisible; lint output 'clean — 0 decision bead(s) checked'.

**Slices:** 1) Compose mounts + deploy-flow export materialization; 2) label/metadata backfill on the six seed beads; 3) unlabeled-marker lint mode + weekly-job invocation writing a flagged attention row on violations; 4) export_as_of meta + plaque.

### 7. Spend truth: classify absent tools as absent; re-aggregate by-schedule across models (engineering, S)

**What:** In spend.py's MCP fan-out helpers, treat FastMCP unknown-tool as legitimately-absent (mirror memory.py::_is_missing_memory_schema_error) and only tracker.mark on unreachable/timeout; triage why messenger/qa still fail. Re-aggregate /spend/by-schedule per (butler, schedule_name) after per-model pricing, fixing ranking and duplicate React keys. Follow-on: DB-first evidence layer via the unused core/sessions.py pool helpers.

**Why:** Every /spend surface permanently footnotes healthy switchboard as 'cost source unavailable' — a fleet-scale false alarm shipped through the honesty program's own faithful renderers, training the owner to ignore the flag (CLAUDE.md's classify-before-flagging rule violated in the flagging direction). And the by-schedule section answers its own question ('which schedule is burning money') wrongly for any schedule that ran under 2+ models.

**Evidence:** qc:honesty, live-confirmed: meta.unavailable_butlers=['messenger','qa','switchboard'] while /api/butlers reports all three ok; switchboard's butler.toml deliberately omits scheduling/sessions core_groups, so the tools are legitimately absent; 302 by-schedule rows with ≥10 duplicate (schedule,butler) pairs — drain-curriculum-request split into $2,565 + $1,147 fragments each ranking below its true burn.

**Slices:** 1) Absent-vs-degraded classifier in the spend fan-out + contract test; 2) per-model re-aggregation + React key fix; 3) messenger/qa root-cause triage; 4) optional: top-sessions/by-schedule DB-first with MCP fallback (the pool helpers already exist).

### 8. CI truth: bound the faketime leg; re-check migration uniqueness at merge time (engineering, S)

**What:** Add timeout-minutes (~90) plus per-test pytest-timeout to the nightly faketime matrix so hangs surface as named failures and the run concludes 'failure' (which triggers the scheduled-workflow email); then read the ≥3 exposed time-bombs. Add a coordinator/reviewer protocol clause: before merging any PR touching alembic/versions/, fetch origin/main and re-run tests/config/test_migration_chain_head.py on the merge result.

**Why:** The nightly detector works — it found real +45d time-bombs on its first run — then hung to GitHub's 6h kill on both legs, concluded 'cancelled' (no notification), and will burn ~12h of runner time every night while the found signal stays destroyed. And the core_164 revision collision recurred within this very fleet run; the new guard only fires after main is already red.

**Evidence:** qc:governance-tests, live-confirmed: run 29140308642 both legs 04:52→10:53Z, ≥3 'F' markers visible at 99%, no pytest summary, conclusion 'cancelled'; nightly.yml has no timeout. Source-confirmed: PR #3135 root-cause — PRs #3125/#3127 both minted core_164 off core_163 and both merged green on stale CI.

**Slices:** 1) Workflow timeout + pytest-timeout (names the hung test and the bombs); 2) triage the exposed time-bombs; 3) one-line pre-merge freshness clause in the beads-orchestration dispatch protocol.

### 9. Give QA failure a name: 'failed' case state, honest time-to-repair, watcher-death clauses (ux, M)

**What:** Extend CaseState with a 'failed' terminus (fix the state_of_case fallthrough): destructive StateTrack stop, failed badge in the rail, dossier failure banner quoting the journal error, retry offered exactly there (backend already accepts it). Change the MTTR SQL to pr_merged-only ('time to repair') with a failed-count beside it. Add verdict clauses for overdue patrol (last_patrol_at + 2× interval — data already on the wire), pre-trip failure streak, and runtime-CLI credential health.

**Why:** The only two live cases are terminal crashes rendered as calm in-flight 'detect' work with retry withheld and 'No PR yet.' implying progress; the hero KPI reads 17s MTTR on a day of zero repairs and 100% crashes — the number improves the faster the staffer fails; and the fleet watcher reports 'healthy' while its own runtime token is revoked. Failure impersonating both progress and health, on the surface whose job is catching exactly that.

**Evidence:** qa auditor, live-confirmed: cases #601/#995 state='detect' while circuit-breaker attempts show status='failed'; kpis.mttr_24h_seconds=17.3 with prs_landed_24h=0; staffer_status='healthy' over 'refresh token was revoked' in the journal; severity.py:44-60 fallthrough; healing.py retry gate already accepts failed.

**Slices:** 1) failed state end-to-end + retry gate + fleet-wide fixture sweep (old detect-state fixtures); 2) MTTR filter + failed_24h KPI with destructive tint; 3) overdue/streak/credential verdict clauses; pairs with move 2 which fixes the underlying token outage.

### 10. Calendar truth spine: sync deadman + grid freshness plaque; dedup-aware conflict radar (ux, M)

**What:** Grid-level staleness plaque computed from max source staleness ('Last synced Apr 7 — 96 days ago — Sync now', reusing the existing sync mutation) plus a poller deadman that writes a QA-discovery/attention item when no calendar_sync_cursors stamp lands within 2× the poll interval. Run the conflict radar over the same dedup-collapsed rows the grid renders, excluding butler-projected copies of the owner's own events.

**Why:** The live stack served a 96-day-old week at full authority — the provider sync loop died in April with no error, no escalation, and no pixel outside the Sources tab — while the flagship proactive instrument emitted 12 phantom overlap warnings plus fabricated 'packed day' verdicts for a week containing one real lunch. Both directions of fabrication on one surface; the radar trains dismissal exactly when the data is least trustworthy. Provenance: freshness plaque promoted from the 07-10 dropped ledger on this new dead-sync evidence (promotion 1 of 2).

**Evidence:** calendar auditor, live-confirmed: all 10 source_freshness rows stale with last_synced_at 2026-03-30..04-07 or null (~103 days), last_error empty; conflicts endpoint returns 14 issues referencing 8 entry_ids while the workspace renders 4 entries (origin_ref 3-cluster paired combinatorially); query_calendar_conflicts skips the router's _dedup_workspace_rows pass.

**Slices:** 1) Plaque + Sync-now reuse (FE); 2) poller deadman → infra_state/attention ledger; 3) radar dedup pass + butler-copy exclusion + regression test (3-member cluster yields 0 overlaps); 4) follow-on: origin_ref-only dedup keying + __invalid_check__ source purge.

### 11. Scope shortcut suspension to modal dialogs only (ux, S)

**What:** Change isShortcutTargetSuspended's predicate from any '[role=dialog]' to '[role=dialog][aria-modal=true]', plus a target-containment check so keys typed inside any dialog never leak to the page; regression tests with a non-modal dialog mounted.

**Why:** The persistent floating chat is a non-modal dialog, so while it is open every page-scoped shortcut app-wide silently dies — approvals j/k/a/d/x/u, decisions, issues, chronicles [/]/t, sessions — while the '?' sheet still advertises the dead bindings and g-chords keep working, making it look like random per-page breakage. A one-line predicate change un-kills the shell's entire keyboard spine and restores the widget's own chat-while-triaging purpose.

**Evidence:** cross:shell-discoverability, source-confirmed: use-register-shortcut.tsx:147 suspends on any [role=dialog]; FloatingChatWidget.tsx:349-354 renders a persistent non-modal role=dialog (no aria-modal, no scrim, mounted in RootLayout); every real modal in the codebase already sets aria-modal=true.

**Slices:** Single slice: predicate + containment change + three tests (chat-open page verb fires when focus is on page; focus inside chat suspends; true modal still suspends everything).

### 12. Sessions window truth: owner-tz inclusive date filters; pixels for list-level degraded sources (ux, M)

**What:** Map the From/To date inputs through the owner-tz day-window helper at the SessionsPage boundary (From → start of owner day, To → inclusive end) with a From=To contract test; consume meta.sources_degraded on the main list, pinned strips, and stripe chart (SourceDegradedNote above the table, gate the 'No sessions found' empty state); sharpen the bu-tpudw.5 flag registry to per-endpoint-consumer granularity so the next backend-first flag can't hide behind an aggregate consumer.

**Why:** From=To=<day> live-returns 0 of that day's 97 sessions and renders a calm mutually-corroborating zero across table, KPIs, and chart — the page's only time control fabricates absence, contradicting the just-landed owner-tz sweep. And a down pool renders as seamless complete history in the table directly beneath a verdict opener that correctly names the degraded pool: two truths in one viewport.

**Evidence:** sessions auditor, live-confirmed: /api/sessions/aggregate from=2026-07-11&to=2026-07-11 → total=0 vs to=2026-07-12 → 97 (UTC-midnight + inclusive-<= semantics at sessions.py:139); source-confirmed: zero non-test readers of list-level meta.sources_degraded (SessionsPage destructures only has_more/next_cursor); registry passes because consumption is checked per flag-name codebase-wide.

**Slices:** 1) Day-window mapping + From=To contract test + KPI/window coherence test; 2) list/pinned/stripe degraded consumption + empty-state gating; 3) registry granularity sharpening.

### 13. Health freshness spine: one source key, ages on every vital, five silent-error sites (ux, S)

**What:** Point /measurements/sources at COALESCE(metadata->>'source', metadata->>'provider') and write one canonical key at ingest going forward; thread measured_at age into each overview KPI cell ('57 bpm · 7d', amber past a per-vital SLA); add isError branches with SourceDegradedNote to the five dropped-isError sites (MeasurementChart list+trend, AdherenceStatement, DoseHistory, MealsPage DailyTotals) and register the flags.

**Why:** The overview's only staleness surface is structurally dead — live sources:[] against 206 facts carrying provider metadata — while week-old weight and heart rate render as current with zero age signal: fabricated calm on a health surface. And 'No doses logged yet' can render over a failing adherence source on a medication tracker, the exact sin the fleet just purged elsewhere. (The companion data-derived type-vocabulary move stays in the dropped ledger — it strengthens a 07-10-deferred item and the two-promotion cap is spent.)

**Evidence:** health auditor, live-confirmed: GET /api/health/measurements/sources → {sources:[]} vs 206 facts; KPI strip presents weight from 07-07 and heart_rate from 07-05 ageless; source-confirmed: five components destructure only {data, isLoading} (MeasurementChart.tsx:168, MedicationTracker.tsx:122/156, MealsPage.tsx:60).

**Slices:** 1) COALESCE + canonical ingest key (no backfill needed); 2) KPI ages + per-vital SLA tint + source tooltip; 3) five three-way branches + flag-registry entries.

### 14. Timeline legibility: unwrap machine envelopes; let the Errors lens see bounced deliveries (ux, M)

**What:** Generalize _derive_session_summary server-side: unwrap the <user_message>-family fences, strip 'Please use the /<skill> skill' preambles to trigger labels, and label QA-canary sessions ('QA patrol investigation') instead of dumping their system prompt. Give notification rows with data.status='failed' the destructive mark, and widen event_type=error to include failed deliveries alongside failed sessions.

**Why:** A third of the chronicle's primary column is prompt plumbing on the one page that promises 'every household event' in human terms — and a multi-hour owner-alert outage (the move-3 root cause) rendered as calm purple rows that the 'Errors only' lens structurally cannot see: failure impersonating health on the honesty surface itself. Provenance: the errors-lens clause is promoted from the 07-10 dropped ledger on this live outage evidence (promotion 2 of 2); the summary unwrap is new.

**Evidence:** timeline auditor, live-confirmed: 30/50 head-page summaries machine text (9 raw XML fences, 20 /message-triage preambles, 1 QA system prompt rendered as a travel error row); 5 consecutive notification events status='failed' (SPOTIFY credential, ~30min cadence) with neutral dots; Errors view maps solely to sessions success=False (timeline.py:155,261-274).

**Slices:** 1) Server-side summary derivation + table-driven tests over the live-observed prompt shapes (ships alone, FE untouched); 2) failed-row destructive mark (FE-only); 3) errors-lens widening + contract test that a failed delivery appears under event_type=error.

### 15. Light the context bus: deterministic producers for the five cheapest true signals (ecosystem, M)

**What:** Deterministic (dispatch_mode=job, zero-LLM) producers: calendar→meeting/focused on general, home→at_home from HA presence, travel→traveling from active trip legs, health→sleeping from an owner-declared window; plus set_context/check_context MCP tools for explicit dnd/sick per RFC 0009's writer matrix. Add producer SHALL-requirements to the context-bus spec — the spec currently has none, which is how this stayed invisible.

**Why:** Three hardened consumers — the notify dnd/sleeping suppression gate shipped this cycle, every spawned session's situational preamble, and attention-ledger context reasons — have been reading an empty table for 3.5 months: public.user_context has zero rows ever, so the vision-level 'shared situational awareness' claim exists only as consumer plumbing and health check-ins still fire during meetings. Producers are pure infrastructure at zero LLM spend: the highest leverage-per-token move in the collaboration fabric, and it makes already-shipped honesty machinery real.

**Evidence:** eco:collaboration, live-confirmed: public.user_context 0 rows total in the long-lived dev DB (same DB holds 1,232 insight_candidates — not a fresh instance); set_context/clear_context have no call sites repo-wide; consumers wired at _notifications.py:667-691 and spawner.py:1351; openspec context-bus spec contains no producer requirement.

**Slices:** 1) calendar→meeting job (single writer; preambles go non-empty immediately); 2) home→at_home + travel→traveling; 3) health sleep window (activates the shipped notify sleeping-gate); 4) explicit dnd/sick tools + spec producer-requirements delta + verification that suppressed_context_bus ledger events now occur.

## Dropped (dedup ledger — nothing silently vanished)

- qc:honesty — registry reverse-completeness sweep: cut (S); all 14 unregistered flags currently have consumers, no live defect — take when any flag regresses.
- qc:qa-deploy — infra_state roster line, restore-drill due-time, deployment card + core head, close bu-zhfd0: all folded into ranked #1.
- qc:proactivity — count dashboard-channel ingress as owner engagement: cut (S); real gap but the ratchet has never fired in production yet — bundle with the engagement gate's first live cycle.
- qc:proactivity — delete dead _TELEGRAM_API_BASE constant: trivial cruft, fold into any home.py touch.
- qc:governance — ButlerFinanceFinancesTab host-tz month window: cut (S, single-owner impact); add to the bu-5fwbh residue ledger beside bu-z0fn1.
- dashboard — approvals degraded voice in attention/Now lists: cut on slot pressure; the cheapest next honesty S on the cockpit (36 pending live).
- dashboard — one sessions-24h number (69/57/107 live): cut (M); real one-fact-three-numbers defect — queue behind in-flight bu-gcz9e's briefing rewrite to avoid churn.
- dashboard — honest Dispatch-native cost band ($0.00 fabrication + shadcn dialect island): cut; coordinate with bu-sd0l7's costs-pair amnesty before recutting.
- dashboard — consume board degraded aggregates; truthful Now-list copy + predicate doors: cut, polish tier.
- butlers-roster — fix Overview 'review' dead door (one-line /approvals/:id): cut on slots; strongly recommended as a drive-by S.
- butlers-roster — pill/footer from NEEDS_YOU set; owner-pause vocabulary; eligibility mutation honesty/undo; Overview onto the board verdict: cut as a batch — file as a roster-truth mini-epic.
- sessions — render-or-retire dropped dossier evidence (retry lineage, correction_count): cut (S).
- sessions — unify drawer/page dossier failure states: cut (S).
- timeline — mutation-failure pixels + saved-view delete undo + views-list isError: cut (S).
- timeline — heartbeat rollup / new-events count scoping: cut (S).
- notifications — optimistic ack effective_status patch: cut; two-line drive-by.
- notifications — stats-bar isError leg: cut (S); sibling of the landed jad4j.2 work.
- notifications — acked_at immutable ack + predicate-scoped ack-all: cut (M); real history-rewrite defect, deserves its own bead.
- notifications — one 'failed' definition + tile window labels: cut (S).
- notifications — incident grouping + origin door: cut (M); ranked #3 removes the live root cause that generated the 21-clone incident.
- notifications — Tip swap for title= truncations: cut, fold into the a11y batch.
- issues-audit — visible ?result= predicate chips + noise-filter note + LIKE '_' escape: cut (S).
- issues-audit — honest q-pivot (window=all + narrowing-aware empty copy): cut (S).
- issues-audit — keyboard loop (issues evidence verbs, audit list-triage, request-id door): cut; belongs to the deferred keyboard-triage batch; request-id door evidence noted as strengthened.
- approvals-decisions — carry decision substance (description/options/deadline) through the door: cut (M); natural successor bead once ranked #6 lands and bu-97qrw reads fields.
- approvals-decisions — export staleness window tightening + inode-safety check: partially folded into #6 (mount + as-of plaque); remainder cut.
- approvals-decisions — decisions poll cadence + indeterminate badge; age-helper unification + palette verbs: cut (S each).
- calendar — origin_ref-only dedup + __invalid_check__ source purge: cut; kin of ranked #10, file behind it.
- calendar — name the degraded radar instead of silencing: cut until #10 makes the radar's word worth trusting.
- calendar — period-paging/view-switch keyboard verbs: cut, polish.
- health — data-derived measurement vocabulary (types endpoint drives KPIs/tabs/filters): cut — strengthens the 07-10-deferred insight-door vocabulary item, but the two-promotion cap is spent; carries strong new census evidence for next run.
- health — unified ?type= predicate, owner-tz trend buckets, palette logging verbs: cut (S each), queue behind ranked #13.
- spend — truthful verdict (no-ceiling/breach clauses), honest /spend/rules 503, by-schedule fold, keyboard controls, ceiling_available flag, movers partial-day window: cut; rules-503 is the best S; breakdown window unification must coordinate with in-flight bu-7o89u.
- memory — six-move batch (tracker through every fan-out, detail-trio three-way, fleet-honest re-embed, never-ran attention clause, server-side rules ordering, rail guards): cut as a memory-truth epic; detail-trio and never-ran clause are the priority S's.
- entities — serve compute_urgency to the Plex rail (one attention truth): cut (M); flagship sin-8 instance, deserves a bead.
- entities — excise dead contacts tab/client family: cut; fold into the deferred contact-era vestige excision cluster (new evidence: it's a whole dead tab, not cosmetics).
- entities — origin-crumb wiring, scope-faithful j/k siblings, single-h1 dossier: cut, polish.
- settings-secrets — auth_renewal ?tab= → passport focus-grammar repoint: cut; one-line drive-by with contract test.
- settings-secrets — model-verification truth-loop UI: folded into ranked #2's hourly sweep + Models-tab pixels.
- settings-secrets — privileged-reel filter fix, permissions query-boundary port, webhook delete confirm, console single-sourcing + ConsoleClock leak: cut; permissions items overlap the deferred rebuild umbrella.
- education — curriculum-build lifecycle honesty (ledger/doors/staleness clamp for the 21-day dead build): cut (M) on slots; the critical finding stands — file as a bead.
- education — child three-way honesty, Dispatch status-color module, reviews-first IA, struggling-nodes dual-source collapse: cut.
- chronicles — 1904 archive clamp + birthday-projection fix: cut (S); strong candidate for the next batch (live fabricated day).
- chronicles — ManualRefreshButton rewire onto live IEA keys, seam error honesty, jump-to-date + trend click-through, Gantt keyboard/Tip: cut; refresh rewire is a cheap S.
- qa — detail-route 503-vs-404, PatrolJournal UTC clock, j/k case rail: cut; fold into ranked #9's epic or the keyboard batch.
- ingestion — attention-strip fork deletion, archive undo/restore, lifecycle-control wiring, sub-source honesty, reason tags + rule anchors: cut as a batch; the strip fork (S) is the priority — it live-resurrects deliberately-archived identities.
- system — egress catalog truth, standing-posture vs acute split, insight-delivery availability flag, backup growth sparkline: cut; deployment tile + drill fix folded into ranked #1; egress noise (99.1%) is the best M of the rest.
- cross:shell — badgeKey⇒chord convention + palette chord teaching: cut, polish; deployments door folded into #1.
- cross:visual — lane-palette tokenization + .ts hex-guard closure, state-fill guard gap + four violators, DayNarrative serif voice, eyebrow codemod, --green-text triad: cut as a visual-language batch bead; the .ts lint blind spot is the priority mechanism.
- cross:speed — query update-path manifest: cut; coordinate scope with in-flight bu-01r64's coverage-manifest slice before filing. IEA drilldown speed pass (DayRibbon loading gate, placeholderData, adjacent-day prefetch): cut, cheap S. Mutation-classification fence; AutoRefreshToggle retirement (sequence after bu-01r64.3): cut.
- cross:a11y — Gantt keyboard doors + SVG-interactivity lint, title= lint fence + sweep completion, ui/Combobox primitive, QA dropdown swap, Decisions disclosure semantics: cut as an a11y batch — strengthens the 07-10-deferred accessibility-adoption batch (role=button fence must cover SVG), promotion cap already spent.
- eco:connectors — all six moves (bank e-statement auto-capture superseding the drop-folder, GitHub owner-events digest, trip-window flight status or manifesto amendment, multi-device ActivityWatch + missing spec, lifestyle media capture, RFC 0018 FileWatch amendment): cut from ranks — feed as amendments into the existing bu-dyq22 connector-roadmap lane; the travel manifesto-promise-without-substrate deserves an owner-decision bead.
- eco:inference — telemetry coalescing at the route boundary (M, 59.3M tokens/wk line), policy-conformant fallback constant (S, claude-haiku-on-codex is non-functional by construction), roster-wide schedule tiering/budgets (S, 860K tokens/run cron): cut; fallback + tiering are cheap spend-hygiene beads; the classification SLA measurement (28x over) strengthens the known api-lane owner decision without re-filing it.
- eco:knowledge-graph — earned-autonomy bridge (scope-template fingerprints + rule-maturity crosswalk): cut from ranks on L cost only — the vision's core success criterion has no working mechanism; file as an owner-gated architectural epic. Reinforcement-aware decay: coordinate with in-flight bu-5ud8p (it owns the sweep, not the input signal). Deterministic catalog dereference, fleet edge growth, delegate_ask heartbeat: cut.
- eco:collaboration — close the delegation loop (answer return-path + roster adoption + multi-target), cross-domain synthesis pass, commissions primitive (L, needs RFC + owner gate), chronicler briefing chorus (S), full-roster insight participation + travel scan autopsy (S): cut; the two S's are ideal next-ideation-epic children.
- eco:interfaces — Telegram media ingress (voice→transcript, photo→artifact), pocket command lane (/status,/mute), doors in outbound messages, 'what I held back' briefing recap, owner-surface capability spec: cut; the 'failed' outcome half was folded into ranked #3; doors + recap are the best S's once #3 restores delivery.
