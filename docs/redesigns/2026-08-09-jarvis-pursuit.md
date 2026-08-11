# JARVIS Pursuit: Run 08 (2026-08-09)

Eighth recurring generative audit pursuing a world-class JARVIS-like system across the
Butlers ecosystem. This run first graded all 16 moves from run 07, then re-measured 16
page groups, four cross-cutting qualities, and six ecosystem lenses against current source,
binding doctrine, current backlog, and a read-only live stack.

The audit used 29 fresh-context agents in 12 strict hourly batches of two or three. Page and
QC work used `gpt-5.6-terra/medium`; cross-cutting and ecosystem work used
`gpt-5.6-sol/high`; synthesis was performed only by the primary agent. The first batch
started at `2026-08-09T05:21:15Z` and the final audit checkpoint completed at
`2026-08-09T16:38:57Z`. Every batch was durably checkpointed before the next prompt set was
rebuilt.

The audited source and live runtime were both
`e91a989503f1535e5605c685fcbc2331f905e772`. Runtime inspection was GET-only and
read-only: no database writes, connector actions, replay, restart, or external side effects.
Live identifiers and machine-local paths in the structured evidence are replaced with typed
placeholders; counts, roles, health states, and empty-state semantics are preserved.

**Data:** the full structured output from all 29 lanes is in
[`2026-08-09-jarvis-pursuit-data.json`](2026-08-09-jarvis-pursuit-data.json). Query one
lane with:

```bash
jq '.audits[] | select(.page == "<label>")' \
  docs/redesigns/2026-08-09-jarvis-pursuit-data.json
```

Labels use `qc: ...`, `page: ...`, `cross: ...`, and `eco: ...`; root synthesis is under
`.synthesis`. The requested artifact-design capability was not installed in this environment,
so this canonical Markdown dossier is the readable artifact and the JSON is its machine-readable
companion.

The synthesis began with **59 distinct raw candidate titles**: 5 QC `candidate_moves` and 54
non-QC `moves`. It then deduplicated and prioritized those candidates into the 15 ranked moves
below. Recheck the machine-readable denominator with:

```bash
jq -e '
  .synthesis.candidate_move_totals as $totals
  | ([.audits[].candidate_moves[]?.title, .audits[].moves[]?.title] | unique) as $titles
  | ($titles | length) == $totals.distinct_raw_titles
  and $totals.distinct_raw_titles == 59
  and $totals.qc_candidate_moves == 5
  and $totals.non_qc_moves == 54
' docs/redesigns/2026-08-09-jarvis-pursuit-data.json
```

## North star

Five-second fleet verification with earned calm: nothing fabricated, failure never
impersonates health, staleness never wears current-data authority, and every consequential
clause is a door on an unbroken signal to session to evidence spine. The interface is
keyboard-first, follows Dispatch as if built by one hand, respects specialist manifesto
ownership, and meets the repository engineering and lifecycle bars.

## Headline

The dashboard is broadly mature, but authority leaks at boundaries. Of 16 page groups,
**9 are solid, 6 functional, and 1 weak**; all four cross-cutting qualities remain solid.
The prior program also held up well: **10 of 16 run-07 moves landed as designed, 5 landed
with gaps, 1 remains the known not-landed block, and no whole move regressed**.

The frontier has moved below components. Butlers records rows, sockets, model choices,
facts, events, accepted tasks, and proactive candidates, but too often cannot prove the
scope, freshness, capability fit, coverage, or final outcome that gives those records
authority. Four current defects make the pattern concrete:

- Long conversations route against their oldest 20 messages instead of their latest 20.
- A weekly cron is projected as 30 monthly runs, materially overstating Spend.
- Acknowledging a continuously down butler is undone by the very next Issues poll.
- An open but silent event socket still earns green `Live` and five-minute polling.

The ecosystem version of the same failure is a missing receipt. A curriculum `202`, a
scheduled domain-event wake, a successful runtime return, or a quiet proactive broker is
not proof that useful owner-facing work completed.

