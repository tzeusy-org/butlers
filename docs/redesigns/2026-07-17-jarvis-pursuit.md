# JARVIS Pursuit — 2026-07-17 (run 05)

Fifth run of the recurring generative pursuit audit (`butler-relentless-jarvis-pursuit`).
Predecessors: [2026-07-03](2026-07-03-jarvis-audit.md) · [2026-07-04](2026-07-04-jarvis-pursuit.md) · [2026-07-10](2026-07-10-jarvis-pursuit.md) · [2026-07-12](2026-07-12-jarvis-pursuit.md).

**Scope:** 32 fan-out agents + 1 synthesizer — 4 QC graders over the released 07-12 program
(`bu-hmdqz`, all 15 moves merged), 19 page-surface auditors, 4 cross-cutting sweeps, 5 ecosystem
lenses. The live dev stack was up for the QC graders and early page audits (`live-confirmed`)
and **down for most later audits** (`source-confirmed`) — the outage itself is a finding: the
serving worktree's two unmerged commits broke the chronicler migration and left the stack dead
for hours mid-run (see move 2).

**Full per-agent structured output:** [`2026-07-17-jarvis-pursuit-data.json`](2026-07-17-jarvis-pursuit-data.json).
Access pattern:

```bash
jq '.audits[] | select(.page | startswith("<key>"))' docs/redesigns/2026-07-17-jarvis-pursuit-data.json
jq '.synthesis.ranked_moves[] | select(.rank==1)'    docs/redesigns/2026-07-17-jarvis-pursuit-data.json
```

## North star

Five-second fleet verification; earned calm — nothing fabricated, failure never impersonates
health, staleness never wears current-data authority; every clause a door on an unbroken trace
spine (signal → session → evidence); keyboard-first; one visual language (Dispatch) built by one
hand; plus the th-engineering bar (readable hot paths, honest rigorous tests, one-way boundaries,
zero cruft) and the th-projects mandates (specs as planning source of truth; every lifecycle
closed: merged → archived → deployed → decided).

## Headline

Run 05's QC verdict is the strongest of the five runs — 40 of 48 graded slices of the 07-12 program landed as-designed, 7 with gaps, 1 regressed, 0 failed — and yet the fleet spent the week talking to itself: the single most consequential discovery is the consolidation ouroboros, where the core-spawner spec's mandate to store every session's output as an episode makes memory consolidation consume its own exhaust — ~586 no-op LLM sessions/day and ~31M input tokens in two days on travel alone, drowning 90% of the timeline chronicle, flooding the dashboard Now list, and squeezing the global 3-slot spawn cap (live-confirmed independently by four agents). The one regression is the program's flagship: within 4 days of the 07-12 redeploy the tmp-worktree hotreload ceremony returned, `butlers deploy` has produced zero deployments since its own, and the frozen worktree's two unmerged commits broke the chronicler migration and left the whole dev stack dead for hours during this very run — visible only because the move's own behind-main red clause worked. The second-biggest discovery is that the proactive morning voice is structurally destroyed: quiet-hours suppression deletes rather than parks (212 notifications/14d including every morning report), and the freshly-landed as-designed sleeping producer immediately blacked out the insight channel's only daily slot (0 delivered since 07-13; 1,072 expired lifetime vs 25 delivered). The run names a new arc pathology — landed mechanisms lose to the next producer — and the ranked program leads with breaking the ouroboros, deploy provenance truth, park-don't-delete suppression, and a prompt-dump firewall keyed on structured triggers instead of allowlists.

## QC verdict on the 07-12 program (bu-hmdqz)