## QC verdict on run-07 landings

Sixteen moves graded: **10 as-designed, 5 landed-with-gaps, 1 not-landed, 0 regressed**.

Confidence is assigned to the central QC claim, not merely to the presence of a supporting
GET: **live-confirmed** means the claim was directly observed through a read-only live check;
**source-confirmed** means a decisive source/test trace proves it; **inferred** is reserved for
claims without either decisive observation. The per-finding and per-move grades live in the data
artifact.

| Move | Grade | Confidence | Current evidence |
|---|---|---|---|
| 1. Chronicles narrative truth | landed-with-gaps | source-confirmed | Coverage-first narrative states are real; `voice_source=templated` is live but not rendered. |
| 2. Make Stop actually stop | as-designed | source-confirmed | Message-scoped cancellation durably fences invocation and waits for runtime acknowledgement. |
| 3. Condition and delegation ledger doors | as-designed | source-confirmed | System conditions, per-butler delegation rows, and wake-failure attention are live and availability-aware. |
| 4. Delivery receipt spine | landed-with-gaps | source-confirmed | Retry/Escalate are real and atomic, but rejected recovery has no visible result. |
| 5. Fabricated-calm leaf sweep | landed-with-gaps | source-confirmed | Full Chronicles family invalidation landed; point-event failure still becomes empty map/scrubber evidence. |
| 6. Owner condition ledger | as-designed | source-confirmed | Durable owner conditions, reconciliation, ageing, and recovery surfaces are present. |
| 7. Last-hop door repair | as-designed | source-confirmed | Notification, request, session, and health destinations preserve the proven identity. |
| 8. Conversational spine v2 | as-designed | source-confirmed | Cross-channel anchor, sticky routing, provider resume, SSE, and durable Stop are real. |
| 9. Insight broker v2 | landed-with-gaps | source-confirmed | Broker controls and clustering are real; the System tile bypasses owner time. |
| 10. Domain-event bus | landed-with-gaps | live-confirmed | Durable bus and tests exist; two live Travel-to-Finance rows are `failed_permanent`. |
| 11. One safety envelope | as-designed | source-confirmed | Pending-safe confirmation, undo windows, and the raw-confirm lint are real. |
| 12. Keyboard chassis completion | as-designed | source-confirmed | Shared route/action/shortcut registries and targeted hot loops landed. |
| 13. Inference efficiency | as-designed | source-confirmed | Evidence routing, cache, quota/ceiling fold, and speculative prewarm landed. |
| 14. Knowledge-graph trust mechanics | not-landed | source-confirmed | Still intentionally blocked as `bu-ep4ks.14`; do not duplicate it. |
| 15. Population coverage gates | as-designed | source-confirmed | Named polling, shared state/keyboard primitives, and route prefetch enforcement landed. |
| 16. Authoritative-source perception | as-designed | source-confirmed | Atmosphere feed and UI are real; unconfigured state is reported honestly. |

The five gaps are narrow but important. Run-07 move 1 is no longer a docs-only landing;
that prior correction is retired. The new operational hold is move 10: current source and
tests look healthy while two live deliveries remain terminal failures. This dossier does
not authorize restart or replay.

## Tier board

Movement is relative to the 2026-07-25 run. `Reassessed down` means this audit proved a
trust defect that the prior tier missed; it does not claim a specific code change caused a
regression.

| Surface | Verdict | Movement | Reason |
|---|---|---|---|
| dashboard | functional | reassessed down | Composite startup supplies empty derivatives before sources settle, so loading impersonates a quiet fleet. |
| butlers roster | solid | unchanged | Partial board-source degradation is visible in the header but excluded from the all-clear verdict. |
| sessions + timeline | solid | unchanged | Saved-view selection can contradict URL scope; transient dossier failure lacks in-place retry. |
| chronicles | functional | unchanged | Truth states landed; provenance, point-event availability, and atomic day refresh remain incomplete. |
| education | weak | unchanged | Review/analytics failures impersonate empty state and 202 acceptance is overpromised. |
| entities | solid | unchanged | Concentration drops predicate context and calls a weight sum `touches`. |
| calendar | solid | unchanged | Filtered views inherit global source counts and unreadable overlays can become `Tomorrow is clear`. |
| ingestion | functional | reassessed down | Checkpoint identities impersonate independent offline listening connectors. |
| issues + audit | functional | reassessed down | Poll samples reopen acknowledged outages and fuzzy historical links can end in false calm. |
| attention | solid | unchanged | Decision Desk is readable but mount-time, with no live reconciliation after a successful initial read. |
| memory | solid | unchanged | No distinct new move survived deduplication against open memory-honesty work. |
| health | solid | unchanged | Condition-linked symptoms and research have no condition-scoped evidence dossier. |
| spend | functional | reassessed down | Weekly cadence becomes daily burn; routing-rule deletion is immediate and irreversible. |
| settings + secrets | solid | unchanged | Evidence is content-blind and real; matrix/catalog recovery and owner-time chronology remain incomplete. |
| qa | solid | unchanged | Detail failure becomes not-found and the case chronology mixes three clock authorities. |
| system + chat | functional | unchanged | Stop is real; `Refresh system status` does not refresh the verdict's evidence cohort. |
| cross: shell | solid | unchanged | Router, subnav, palette, chords, and prefetch remain separately synchronized projections. |
| cross: visual | solid | unchanged | Binding specs disagree on whether identity hues may encode non-identity roles. |
| cross: speed | solid | unchanged | Half-open liveness and split dynamic-navigation warmup remain structural gaps. |
| cross: a11y | solid | unchanged | Loading-only axe coverage, composed contrast, and native-control focus are not fully governed. |
| eco: connectors/perception | functional | improved, same tier | Atmosphere landed; further connector candidates remain conditional on owner use and reliability. |
| eco: inference/model-routing | functional | improved, same tier | Evidence routing landed; hard capability fit and decision receipts do not yet exist. |
| eco: knowledge-graph | weak | unchanged | Narrative-memory trust remains blocked; structural facts add evidence/coverage/time gaps. |
| eco: cross-butler collaboration | functional | improved, same tier | Standing events exist; semantic contracts and reaction outcomes do not. |
| eco: proactivity | functional | improved, same tier | Broker controls landed; quiet still cannot prove every expected detector looked. |
| eco: interfaces/conversation | functional | improved, same tier | Durable continuity landed; long-window ordering and post-session closure are wrong or absent. |

Movement summary: **5 improved within their existing tier, 4 reassessed down, 17 unchanged,
0 proven regressions**. Across all 26 non-QC surfaces, the synthesized distribution is
13 solid, 11 functional, and 2 weak.

## Systemic themes

1. **Evidence cohorts are implicit.** Dashboard calm, Butlers all-clear, Calendar scope,
   Education emptiness, Chronicles refresh, and System refresh each derive authority from a
   different source set than the content or action they describe.
2. **Accepted is not completed.** Education's 202, a domain-event wake, a conversation reply,
   and an insight candidate all need a durable final receipt rather than stronger success copy.
3. **Epistemic receipts stop below the UI.** Model selection, structural facts, and proactive
   candidates cannot fully explain fit, coverage, freshness, provenance, and outcome.
4. **Real numbers can still carry the wrong axis.** Weekly cadence becomes daily burn,
   relationship weight becomes touches, and filtered Calendar scope inherits the global count.
5. **Owner time remains optional in implementation.** At least seven operational page families
   bypass the canonical `Time` primitive, sometimes with hard-coded UTC.
6. **Generalization is projection-based.** Router, subnav, palette, chords, lazy chunks, query
   warmups, accessibility scenarios, focus policy, and visual roles can independently drift.