Across the four QC graders, all 15 moves of bu-hmdqz decompose into 48 graded slices: 40 landed-as-designed (83%, up from 34/46 = 74% on 07-12), 7 landed-with-gaps, 1 regressed, 0 failed — the fleet's execution fidelity is now genuinely high, and most landings were live-proven, not just source-read. Standout landings: move 15.3's sleeping producer closed the loop end-to-end live (producer → context bus → notify suppression gate → honest 'context_bus:sleeping' ledger reason), and move 2.3's dispatch breaker actually opened during a real 07-15 provider burst, paged the owner via the attention ledger, and recovered through its half-open path. The worst landing is move 1 slice 1, graded regressed: the exact pathology the flagship deploy move was named for — serving a frozen tmp .worktrees/ checkout — returned within 4 days; every post-07-12 deployments-ledger row is a boot record impersonating a deployment, the deploy CLI has zero adoption since its own redeploy, and during this run the serving worktree's 2 unmerged commits broke the chronicler migration and killed the dev stack for hours (mitigated only by slice 3's behind-main clause, which honestly showed commits_behind_main=4). The with-gaps cluster shares one shape and it is the run's meta-finding, the next step in the arc (run 03: backend-only honesty flags; run 04: mechanism-real, last-mile-missing): landed mechanisms now decay on contact with the next producer — the timeline unwrap's allowlist was drowned within 4 days by the new schedule:consolidation prompt shape (44/50 head rows machine text, worse than pre-fix), the sleeping producer as-designed suppressed the insight channel's only delivery slot, the conflict radar's dedup left a same-family packed-day fabrication (all_day flag never set at write), the decision lint permanently flags closed beads, and the measurement source key covered only the wellness arms. Fixes keyed on surface patterns, allowlists, and ceremony discipline rot; the program's next fixes must key on structured invariants (trigger_source, lifecycle state, calendar/source identity) and automated gates.

## Tier board (movement vs 2026-07-12 baseline)

| Surface | Verdict | Movement | Note |
|---|---|---|---|
| dashboard | solid | unchanged | Honesty muscle grew (role=alert source deaths, undo-contract inline triage) but live the page states three irreconcilable attention counts at once, the failed-notifications clause is an all-time eternal alarm (87), and the Now list is flooded with raw consolidation prompts. |
| butlers-roster | solid | unchanged | Board strong structurally; header fabricates a '30s refresh' contract the code stopped honoring, and the overdue clause's own evidence line contradicts it ('silent 12m, expected hourly' on 5-min-cadence butlers). Zero keyboard surface on the flagship verification page. |
| butler-detail | solid | unchanged | 07-12 one-console restructure landed; header renders a 67-day-past fire time as 'next' (live), activity feed prints raw REQUEST CONTEXT envelopes, and overview panels drop the board's degraded flags (fabricated calm during outages). |
| sessions | solid | unchanged | Degraded fan-out honesty best-in-class and live-verified (bu-hmdqz.12 landed), but the advertised j/k/[/]/y loop is self-suspending (first keypress opens an aria-modal drawer that kills all shortcuts) and the dossier's tool timeline double-counts calls with contradictory verdicts (live). |
| timeline | functional | regressed | Chronicle drowned: 179/200 live head rows are travel schedule:consolidation sessions rendering raw system prompts — worse than the 07-12 pre-fix state (30/50). The move-14 unwrap works for its allowlist; the allowlist architecture is the defect. |
| notifications | functional | regressed | Trace spine 100% severed: 0/793 rows carry session/trace ids, jsonb double-encode nulls all metadata since 05-01 (588/793), the entire 'Retried' vocabulary is structurally unreachable, and failed critical alerts offer only bury verbs (live instance: WhatsApp-unlinked alert never reached the owner). |
| issues-audit | functional | unchanged | 07-12 grouping/occurrence fixes all landed live, but 275 credential/model failures in 7d are structurally invisible to /issues (writers omit result='error') and the 'Privileged' default view is 74% machine cadence noise. |
| approvals-decisions | solid | unchanged | Most honest vocabulary in the fleet, but window-scoped: 97 owner-approved actions that never executed (incl. 4 outbound messages) are invisible behind the last-30-decided keyhole, and the palette Approve bypasses the undo-window contract. |
| calendar | solid | unchanged | Truth-spine work (plaque, deadman, dedup radar, undo-from-audit) genuinely consumed; the page mixes three timezone authorities (browser/hardcoded-SGT/grid) in violation of the owner-timezone spec, and the surviving conflict issue is itself a fabrication (24.0h 'meetings' from a butler note). |
| health | functional | unchanged | Freshness spine landed (QC-confirmed) but partial: edit dialog crashes on ~69% of live readings (TYPE_META on out-of-vocabulary types), insight severity polarity is inverted, and 158/206 facts are invisible to the sources endpoint. |
| spend | functional | regressed | Scaffolding near world-class, dollars unproven: the fleet's most-used model family (gpt-5.6, 53M tokens MTD) prices as silent $0 in the MTD headline AND the fleet-halt ceiling gate; the same response shows chart $55.18 vs KPI $23.65. |
| memory | solid | unchanged | Best-spoken surface; the 07-17 backend degraded vocabulary (#3346 pools_failed / truthful 503s) is discarded everywhere client-side except the overture, and the dead-letter clause dead-ends one hop from evidence stored in the DB. |
| entities | solid | improved | Re-graded up: degraded notes throughout, view-local keyboard maps, merge pipeline, circles fix live-verified. Plex still hides 156/174 people with no door and concentration's default answer is the owner at 50.9%. |
| circles | functional | improved | 07-12 regression healed: limit=200 fix + truncation footnote + e2e live-confirmed by QC (5.1 as-designed). Still no keyboard map; folded into the entities family. |
| settings-secrets | functional | regressed | Re-graded down on deeper audit: the permissions matrix cannot create a first explicit grant/revoke (primary governance control dead, spec mandates flippable inherited cells), /api/approvals/metrics fabricates all-zero calm on pool loss, and attention deltas keyed on non-unique kind clobber sibling alerts. |
| education | functional | unchanged | Nothing shipped since 07-12 except copy commits; /flows lifecycle endpoint still unwired (the '21-days-dead behind still-building' mechanism), curriculum request lock still an invisible honor-system 409, abandoned curricula still unreachable. |
| chronicles | solid | unchanged | Structurally mature with real correction write-path; dark-feeder days still impersonate quiet days at the voice column, briefing sub-queries swallow genuine DB failures into calm, and dense days silently lose their morning to a 500-row DESC cap. |
| qa-system | solid | unchanged | Move 9 fully landed live (failed terminus, MTTR honesty, verdict clauses); suppressed patrols still wear the clean-green dot, the overdue deadman only fires on re-render, and claim⇄evidence traversal is mouse-only. |
| ingestion | solid | unchanged | Honesty is allowlist-based: the three pre-named sub-source flags are honest while every query-level failure renders calm empties ('No connectors registered.', all-zero KPIs, 'Policy lives in code.'); non-OAuth errors get a dead-end reauth pill. |
| system | solid | unchanged | Best bones on the dashboard and the behind-main clause did live work this run (caught the worktree drift); verdict unions only 7 of 10 page sources and cached board data masks a dead board API indefinitely. |
| cross:shell | solid | unchanged | World-class chassis, ~40% adoption: palette empty-query page verbs vanish after first navigation (global Run-butler rows win the slice), Sessions ships shortcuts with no hint strip, telemetry ledgers and detail pages have zero keyboard surface. |
| cross:visual | solid | unchanged | Dispatch spine real and enforced, but cool hues (blue/purple/sky) are an unregistered shadow palette carrying 7+ meanings that the ESLint guard never covered; education still ships light-only pre-Dispatch chips; chat bounces (forbidden motion). |
| cross:speed | solid | unchanged | Never-blank floor proven but unapplied to Issues/Spend/butler-activity window toggles; memory search renders 'Nothing in the books.' while loading; intent-prefetch built once, wired 3 components deep. |
| cross:a11y | functional | unchanged | Primitives layer rose (lint gate, reduced-motion floor, modal choreography, StateDot) but the flagship chat loop is completely silent to screen readers, Secrets Passport fields have invisible keyboard focus, and the axe route sweep only audits pending-fetch empty states. |
| eco:reliability | weak | unchanged | Not re-audited as a lens this run; adjacent evidence points down, not up — deploy regression, restore drill failing since 07-12 (createdb permission denied, backups unproven), calendar provider sync outage at ~110 days with a one-shot unactioned escalation. |
| eco:proactivity-quality | functional | unchanged | Delivery spine now honest and transport works (49 delivered, failed outcome live), but the morning lane is structurally destroyed: quiet-hours deletes 212 msgs/14d, insight channel dark since 07-13 (suppressed by the new sleeping mirror), briefings computed 06:50 daily have no morning consumer. |
| eco:data-quality | functional | unchanged | No dedicated lens this run; cross-agent evidence shows the write-side stamping class persists (notifications provenance nulled, audit result=NULL, unattributed measurements, batch-blurred memory provenance, requested-vs-executed model in spend). |
| eco:inference | weak | unchanged | 07-12 landings closed the honesty gap in model selection (breaker live-proven), but the loop hemorrhages: consolidation ouroboros (~586 no-op sessions/day), 60s p50 / 134s p95 owner message→reply with cold CLI boot per dispatch, 8+ serial pre-spawn round trips. |
| eco:collaboration | functional | improved | Re-graded up from weak: context bus live and gating deliveries, briefing participation recovered to 7/7 fresh. Delegation still zero rows ever with no session linkage; multi-domain messages still produce N uncoordinated replies; the bus has no read surface. |
| eco:knowledge-graph | functional | improved | Re-graded up from weak: facts 4,017→4,437, confirmations 21→191, rules 2→89, catalog injected into every session. Trust layer hollow: provenance batch-blurred (500-episode citations), switchboard hygiene jobs dead since April (4,487 episodes past TTL), rule promotion structurally unreachable. |
| eco:connectors | functional | unchanged | Harden-before-broaden honored (#3360/#3351/#3367/#3392/#3375); manifesto-promise-vs-substrate gaps persist (travel document expiry: zero rows ever; phantom energy tools in a live skill; no voice-call substrate; outdoor weather ingested but unread). |
| eco:interfaces | functional | unchanged | No dedicated lens this run; sweeps show the chat front door still has no chord, is SR-silent, and uses forbidden bounce motion — the flagship conversational interface lags the dashboard's maturity. |

Score: 15 solid · 12 functional · 1 weak verdicts across audited surfaces (QC graders carry
landings, not tiers). Movement: entities, circles, eco:collaboration, eco:knowledge-graph
**improved**; timeline, notifications, spend, settings-secrets **regressed**.

## Systemic themes

### Landed mechanisms lose to the next producer (the run-05 arc pathology)

The new stage in the run-over-run arc (run 03: backend-only honesty flags; run 04: mechanism-real-last-mile-missing): mechanisms now land as-designed and are then eroded within days by new producers, shapes, or workflows their allowlist/ceremony never covered. Pattern-matched unwraps, hand-listed statuses, and discipline-only protocols decay on contact with the fleet's own velocity. The durable fix shape is structural keys (trigger_source, lifecycle state, source identity) and automated gates, never enumerated surface patterns or coordinator discipline.

**Exemplars:** qc:ux-truth/timeline: move-14 summary unwrap drowned within 4 days of landing by the new schedule:consolidation prompt shape — 44-46/50 live head rows raw machine text, worse than pre-fix; qc:deploy-ci: `butlers deploy` bypassed by the compose-hotreload-from-worktree ceremony it retired, within 4 days, and the frozen worktree's unmerged commits killed the dev stack for hours; qc:routing/eco:proactivity: the as-designed sleeping producer immediately suppressed the insight channel's only daily slot (dark since 07-13); qc:data-truth: decision lint permanently flags two closed beads every Monday.

**Affected:** timeline, dashboard Now list, system/deploy pipeline, insights/proactivity, calendar conflict radar, decisions digest, butler-detail activity feed

### The fleet is talking to itself: the consolidation ouroboros

The core-spawner spec mandates storing every memory-enabled session's output as an episode with no maintenance carve-out, so consolidation sessions' own outputs re-enter the consolidation queue forever — a self-sustaining loop spawning ~586 no-op LLM sessions/day on travel alone (~34M uncached tokens/week), multiplied ~4x by residual cross-schema episode misrouting. The grind burns workhorse-tier quota, occupies ~3.5 slot-hours/day of the global 3-slot spawn semaphore that owner-interactive dispatches share, floods every session-listing surface, and its spend is invisible because the models involved are unpriced.

**Exemplars:** eco:inference (critical, live): travel 586/588 sessions in 24h are schedule:consolidation; pending=1 self-refeed observed between beats; qc:ux-truth (live): 1,678 sessions / 31,014,676 input tokens in ~2 days, one ~18.5k-token session per single episode; timeline (live): 179/200 head events are this loop; eco:inference (live): foreign butlers' episodes still landing in travel.episodes post-#3393/#3404.

**Affected:** eco:inference, timeline, dashboard, sessions surfaces, spend (unpriced), memory quality, interactive spawn latency

### Suppression is deletion: the proactive morning voice is structurally destroyed

The delivery spine is honest (failed outcome, transport fix, breaker paging all live) but the routine proactive lane is choked exactly at the owner's morning: the quiet-hours gate records 'suppressed' and destroys content (no envelope, no body persisted), three divergent quiet-window predicates disagree so the 08:00-08:59 morning-report band is destroyed under a 23→8 policy, the sleeping signal is a confidence-1.0 mirror of the static policy that now double-gates three consumers, and the briefing pipeline computes fresh 7/7 contributions at 06:50 daily that no morning job ever delivers (sole consumer is a mistimed 15:15 'Tomorrow Prep').

**Exemplars:** eco:proactivity (critical, live): 212 destroyed notifications/14d incl. daily home/finance/relationship/health/chronicler morning sends, content unrecoverable; (critical, live): insight cycle at 08:07 suppressed by sleeping-until-09:00 → 0 delivered since 07-13, 1,072 expired lifetime vs 25 delivered at 85% engagement; (major, live): eod-tomorrow-prep authored for 23:00 SGT fires 15:14 SGT while scheduled_tasks.timezone claims UTC.

**Affected:** eco:proactivity, notifications, insight broker, briefing pipeline, every domain butler's morning output, upcoming bu-s8l3i attention panel (will be dominated by 179 secrets_lifecycle suppression rows)

### The write side never stamps what the read side renders

Doors and honesty surfaces are coded and shipped, but bound to columns and keys no writer populates — so trace spines sever at birth and truth surfaces cover a minority of the record while wearing complete authority. This is the write-side twin of the last-mile theme: the contract exists at the boundary, and the producers upstream never signed it.

**Exemplars:** notifications (critical, live): 0/793 rows carry session_id/trace_id though deliver.py plumbs both, and a jsonb double-encode nulls all metadata since 05-01 (588/793) — Session/Trace/origin doors have never rendered and 'Retried' is unreachable; issues-audit (critical, live): credential/model verify writers omit result='error' so 275 failures/7d can never form an issue group; health/qc:ux-truth (live): measurement_log stamps no source key — 158/206 facts invisible to the freshness spine; eco:knowledge-graph (live): every consolidation fact/rule links to all 500 batch episodes (median=max=500 links), and dangling links are guaranteed once TTL cleanup runs.

**Affected:** notifications, issues-audit, health, memory/knowledge-graph, spend (requested-vs-executed model attribution), delegation ledger

### The honesty last mile still ships backend-first — now measured in days, not months

Run 04's signature defect narrowed but persists, with a sharper twist: envelopes landed this very week are already being discarded client-side. Degraded meta, truthful 503s, and error states exist on the wire while the consuming component renders calm empties, fabricated zeros, or 'not found'. The class needs an enforcement gate (lint/manifest over isError/meta consumption), not another sweep.

**Exemplars:** memory (major, source): PR #3346 (07-17, backend-only) shipped pools_failed + named-pool 503s — consumed only by MemoryOverture; the detail trio still renders 'not in the ledger' for pool outages; settings (major, source): /api/approvals/metrics returns all-zero defaults on pool loss with no DegradedSources tracker, feeding the console's red signal; ingestion (major, source): roster fetch failure renders 'No connectors registered.' + all-zero KPI band; filters failure renders 'Policy lives in code.' at all five gates.

**Affected:** memory, settings-secrets, ingestion, approvals sections, timeline butlers-facet/load-older, chronicles briefing + badge strip, QA rail/dossier, sessions pinned excerpts

### World-class chassis, ~40% adoption — and the opt-in is decaying

The shell owns genuinely excellent primitives (command spine, shortcut registry, list triage, prefetch registry, EmptyState actions, QueryBoundary, FetchingDim, announcement-capable a11y layer) but every one is opt-in, and adoption is regressing rather than spreading: pages ship shortcuts without advertising them, the palette's empty-query view loses page verbs after any navigation, and the flagship sessions keyboard loop is structurally self-suspending. Enforcement has been proven in-repo (poll-policy lint, design-token lint, axe manifest) — the same pattern needs to guard adoption.

**Exemplars:** sessions (major, source): j/k opens an aria-modal drawer that suspends all page shortcuts — the advertised loop is dead on arrival; cross:shell (major, source): after first navigation, Cmd+K's Actions group shows 8 global 'Run <butler>' rows and drops the current page's verbs; cross:a11y (major, source): the chat thread has no live region anywhere — the JARVIS front door is silent to assistive tech; cross:speed (minor, source): intent-prefetch wired into exactly 3 components while the main sessions table cold-fetches its own drawer.

**Affected:** sessions, timeline, audit, ingestion ledgers, butlers board, memory, spend controls, QA dossier, chat widget, secrets passport

## Ranked moves

### 1. Break the consolidation ouroboros: maintenance sessions never become episodes — engineering, cost M

**What:** Spec delta to core-spawner (§'store the session output as an episode' gains a maintenance trigger-source exclusion) + spawner carve-out so schedule:consolidation-class sessions never write their own output as episodes; clean up the ~1,800 self-generated episodes; root-cause the residual cross-schema episode misrouting (post-#3393/#3404) that multiplies the loop ~4x; batch N pending episodes per spawned session (vs ~1 today at ~18.5k boilerplate tokens each) with a per-run session cap and a 'backlog not draining' deadman; route this tool-less text-in/JSON-out job to the cheap tier/api lane.

**Why:** vision.md earned calm / 'nothing fabricated' — consolidation exists to distill the OWNER's life, not butler self-narration; th-engineering readable hot paths + the owner's cost-independence doctrine (no-claude/opencode). This is live production breakage: money, spawn capacity, and every session surface at once.

**Evidence:** live-confirmed by four independent agents: eco:inference (critical: 586/588 travel sessions in 24h are schedule:consolidation; pending=1 self-refeed observed; spawner.py:2016-2022 stores episodes unconditionally per spec:234), qc:ux-truth (1,678 sessions / 31.0M input tokens in ~2 days), timeline (179/200 head rows), dashboard (Now list flooded). Misrouting live: 48 foreign-butler rows/24h in travel.episodes.

**Slices:** 1) spec delta + trigger-source exclusion + test asserting no episode after a schedule:consolidation spawn; 2) cleanup migration/script for self-generated episodes; 3) misrouting residual diagnosis + fix; 4) episode batching + per-run cap + backlog deadman; 5) cheap-tier/api-lane transport for tool-less maintenance.