7. **Last-hop context still decays.** Predicates, filters, conditions, rules, and transient
   recovery are often lost at the final click even when the target record is real.
8. **Green code is not runtime recovery.** The Travel-to-Finance failures require diagnosis;
   restart or replay remains an owner-authorized operation outside this audit.

## Ranked moves

| # | Move | Kind | Cost |
|---:|---|---|:---:|
| 1 | Turn every conversation into a closed evidence ledger | ecosystem | L |
| 2 | Make Spend projections mathematically authoritative and policy deletion reversible | engineering | M |
| 3 | Make Issues a durable condition ledger with exact Audit evidence doors | engineering | M |
| 4 | Make heartbeat freshness the single event-bus health authority | engineering | S |
| 5 | Make every verdict and recovery action declare one evidence cohort | UX | L |
| 6 | Make proactive calm provable with source-run and evidence-freshness receipts | ecosystem | L |
| 7 | Make DispatchIntent, model capabilities, and resolution receipts the inference contract | ecosystem | L |
| 8 | Version domain-event contracts and close every wake with a reaction receipt | ecosystem | L |
| 9 | Make the canonical relationship graph evidence-bearing, coverage-aware, and bitemporal | ecosystem | L |
| 10 | Give curriculum requests a durable accepted-to-outcome receipt | product | L |
| 11 | Make connector fleet health runtime-instance authoritative | engineering | L |
| 12 | Make accessibility guarantees route- and state-complete | engineering | M |
| 13 | Make one typed shell-capability manifest own routing, discovery, and warmup | engineering | M |
| 14 | Finish the owner-time migration as a repository invariant | UX | M |
| 15 | Make visual token roles type-safe and spec-authoritative | design | M |

### 1. Turn every conversation into a closed evidence ledger

The oldest-page bug is immediate correctness debt: `limit=20, offset=0` fetches the first
20 rows, not the latest 20 that the route comment promises. Fix it with a stable newest-window
and keyset-backscroll contract, then close the two other ledger holes in the same spine:
idempotent post-session enrichment of assistant replies and immutable validated page grounding.
The result must preserve recent owner corrections, expose older history without loss, and open
the exact session/accounting/context evidence behind each answer.

This is the durable layer beneath `bu-27dxl.9`; it does not reopen Telegram parity or
`bu-s3qvp` terminal-action recovery.

### 2. Make Spend projections mathematically authoritative and policy deletion reversible

Replace the 24-hour sample multiplied by 30 with a deterministic monthly occurrence basis,
return projected runs and forecast basis separately from historical range cost, and guard the
live weekly case with fixed-clock tests. In the same budget-control dossier, make deletion of
a first-match routing rule an effect-reviewed action with an exact-order server-backed Undo,
or an explicit irreversible gate if retention policy forbids restoration.

### 3. Make Issues a durable condition ledger with exact Audit evidence doors

Define reachability onset, recovery, and recurrence independent of probe sample timestamps so
acknowledgement survives polling and reopens only after recovery followed by a new failure.
Expose a server-computed audit group or occurrence identity and preserve its window. A historical
audit failure must open the exact group, or state that it is outside current scope, instead of
searching a seven-day client subset and announcing no active issues.

Coordinate with `bu-9d5vp`; do not duplicate already-landed request/session doors.

### 4. Make heartbeat freshness the single event-bus health authority

Derive `healthy|late|down` from ready-state plus the last received message, close and reconnect
late sockets, and make polling cadence, the shell indicator, and announcements consume the same
clock-driven value. The backend already sends 20-second heartbeats and the client already stores
`lastEventAt`; this is a small, high-leverage authority correction.

### 5. Make every verdict and recovery action declare one evidence cohort

Introduce a typed cohort contract: named sources, settled/loading/empty/partial/failed state,
as-of, active scope, retained-data posture, and one refresh action. Prove it first on Dashboard,
Butlers, and Calendar, then use it for Chronicles/System atomic refresh, Decision Desk and
governance reconciliation, Education review/analytics, QA/session detail retry, and explicit
notification recovery outcomes.

`bu-a459g` and `bu-qwesi` remain the owners of broad query-coercion cleanup and enforcement.
This move owns the higher-level invariant that a verdict and its recovery action describe the
same settled sources.

### 6. Make proactive calm provable with source-run and evidence-freshness receipts

Register every code-defined proactive source with owner manifesto, cadence, and lateness policy;
append a run receipt even when it proposes nothing. Require each new candidate to link its run,
typed evidence, observation time, and freshness deadline. Recheck before delivery, mark stale
rather than notify, and show covered, partial, failed, and missed sources on System. Pilot Finance
and Travel before broader adoption.

Shadow certification and useful-action-window ranking follow this substrate; they are not
parallel foundations.

### 7. Make DispatchIntent, model capabilities, and resolution receipts the inference contract

Define deterministic invocation requirements for tool mode, structured output, resume, context,
deadline, consequence, and budget. Publish tested adapter capabilities, add them to the catalog,
and filter hard fit before evidence ranking. Persist a prompt-free receipt containing intent,
eligible/excluded candidates, evidence age, overrides, and winner reason; make the session model
clause a door to it.

Preserve run-07 inference efficiency. Exploration and self-escalation wait until fit and decision
evidence are first-class.

### 8. Version domain-event contracts and close every wake with a reaction receipt

Move event semantics into publisher-owned, Git-declared versioned schemas with minimization,
retention-policy references, subscriber policy, and reaction expectations. Add append-only
reaction attempts ending in `acted|ignored|deferred|failed|unreported`, with subscriber session
and typed evidence doors. Relabel transport delivery honestly as wake scheduling.

Use the live Travel-to-Finance failure as a read-only regression case. Any restart or replay is
a separate owner-authorized operation. Collaboration cases and the Home/Finance energy pilot wait
until these contracts prove reliable.

### 9. Make the canonical relationship graph evidence-bearing, coverage-aware, and bitemporal

Persist the typed evidence the structural writer already validates, through both direct and
approved writes. Add predicate/source coverage that composes to
`present|absent_proven|unknown|unavailable`, plus effective intervals distinct from assertion and
observation time. Relationship reads must return this truth packet without embedding raw source
content or crossing schema-isolation boundaries.

This is separate from blocked `bu-ep4ks.14`, which concerns learning behavior in narrative memory.

### 10. Give curriculum requests a durable accepted-to-outcome receipt

Persist request identity and lifecycle before spawning, then record trigger/session, curriculum,
calibration delivery, failure reason, and terminal timestamps. Replace the unconditional success
toast with accepted, completed, and degraded states plus session/curriculum doors. Keep the
pending-request guard idempotent. Education query availability belongs to move 5; this move owns
only accepted-work closure.

### 11. Make connector fleet health runtime-instance authoritative

Add an explicit registry role or runtime-instance relationship and backfill only from proven
producer semantics. Roster, attention, KPI, and liveness derive from executable listeners;
checkpoint/history rows remain inspectable under their parent but do not wear offline health.
Guard the real Google Health parent-plus-subidentity shape and report classification uncertainty
instead of inferring role from opaque suffixes.

### 12. Make accessibility guarantees route- and state-complete

Derive a scenario manifest from the real router, including parameterized dossiers, loaded,
degraded/empty, and consequential overlay states. Run axe and keyboard choreography against those
production states. Add computed two-theme contrast coverage, replace alpha-muted semantic text
with opaque AA-safe tokens, include native form controls in the focus floor, and reject outline
suppression without a tested replacement.

### 13. Make one typed shell-capability manifest own routing, discovery, and warmup

Declare route loader, label, keywords, family, placement, chord, dynamic/context-only policy, and
query warmups once. Derive static routing projections, subnavigation, Cmd-K, help, chords, chunk
prefetch, and data warmup from it. Keyboard intent starts immediately; pointer intent stays
debounced. Contract tests reject undiscoverable routes, untaught chords, and cold lazy detail
destinations.