### 2. Deploy truth pack: serving-provenance honesty + automated migration-uniqueness gate — engineering, cost S

**What:** Add deployments.source ('deploy'|'boot') + serving_mode (image vs bind-mounted-worktree, detected at boot) stamped at both call sites; DeploymentTile + SystemVerdictBanner render a red 'boot from bind-mounted worktree .worktrees/<name> (hotreload)' clause; a post-merge-to-main CI job runs test_migration_chain_head.py on the merged tree (paths: alembic/versions/**), retiring the discipline-only AGENTS.md:499 clause.

**Why:** Five-second fleet verification + 'staleness never wears current-data authority': boots currently impersonate deployments and worktree serving is invisible except via behind-main. The 07-12 flagship move regressed in 4 days and the failure mode broke the stack during this run — the fix must be structural, not ceremonial. (qc:deploy-ci moves 1+3, merged.)

**Evidence:** live-confirmed (qc:deploy-ci, regressed grade): hotreload containers bind-mount .worktrees/compose-memory-schema-refresh-20260717 (HEAD = origin/main + 2 unmerged commits); commits_behind_main=4; all 7 post-07-12 ledger rows are boot records with started_at==finished_at; the worktree's chronicler migration break left the dev stack in Created state for hours (observed by ~10 audit agents); core_164 collision class already recurred once inside the 07-12 fleet run.

**Slices:** 1) core_NNN migration (serialize the number) + source/serving_mode stamped in cli.py:_record_deployment_boot and deploy.py:run_deploy; 2) FE red clause + verdict lift; 3) on-push-to-main migration-chain workflow failing loudly into QA infra_state; drop the 'not an automated gate' sentence.

### 3. Park, never delete: one suppression primitive, one quiet-window predicate, wake-anchored morning flush — ecosystem, cost L

**What:** notify()'s quiet-hours/context-bus branches write a content-bearing deferred envelope with wake-anchored deliver_at (never a bare content-less 'suppressed' row); one shared end-exclusive quiet-window predicate replaces the three divergent implementations (inclusive core / exclusive broker / +1h sleep mirror) and the duplicated config collapses; the insight broker's suppressed skip enqueues a catch-up cycle at suppression end; wake evidence (telegram activity, OwnTracks, HA) supersedes the sleeping signal and fires one composed morning flush of parked items + briefing/combined/<date>; secrets_lifecycle policy-suppression parks once instead of writing ~13 identical rows/night; re-anchor eod-tomorrow-prep + stale UTC cron comments and add a sync-time 'always-suppressed schedule' lint.

**Why:** vision.md: 'butlers surface timely, relevant insights without being asked' — earned calm requires silence to be chosen and reversible, never destructive; the health manifesto owns sleep truth, not a static-policy mirror at confidence 1.0. Provenance note: this builds on and obsoletes the 07-12 dropped-ledger 'what I held back' recap — parked envelopes make the recap real (eco:proactivity moves 1-4, merged).

**Evidence:** live-confirmed, two criticals (eco:proactivity): 212 destroyed notifications/14d incl. every morning report, bodies unrecoverable (attention_ledger metadata NULL); insight channel 0 delivered since ~07-13 (45 pending, 11 expired that week; 1,072 expired vs 25 delivered lifetime at 85% engagement) because the as-designed sleeping producer suppresses the only 08:07 slot; live 08:00-08:25 suppression casualties across 4 butlers from the inclusive-end predicate; eod-tomorrow-prep fires 15:14 SGT vs its '23:00 SGT' prompt (4 consecutive ledger days).

**Slices:** 1) notify() park branch + content-bearing ledger rows (kills the data loss, ships alone); 2) shared end-exclusive predicate + config collapse + boundary tests; 3) broker catch-up cycle at suppression end; 4) wake-evidence supersede + single composed morning flush; 5) secrets_lifecycle single-park + cron re-anchor + always-suppressed lint.

### 4. Prompt-dump firewall: structured-trigger-first summaries + maintenance demotion everywhere — ux, cost M

**What:** Invert the summary architecture so machine text can never reach a human-intent column: derive labels from trigger_source/skill metadata BEFORE any prompt-text inspection (schedule:<job> → humanized label unconditionally; prefix-aware _TRIGGER_LABELS); generalize is_heartbeat to machine_class ('owner'|'heartbeat'|'maintenance') so TimelineLedger and the dashboard Now list collapse maintenance groups into rollups ('travel: 48 maintenance runs this hour') with an Internal lens defaulted off; extract timeline.py's unwrap into a shared helper consumed by activity_feed.py (butler-detail Recent panel).

**Why:** Earned calm / 'every household event in human terms': the QC meta-finding is that the move-14 allowlist structurally leaks every future machine-prompt family — this is the structural fix that makes theme-1's class impossible for chronicle surfaces. One vocabulary, one hand: the same session must read identically on timeline, dashboard, and butler-detail. (Merges qc:ux-truth move 1, timeline moves 1-2, dashboard move 3, butler-detail move 2.)

**Evidence:** live-confirmed: 44-46/50 timeline head rows render the raw '# Memory Consolidation' system prompt + 2 raw '=== Chat id:' telegram envelopes; dashboard Now list shows the same; butler-detail finance activity feed prints 'REQUEST CONTEXT {json…' as summaries (activity_feed.py:81 prompt[:120]) while timeline.py:148-161 already owns the unwrap.

**Slices:** 1) trigger-first label derivation + table tests (covers the '=== Chat id:' shape via its trigger); 2) machine_class + FE maintenance rollup + Internal lens; 3) shared unwrap helper adopted by activity_feed (+contract test both endpoints render the same session identically).

### 5. Repair the notification provenance pipeline end-to-end (and give failure a recovery verb) — engineering, cost L

**What:** Fix the jsonb double-encode (log_notification passes the dict; codec encodes once) with a regression test on jsonb_typeof; read-side unwrap + one-shot backfill of the 588 string-scalar rows; actually stamp session_id/trace_id/request_id at write (deliver.py already resolves them); render the origin/session/trace doors; add POST /notifications/{id}/retry re-invoking deliver() from the stored notify.v1 envelope with owner-contact fallback for the empty-recipient failure class — making effective_status='retried' reachable for the first time.

**Why:** North star: 'every clause a door on an unbroken trace spine' — the spine is severed at three layers on the outbound ledger, and the API asserting metadata:null for rows that HAVE provenance is fabricated absence; 'failure never impersonates health' — the only verb on a failed critical alert today is to make it look read. (notifications moves 1+2, merged.)

**Evidence:** live-confirmed (notifications, critical+majors): 588/793 rows double-encoded → API nulls provenance (log.py:76 json.dumps + db.py:137-143 codec); 0/793 rows carry session_id/trace_id; ?status=retried → total=0 all-time (dead vocabulary incl. its most expensive stats query); live row e813d8ca: 'WhatsApp unlinked, ingestion paused' alert failed on empty recipient and never reached the owner.

**Slices:** 1) write-side codec fix + typeof regression test; 2) read-side legacy unwrap + backfill; 3) session/trace stamping + FE doors; 4) retry-from-envelope endpoint + verb on failed rows (makes 'retried' real or, failing that, retire the vocabulary).

### 6. Close the /issues failure spine at the audit append boundary — engineering, cost M

**What:** Make result='error' non-optional for failure-semantic audit writes: fix _write_credential_audit (verified→success / failed→error + probe message), models.verify_all, approval/model mutation writers; backfill the 608 historic action='failed' rows so history joins the spine; replace the 'Privileged' two-item denylist with a consequence allowlist (approval.*, model.*, permission.*, data.*, webhook.*, credential events, OR result='error'); port the sessions owner-tz From/To bound fix to audit.py via a shared helper.

**Why:** 'Failure never impersonates health' on the fleet's canonical failure register: /issues must be the one place where 'no rows' means 'nothing failed'. The From-date bug is the exact class sessions fixed on 07-12 (bu-hmdqz.12), documented in sessions.py and unapplied here. (issues-audit moves 1+2+3, merged.)

**Evidence:** live-confirmed (issues-audit, critical): 275 credential/model failures in 7d invisible to /issues (result=NULL; Spotify ACCESS_TOKEN failing right now renders OutcomeBadge 'Unknown'); privileged view is 74% llm_api_call/session success cadence (4,742 of ~6,400 rows/7d); current-day writers produce 1,200+ NULL-result rows/7d falsifying the 'Unknown = pre-unification' comment.

**Slices:** 1) writer fixes + append-boundary contract test (failure-semantic action families require result); 2) backfill migration; 3) consequence-allowlist predicate (keep ?noise=all); 4) shared owner-tz bound helper + To input + faketime test.

### 7. Spend truth: unpriced-model honesty + one ledger spine + divergence deadman — engineering, cost L

**What:** Explicit pricing entries or billing_class ('subscription — $0 marginal', 'local — free') for the gpt-5.6 family; estimate_session_cost distinguishes None from 0.0 and threads unpriced_models[] through summary/breakdown/forecast → rendered as '—/unpriced' incl. a ceiling-gate 'blind to N unpriced models' clause; derive per-day actuals and all dollar figures from token_usage_ledger (the halt gate's own spine — the month-total-only limitation is self-imposed), retiring sessions.model pricing that attributes dollars to models that never ran; a sessions-vs-ledger divergence deadman; label or backfill the pre-Jul-10 requested-vs-executed misattribution.

**Why:** 'Nothing fabricated' — the budget instrument currently performs earned calm it has not earned, and the fleet-halt gate is blind to the fleet's most-used model; in-repo precedent bu-qcuw4/#2394 already ruled unpriced renders '—', never $0.00. (spend moves 1+2, merged; distinct from open bu-m95jq discretion enforcement.)

**Evidence:** live-confirmed (spend, critical+majors): gpt-5.6-luna/sol/terra render $0.0000 bars while luna alone has 1,988 ledger rows / 53.3M input tokens MTD; one response shows solid-actuals sum $55.18 vs mtd_usd $23.65 (2.3x, visible slope kink); $570.92 attributed to gpt-5.5 with zero July ledger rows (travel sessions carried requested model, ledger the executed one).

**Slices:** 1) pricing/billing-class entries + unpriced envelope + '—' rendering + ceiling-gate clause; 2) per-day/butler/model ledger GROUP BY variant → repoint forecast/daily/summary/breakdown; 3) divergence deadman + pre-fix-era label/backfill.

### 8. Make restore capability real: fix the CREATEDB grant, escalate failed drills — engineering, cost S

**What:** Grant CREATEDB to the drill role in scripts/init-db.sql (or restore into a pre-created scratch database); make _restore_drill_overdue result-aware — on last result=fail shrink retry to 24h and write an attention-ledger row naming the error; add a drill-success regression test against testcontainers Postgres and a 'failing since <ts>' age on /system/backups.

**Why:** Earned calm: 850MB nightly backups marked 'healthy' are fabricated safety while the only restore attempt ever made died on a permission error and re-fails weekly by design with no escalation — the one disaster-recovery primitive must be proven, not presumed. (qc:deploy-ci move 2.)

**Evidence:** live-confirmed (qc:deploy-ci, major): single restore_drill_result row ts=2026-07-12 result=fail 'createdb failed: permission denied' — deterministic in this environment; get_last_restore_drill has no result filter so a failed drill resets the 7-day clock; no attention row, QA finding, or bead drives the fix.

**Slices:** 1) grant or scratch-DB mechanism fix; 2) result-aware retry interval + attention row; 3) regression test + failing-since age.

### 9. Standing-false-signal pack: five S-fixes to alarms that can never clear and calms that were never true — ux, cost M

**What:** (a) dashboard failed-notifications signal windowed to 24h with '· 24h' label and lifecycle back to calm (all-time count stays on /notifications); (b) decision-convention lint scoped to open beads (closed decisions are done, not violations) — silences the standing Monday '2/6 beads fail' digest; (c) QA pulse strip gives 'suppressed' patrols a destructive/amber ring (never clean green) + full status vocabulary test pinned to _VALID_PATROL_STATUSES; (d) butler-detail header never renders a past fire time as 'next' — amber 'overdue: <schedule> Nd' fact instead; (e) calendar truth trio: context producer skips butler-authored events, conflict scanner treats ≥24h midnight-aligned/butler_generated events as non-meetings, purge the phantom 'butler'/'butlers' source rows pinning the freshness plaque.

**Why:** Earned calm — a flag the owner learns to ignore destroys the whole attention system's authority (the exact flag-training-dismissal shape the 07-12 spend move fixed); each item is a small live falsehood on a high-glance surface. (Merges dashboard move 1, qc:data-truth move 1, qa move 1, butler-detail move 1, qc:routing move 1 + qc:ux-truth moves 3-4's calendar halves.)

**Evidence:** all live/source-confirmed: 87 all-time failed pinned red on / with the LLM briefing inheriting the ratchet; lint failure reproduced with the job's exact command against the real export; suppressed→green mapping at QaOverviewPage.tsx:471-478 vs backend vocabulary; finance header 'next 67d ago' (live schedules data); '24.0h of meetings' issue from a butler cafe-closure note + user_context's only meeting row was 'BUTLER:'-prefixed at confidence 1.0 + phantom sources frozen at 2026-04-16.

**Slices:** Five independent S slices, one PR each — (a) through (e) above, each with a regression test.

### 10. Stalled-approvals whole-population radar + recovery verbs — ux, cost M

**What:** Backend: state=stalled (status='approved', never executed) on the flat approvals endpoint + a whole-population stalled count in meta (degraded-tracked); opener derives its stalled clause from the count, never the 30-row decided window; a stalled lane with Retry-dispatch and Abandon verbs; the dossier renders the decision record (decided_by/decided_at/denial reason/execution_result) and carries Retry at terminal states.

**Why:** 'Failure never impersonates health' on the Trust Console itself: owner-approved-but-never-executed is the worst silent failure class — the owner said yes and nothing happened — and the current radar is a keyhole the executed stream permanently saturates. The approve toast even promises 'Retry from History' that eviction makes false. (approvals move 1 + the dossier-outcome half of move 2.)

**Evidence:** critical, source+live-DB-confirmed (approvals; stack API down so SQL substituted): relationship.pending_actions holds 97 status='approved' rows spanning 2026-05-23→07-16 incl. 4 outbound notify messages; no endpoint exposes a stalled count or filter (approvals.py:1626); decided_by/decided_at served but never rendered despite a code comment claiming they are.

**Slices:** 1) stalled state + population count + opener repoint; 2) stalled lane + Retry/Abandon verbs (reusing the existing history-row retry mutation); 3) dossier decision-outcome section + execution_result field.

### 11. Make the permissions matrix real: flip-on-inherited creates the first explicit grant — governance, cost S

**What:** Enable click on inherited cells (the existing modal already collects the reason; PUT already upserts) with inherited-vs-explicit rendered as dim-vs-foreground instead of disabled; add vocabulary validation to PUT /api/permissions (reject perms outside ENFORCED_PERMISSIONS, butlers outside the registry) so the newly-writable surface cannot mint decorative rows; spec contract test.

**Why:** The governance surface's manifesto question — 'what can this system do, on whose authority?' — is void while the owner cannot write a revoke; openspec/specs/dashboard-permissions/spec.md:60-61 already mandates flippable inherited cells, so this is spec-compliance, not new design. (settings-secrets move 1.)

**Evidence:** critical, source-confirmed (settings-secrets): frontend disables exactly the dense-default inherited cells (every pair without an explicit row), so no first explicit grant/revoke can ever be created from the UI; PUT accepts arbitrary butler/permission strings unvalidated (permissions.py:157-203).

**Slices:** 1) enable click + optimistic dim→foreground transition; 2) PUT vocabulary validation; 3) spec amendment + contract test.

### 12. Health: data-derived measurement vocabulary end-to-end + severity polarity fix — ux, cost M

**What:** A /measurements/types endpoint derived from live data feeds chart tabs, tracker filters, KPI candidacy, and a generic form value editor (killing the TYPE_META crash structurally); fix the inverted insight-priority polarity (+regression test pinned to the backend threshold); insight doors carry ?type=/since/until which the chart actually consumes; measurement_log stamps source='owner_log' so the freshness spine covers the whole record (closes QC 13.1's gap).

**Why:** PROMOTION (1 of 2) from the 07-12 dropped ledger ('measurement vocabulary hardcoded in 5 divergent lists' was cut last run) — promoted on NEW evidence: the edit dialog now provably crashes on the majority of live data and a live hrv-drift insight flags a vital the dashboard cannot chart, filter, or name. 'The surface must speak for the whole record' + trace-spine doors. (health moves 1+2 + the stamping slice of move 3.)

**Evidence:** live+source-confirmed (health, critical+majors): MeasurementForm.tsx:169 TYPE_META[type].unit crashes for ~142/206 (69%) of live readings; HealthOverviewPage.tsx:291-295 renders priority≥2 (urgent per router.py:2000) as 'low'; 102/206 readings have no chart tab; 158/206 facts source-less (qc:ux-truth 13.1 gap, live SQL).

**Slices:** 1) types endpoint + OpenAPI contract test; 2) generic form editor (fixes crash, ships alone); 3) chart/filter/KPI consumption; 4) polarity fix + typed insight doors; 5) measurement_log source stamp + sources-sum contract test.

### 13. Self-healing module-default schedules + expired-retention deadman — engineering, cost S

**What:** ensure_module_default_schedule's reclaim re-enables module-default rows it reclaims (with an audit entry) + regression test reproducing the Apr-7 orphaned-TOML→disable→reclaim-leaves-dead path; an expired-retained gauge in memory stats with a degraded-honesty flag; drain switchboard's 4,487 overdue episodes (coordinated with provenance so evidence links don't silently dangle).

**Why:** Retention is a kept promise: the most sensitive schema (routed owner-message content, sensitivity-classed) has violated its own TTL policy for 3 months because a hygiene job died silently and the reclaim path is designed to never resurrect it — failure impersonating health at the infrastructure layer. Complements, not duplicates, open bu-c6wjr (background-job health surfacing): this makes the module heal its own defaults. (eco:knowledge-graph move 3.)

**Evidence:** live-confirmed (eco:knowledge-graph, major): switchboard.scheduled_tasks memory_episode_cleanup/memory_consolidation enabled=false, source='db', last_run 2026-04-07; 4,487 of 4,798 switchboard episodes past expires_at; scheduler.py reclaim comment 'enabled … left untouched'.

**Slices:** 1) reclaim re-enable + audit + regression test; 2) expired-retained gauge + flag on /memory stats; 3) coordinated drain.

### 14. Memory honesty last-mile: consume the #3346 degraded vocabulary + close the dead-letter lifecycle — ux, cost M

**What:** Detail trio consumes the truthful 503 (named unreachable pools) instead of rendering 404-styled 'not in the ledger'; registers + search read meta.pools_failed into the existing SourceDegradedNote; search shows meta.total with paging ('showing 1-50 of 312') instead of slice-counts-as-totals; expose dead_letter_reason/last_consolidation_error/attempts on the episode dossier + POST /episodes/:id/requeue so dead_letter stops being terminal-by-omission; commit-footer inline failure/landing feedback for Confirm/Retract; route SearchResults through QueryBoundary (kills the loading-renders-'Nothing in the books.' verdict).

**Why:** 'Failure never impersonates health' + th-projects 'every lifecycle closed': the backend shipped the truth on 07-17 (b52cf10a3) and the frontend discards it everywhere except the overture — the fleet's best-spoken surface currently fabricates absence during pool outages, and its single red clause dead-ends one hop from evidence the DB already holds. (memory moves 1-4 + cross:speed's SearchResults move, merged.)

**Evidence:** source-confirmed (memory + cross:speed): pools_failed consumed only by MemoryOverture.tsx (grep); _raise_memory_detail_miss's named-pool 503 detail discarded; consolidation.py:375-399 writes four failure fields never SELECTed by the API; SearchResults destructures no isLoading (renders the verified-empty verdict during fetch); use-memory.ts mutations have no onError.

**Slices:** 1) detail-trio 503-vs-404 branches; 2) pools_failed notes on registers/search + totals/paging; 3) dead-letter fields + requeue endpoint + failure band; 4) commit-footer feedback + QueryBoundary adoption.

### 15. Calm-empty error-honesty sweep beyond bu-tpudw's fence, with an enforcement gate — ux, cost M

**What:** One coordinated pass consuming isError/degraded meta on the surfaces the in-flight bu-tpudw epic does not fence: ingestion (roster, connector detail secondaries, filters gates, histogram '0 events' headers), approvals (suggestions/promotion/stats sections + DegradedSources on /api/approvals/metrics), timeline (butlers facet, load-older failure pixel, named degraded_butlers), chronicles (classify-before-swallow in briefing sub-queries + SourceStateBadgeStrip isError), QA (rail SourceDegradedNote + stop summary errors blanking healthy dossiers), settings (audit-reel isError one-liner), sessions (tri-state pinned excerpts). Then a lint/manifest gate (the proven poll-policy/axe-manifest pattern) so new query call sites cannot silently drop isError.

**Why:** Fleet-wide bu-qvnce.1 convention: 'a source that raises must never render as a truthful empty/zero/all-clear' — this class has now survived three pursuit runs by reappearing at new call sites faster than sweeps retire it; per theme 1, the sweep only sticks if it ships with enforcement. Explicitly coordinates with (does not duplicate) bu-tpudw's sessions/issues/search fence.

**Evidence:** source-confirmed across seven agents with exact drop sites: ConnectorsRoster.tsx:124-135 ('No connectors registered.' on failure), FiltersPipeline.tsx:176-223 ('Policy lives in code.' at all five gates), approvals.py get_metrics all-zero fabrication, ApprovalsPage.tsx:1180-1302 isError drops, TimelinePage.tsx:99-100, editorial.py:479/538 broad PostgresError→calm, QaOverviewPage.tsx:784, SettingsPermissionsPage.tsx:399-441, SessionsPinnedStrip.tsx:233-235.

**Slices:** 1) ingestion pass; 2) approvals sections + metrics DegradedSources; 3) timeline/chronicles/QA/settings/sessions pass; 4) never-drop-isError lint or manifest test with grandfathered exemption list.

## Dropped ledger

Agent-proposed moves cut or deduped by the synthesizer (source agent prefixed) — recorded so
nothing silently vanishes:

<details>
<summary>136 dropped/deduped proposals</summary>

- butlers: Truthful board freshness plaque (replace hardcoded 30s caption) — cut, polish tier; batch with the ledgered roster-truth mini-epic
- butlers: Cadence clause cites its real expectation — cut (S, below the line); pair with the freshness plaque when the roster-truth epic forms
- butlers: Board keyboard loop (cell traversal + focused restore) — cut; part of the fleet-wide keyboard-adoption debt named in themes
- butlers: Reason-aware deep links from needs-you clauses — cut, capability polish
- butlers: One roster vocabulary (staffers in all-clear, Active vs RUNNING) — cut, S copy fix for the roster-truth batch
- butlers: Per-name restore pending truth — cut; adjacent to the 07-12 eligibility-mutation-honesty ledger item, ship together
- calendar: Adopt the owner-timezone contract across the workspace — near-miss cut (spec-backed major, three tz authorities); file as a standalone bead next cycle
- calendar: Ambient pending-proposals badge + palette verb — cut; coordinate with bu-ckkpz before building
- calendar: Source-toggle optimistic-lie revert — cut, two-line fix; batch into any calendar PR
- calendar: Keyset-walker truncation honesty (forged has_more:false) — cut, latent (>10k entries)
- calendar: Tokenize the overlap amber edge — cut; fold into open bu-rqbcx sweep
- chronicles: Coverage-honest briefing (feeder_dark into state/headline/voice) — near-miss cut; strongest chronicles item, revisit next run
- chronicles: Replace 'Longest gap' KPI with shared untracked primitive — cut
- chronicles: Classify-before-swallowing in briefing sub-queries — merged into ranked #15
- chronicles: Whole-day fetch integrity (paginate to meta.total) — cut
- chronicles: SourceStateBadgeStrip time-scope + error honesty — merged into ranked #15
- chronicles: Every attention clause gets a door — cut
- chronicles: One axis contract for recent-days totals — cut
- chronicles: Voice provenance chip — cut, polish
- education: Wire GET /flows lifecycle plaque + build-stall deadman — cut; strongest education item, bundle if an education epic forms
- education: Curriculum request visible lifecycle + TTL + cancel — cut, same bundle
- education: Un-orphan abandoned/completed curricula — cut, same bundle
- education: Session doors on quiz rows — cut
- education: Review rows become doors with keyboard path — cut
- education: Honesty-and-dialect sweep (owner-tz dates, isError, Dispatch chips) — cut; bundles 07-12 debt, same bundle
- notifications: Owner-tz inclusive since/until filters — cut S; port alongside ranked #6's shared owner-tz helper
- notifications: Verdict opener doors carry their window + one failed definition — cut
- notifications: Resolve recipients to contacts + butler picker — cut
- notifications: 07-12 drive-bys (stats isError + optimistic ack repaint) — merged into ranked #15
- system: Complete the verdict union + freshness contract — cut as own move; board-isError slice noted for ranked #15's orbit
- system: Every verdict problem line becomes a door + failed-insights drawer — cut
- system: One software-identity card (merge Version/Uptime/Deployment) — cut; adjacent to ranked #2
- system: Delete orphaned heartbeat endpoint/hook spine — cut; file as S cruft bead
- system: Egress owner-gate honesty (resolve caller or retire theater) — cut, decision-first bead
- system: Instance flight recorder (24h event timeline) — cut L; revisit after ranked #2 lands
- timeline: Keyboard spine (j/k, palette verbs) — cut; keyboard-adoption debt theme
- timeline: Honest secondary sources bundle — merged into ranked #15
- timeline: Notification spine door + running vocabulary + filter-aware empty — cut
- approvals: Complete the dossier's outcome half — partially merged into ranked #10 (decision record); execution-trace remainder cut
- approvals: Route palette Approve through the undo-window contract — cut; file as S bead (real double-decision hazard)
- approvals: Live clock (ticking countdowns + re-ranking) — cut
- approvals: /decisions self-refresh reconcile poll — dedup; hand the key to in-flight bu-01r64 manifest slice / bu-ckkpz owners
- butler-detail: Adopt timeline unwrap in activity feed — merged into ranked #4
- butler-detail: Thread board degraded flags through Overview panels — merged into ranked #15's orbit (butler-detail slice)
- butler-detail: Every Overview number a door + land the /approvals/:id link — cut
- butler-detail: j/k/a/d triage on butler-scoped approvals — cut, keyboard debt theme
- butler-detail: Sibling nav that never dies — cut
- butler-detail: Demote Pause to outline — cut, polish
- cross:a11y: Global focus-visible floor + outline-none lint (Secrets Passport) — cut this run; top a11y candidate, file as bead
- cross:a11y: One announcement primitive (make chat audible) — cut this run; flagship a11y gap, file with the chat epic (bu-p6ey8 orbit)
- cross:a11y: Populated-state axe second pass — cut L; complements open bu-nzeyd
- cross:a11y: Icon-button-needs-label lint + 7 fixes — cut, batch with focus-floor bead
- cross:a11y: ChartFrame accessible-chart generalization — cut
- cross:a11y: Focusable Hint primitive (retire title=) — cut
- cross:a11y: Skip-to-content link — cut, S quick win for the a11y bead
- cross:speed: Never-blank lint + sweep of four escaped surfaces — near-miss cut; pairs with ranked #15's enforcement gate
- cross:speed: Intent-prefetch generalization (PrefetchLink, sidebar warming) — cut
- cross:speed: ['decisions'] update-path gap — dedup; hand evidence to in-flight bu-01r64 coverage-manifest slice
- cross:shell: Scope-ranked command registry — near-miss cut (palette regression is real); file as S bead
- cross:shell: Shell-auto shortcut hint strip in Page primitive — cut
- cross:shell: Keyboard parity for telemetry ledgers + detail verbs — cut, keyboard debt theme
- cross:shell: First-class chat chord — cut; note for chat epic bu-p6ey8
- cross:shell: Roster-gate the whole route spine — cut
- cross:shell: Empty-states-teach sweep with manifest gate — cut
- cross:visual: Close the cool-hue guard hole + categorical adoption sweep — near-miss cut; file as the next visual-language bead
- cross:visual: Retire filled-badge status onto StateDot/kind-tag — cut
- cross:visual: Title/KPI weight unification lint — cut
- cross:visual: One pending vocabulary (kill spinners + bouncing dots) — cut; TypingIndicator swap noted for chat epic
- cross:visual: Meter primitive + global :focus-visible — cut; focus rule folded into the a11y bead
- dashboard: One attention model, served — cut L; the right end-state but heavy; revisit after bu-ckkpz lands
- dashboard: Decision-grade approval rows (subject titles + clamp) — dedup; coordinate with bu-ckkpz before cutting rows
- dashboard: Recut the cost band into editorial dialect — cut; coordinate with ranked #7 and the ledgered costs-pair amnesty
- dashboard: Complete the keyboard loop (Enter/o) + unify butlers-outage door — cut
- eco:collaboration: Life-event ripple (domain-event pub/sub) — cut L; flagship capability, needs an owner-gated RFC bead
- eco:collaboration: Delegation onto the trace spine + wake the asker — cut; would be a third dropped-ledger promotion — strong new evidence (0 rows ever, no session linkage), first in line next run
- eco:collaboration: Briefing→insight bridge (all seven voices, zero-LLM) — cut S; attractive, blocked on ranked #3 fixing delivery first
- eco:collaboration: Single-voice reply composition for multi-domain messages — cut M, RFC-shaped
- eco:collaboration: Owner-state plaque + suppression doors (context-bus visibility) — near-miss cut; partially served by ranked #3's honest parked envelopes
- eco:collaboration: Cross-butler burst coalescing at Messenger — cut; depends on ripple/park moves
- eco:connectors: Singapore civic environment feed (NEA/data.gov.sg) — cut, roadmap lane bu-dyq22
- eco:connectors: Voice-call metadata ledger — cut, roadmap lane + privacy review first
- eco:connectors: Standing owner-documents registry with expiry radar — cut; manifesto-debt bead for the connectors lane
- eco:connectors: LTA commute disruption feed ('commuting' signal) — cut, roadmap lane
- eco:connectors: HIBP breach-event perception — cut; owner-decision bead (complements fenced bu-lezse)
- eco:connectors: Repair phantom energy Q&A tools — cut; file as S bead (skill instructs nonexistent tools)
- eco:inference: Two-lane spawn scheduler (interactive never queues behind maintenance) — cut M; strong follow-on once ranked #1 drains the load
- eco:inference: Single-round-trip dispatch gate + MTD counter — cut
- eco:inference: Memory maintenance on api-direct lane + dead-band — merged into ranked #1 slice 5
- eco:inference: Spawn-phase latency spine (persist measured spawn_latency_ms) — cut S; prerequisite evidence for the warm-pool RFC, file as bead
- eco:inference: Warm-worker session continuity RFC — cut L, owner decision
- eco:knowledge-graph: Cited provenance (per-artifact episode citations) — near-miss cut M; first KG candidate next run
- eco:knowledge-graph: Evidence digest that outlives the episode — cut; pairs with cited provenance
- eco:knowledge-graph: Close the rule application loop (spec-mandated table dropped) — cut; spec-drift bead worth filing
- eco:knowledge-graph: Entity-pivot fleet dossier — cut
- eco:knowledge-graph: Fleet Knowledge failure honesty + read metrics — cut S
- eco:proactivity: Earned-voice ratchet (owner-approved budget raise) — cut; sequenced after ranked #3 restores delivery
- entities: Open the periphery (tier-1500 reachable from Plex) — cut M; strongest entities item, next-run candidate
- entities: Ego-exclude concentration + wake staleness axis — cut S
- entities: Close the archive lifecycle (unarchive + undo) — cut M
- entities: Keyboard parity across the tab family — cut, keyboard debt theme
- entities: One ENTITY_TYPES vocabulary — cut, S bead
- health: Attribution-complete freshness spine + per-source deadman — write-path stamping merged into ranked #12; chip staleness vocabulary cut
- health: Owner-tz trend buckets + variance-honest rows — cut S; 4th instance of the tz class, quick bead
- health: One Dispatch dialect + URL filters + palette logging verbs — cut
- health: Nutrition closure loop (butler-estimated macros) — cut
- ingestion: Truthful remediation verbs (auth vs transport failure split) — cut M; good next-run candidate
- ingestion: Timeline ledger triage keys via useListTriage — cut, keyboard debt theme
- ingestion: Retire orphaned cross-summary endpoint + dead vocabulary — cut; S cruft bead
- ingestion: Connector detail perceived-performance (unblock first paint) — cut S
- issues-audit: Keyset cursor pagination for /api/audit-log — cut M
- issues-audit: Predicate-complete doors (error-text filter carried by issue links) — cut
- issues-audit: Persist selected-but-absent butler pills + name palette ack target — cut S
- memory: Keyboard-first ledger (search focus key, j/k, in-place re-ink) — cut, keyboard debt theme
- memory: Activity rows become doors (spec amendment) — cut; route through spec workflow when picked up
- memory: Per-butler lens on the house ledger — cut
- qa-system: Give the QA verdict a clock (tick the deadman) — cut S
- qa-system: Every QA aggregate becomes a door — cut
- qa-system: Keyboard claim⇄evidence traversal — cut, keyboard debt theme
- qa-system: Degraded-note vocabulary for rail/dossier — merged into ranked #15
- qa-system: Truth in cadence/liveness (interval wiring, poll running patrols, page rail) — cut
- sessions: Make the keyboard loop real (non-modal dossier preview) — near-miss cut M; fabricated-affordance defect, file as bead
- sessions: Unify parser/capture tool-call fingerprinting — near-miss cut M; live evidence-integrity defect on the dossier, file as bead
- sessions: Debounce + replace:true free-text filters — cut S, quick bead (repo precedent x3)
- sessions: Rolling verdict window (quantized cutoff) — cut S
- sessions: Tri-state pinned failure excerpts — merged into ranked #15
- sessions: Outcome hygiene in ToolCallTimeline (never infer from args) — cut S; near-free after the fingerprint fix
- sessions: First-class /sessions/:id identity + shortcuts — cut
- settings-secrets: Honest approvals telemetry (metrics DegradedSources + null-not-zero chain) — merged into ranked #15 slice 2
- settings-secrets: Stable attention identity (id-keyed items, inline overflow) — cut M
- settings-secrets: Permissions page apiFetch/QueryBoundary port — audit-reel one-liner merged into ranked #15; full port cut
- settings-secrets: Models page live truth + one dialect — cut
- settings-secrets: Passport copy/affordance honesty pass — cut S
- spend: Make 'cap this schedule' real (live purpose vocabulary + cap door) — cut M; strong follow-on to ranked #7
- spend: Keyboard parity for the spend control loop — cut S
- qc:ux-truth: Aging re-escalation for unactioned infra escalations (calendar deadman first consumer) — cut M; good pattern, file when the 110-day sync outage itself is actioned
- qc:routing-delivery: Archive the completed context-bus-producers openspec change + sweep done-but-unarchived changes — cut; file as S governance bead (th-projects lifecycle closure), near-mechanical

</details>

## Run notes

- Run executed 2026-07-17 → 2026-07-19 across multiple usage windows; fan-out results were
  harvested from the workflow journal and synthesized by a standalone agent reading the
  harvested JSON (no re-runs).
- The dev-stack outage mid-run degraded later audits from live to source confidence; confidence
  level is stamped per finding in the data JSON.
- Beads: gated epic filed per the fleet-trigger protocol — see the epic/gate ids in the final
  report and the reference memory `reference-jarvis-pursuit-2026-07-17`.