Preserve `bu-zs4at`'s filtered Timeline cache-key concern when a real consumer appears.

### 14. Finish the owner-time migration as a repository invariant

Classify each displayed date as event, freshness, expiry, or date-only, migrate it to the canonical
`Time` primitive, and prove owner-zone versus browser-zone boundaries across Overview, Education,
Attention, Spend, Settings/Secrets, QA, and System insights. Add an AST guard rejecting display-time
locale calls and hard-coded zones outside the primitive or reviewed parsing-only exceptions.

### 15. Make visual token roles type-safe and spec-authoritative

Ratify one semantic role matrix across the binding design and domain specs. Butler identity hues
remain private to `ButlerMark`; state, local category, chart series, and owner-custom colors use
separate typed helpers. Migrate current exceptions, replace file allowlists with repository-wide
guards, and generate or verify the spec table from the registry so the authorities cannot diverge.

## Dropped, folded, or sequenced

- Chronicles point-event and generic query-error leaves remain real, but `bu-a459g` and blocked
  `bu-qwesi` already own the broad cleanup and enforcement substrate.
- Notification recovery error feedback was folded into move 5 and coordinated with `bu-s3qvp`.
- Timeline saved-view, session Retry, Concentration, Health condition, and connector-rule doors
  stay as exact follow-up evidence for their existing domain issues, below the ranked line.
- Inference exploration/regret budgets and guarded self-escalation wait for move 7's fit and receipt
  vocabulary.
- Specialist-led collaboration cases and the Home/Finance energy-cost pilot wait for move 8.
- Proactivity shadow certification and useful-window ordering wait for move 6.
- IMAP, Todoist, and Paperless-ngx are conditional post-v1 candidates only. Confirm actual owner
  use and the existing connector fleet's seven-day reliability before creating implementation work.
- Settings navigation remains owned by `bu-rxtyx`; this run found correctness, not a new chamber IA.
- Live Travel-to-Finance restart or replay is withheld pending explicit owner authorization.

Full drop reasons are under `.synthesis.dropped` in the data file.

## Corrections to standing knowledge

- Run-07 move 1 is now genuinely implemented in PR #3573. Its remaining gap is voice provenance,
  not the prior docs-only state.
- Stop, ledger doors, owner conditions, last-hop repair, conversation anchors, the safety envelope,
  keyboard chassis, inference efficiency, population gates, and atmosphere perception all graded
  as-designed on the audited SHA.
- Run-07 move 14 remains not landed and blocked as `bu-ep4ks.14`.
- The domain-event bus is landed but not operationally healthy end to end; live terminal failures
  prove that implementation evidence and recovery evidence are different things.
- The Tailscale certificate/data-plane failure reproduced but already has `bu-ln1v7`, `bu-zsup2`,
  and `bu-vwz4c`; it was not refiled.
- Memory produced no distinct new move after current memory-honesty work was applied as the baseline.

## Execution gate

The gated program is epic `bu-6jv4m`:

- Owner release gate: `bu-4sza7`
- Ranked moves: `bu-6jv4m.1` through `bu-6jv4m.15`, in dossier rank order
- Terminal reconciliation: `bu-6jv4m.16`

At audit completion the implementation children depended on the open gate. The reconciliation
child depends on the gate and all 15 moves. The owner closed `bu-4sza7` on 2026-08-10, releasing
the 15 implementation children; reconciliation remains sequenced behind those moves. The
documentation artifacts in this PR are the gate's required dossier and data record, and
dependency-cycle and run-08 lint checks were clean when the audit completed.

**Current release status:** the owner release action has already occurred. Closing the gate
permits only the child packets' scoped execution; it does not authorize live restart, replay,
connector mutation, secret inspection, retention-policy invention, or external side effects where
a child records a narrower approval boundary.
