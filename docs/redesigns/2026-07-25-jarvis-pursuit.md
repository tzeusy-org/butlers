# JARVIS Pursuit — Run 07 (2026-07-25)

Seventh recurring generative audit pursuing a world-class JARVIS-like system across the
Butlers ecosystem. The fleet was mid-execution on run-06's epic `bu-27dxl` (15 moves)
throughout this run, so run 07 opened with a QC pass over the merged landings (24 slices
graded), re-measured every page and cross-cutting surface for movement, and ran six
ecosystem ideation lenses (connectors, inference, knowledge-graph, cross-butler,
proactivity, interfaces) over the ground the fleet is not churning.

**Orchestration note:** 29 agents in 12 hourly batches of 2–3, staggered over ~12 hours;
QC + page audits on `sonnet/medium`, cross-cutting sweeps + ecosystem lenses on
`opus/high`, synthesis on `fable`. Every batch checkpointed to a durable harvest file.
Workflow lesson worth one line: on resume, workflow args are ignored (a 100% cache-hit
no-op) — bake the batch pointer into the script (`DEFAULT_BATCH`) before resuming with
`resumeFromRunId` and no args. Completed 2026-07-25.

**Data:** full per-agent structured output in
[`2026-07-25-jarvis-pursuit-data.json`](2026-07-25-jarvis-pursuit-data.json).
Access pattern: `jq '.audits[] | select(.page=="<label>")' docs/redesigns/2026-07-25-jarvis-pursuit-data.json`
(labels: `qc:*`, `page:*`, `cross:*`, `eco:*`); synthesis under `.synthesis`.

## North star

Five-second fleet verification; earned calm — nothing fabricated, failure never impersonates
health, staleness never wears current-data authority; every clause a door on an unbroken
trace spine (signal → session → evidence); keyboard-first; one visual language (Dispatch)
built by one hand; plus the th-engineering bar and the th-projects lifecycle mandates.

## Headline

The honesty doctrine won at page level: 13 of 16 page surfaces stand solid after synthesis
downgrades, 19 of 24 QC'd run-06 slices landed as-designed, and for the first time in seven
runs **zero surfaces regressed**. Two new failure classes replace the old ones. First,
**landed-on-paper**: `bu-27dxl.1.1`/#3512 is tracked as landed but shipped zero runtime code —
the Chronicles truth contract is an unarchived openspec doc (`git show 55506cf1e --stat`:
628 insertions, all under `openspec/changes/`), while `editorial.py:100-131` still emits only
`urgent|busy|mild|quiet` and `ChroniclesPage.tsx:263` defaults missing state to "Quiet day."
Second, **durable truth with no door**: the two genuinely excellent new ledgers
(`infra_conditions`, `delegation_ledger` wake states) dead-end in Postgres with no API or
frontend surface — an L0→L3 escalating outage and a `callback_failed` delegation are visible
only via psql — and a `dead_lettered` notification reaches no owner surface at all. With the
pages largely honest, the frontier has moved inward: the ecosystem is still **edge-triggered,
fragment-level, and forgetful** — no level conditions for the owner's world, no synthesis at
the digest, no reinforcement in the knowledge graph, a conversational layer that cold-starts
every turn, and a router that measures latency then throws the measurement away.

## QC verdict on the run-06 landings

24 merged slices graded: **19 as-designed, 4 with-gaps, 1 not-landed, 0 regressed, 0 failed.**

| Slice | Grade | Note |
|---|---|---|
| #3512 bu-27dxl.1.1 Chronicles narrative truth | **not-landed** | openspec docs only; zero src/ or frontend/ diff; `classify_state()` unchanged (`editorial.py:100-131`) |
| #3515 bu-27dxl.2 ouroboros placeholder prevention | as-designed | `storage.py:1063` skip + `context.py:87` recall filter, end-to-end |
| #3516/#3517 audit attribution + migration-chain CI all roots | as-designed ×2 | AST-scan guard live-confirmed (2 passed); workflow-YAML-parsing anti-drift test |
| #3518 Settings Console attention doors | as-designed | real navigate() wiring, per-subsystem degraded handling, live bus deltas |
| #3520/#3558 Education selection + a11y choreography | as-designed ×2 | real node data into NodeDetailPanel; shared modal choreography |
| #3521 decisions structured context | as-designed | fails closed to `structured_details_available=false` + specific reason |
| #3523 overview cost errors honest | as-designed | isError passed through; SourceDegradedNote instead of $0.00 |
| #3525 entity tombstone redirect + merge confirm | as-designed | survivor redirect + aria-live + AlertDialog confirm |
| #3526/#3527 system verdict sources + permission seeds | as-designed ×2 | DbSize/Egress feed the verdict; guarded legacy-seed retirement |
| #3471 whole-population stalled radar | as-designed | unbounded aggregate via `meta.stalled_count`, verdict clause wired |
| #3531 accessible shell primitives | with-gaps | floor + skip link real and tested; InlineActionLink adopted in 7 files vs 336 raw affordances |
| #3532 keyboard interaction chassis | as-designed | real registry adopted across 18 list-heavy pages |
| #3533 perceived-performance generalization | with-gaps | all 42 routes lazy + background-poll off app-wide; poll lint covers 8/57 files (self-documented deferral) |
| #3522/#3538 infra_conditions ledger + lifecycle | with-gaps | real, race-safe, integration-tested — zero API router, zero frontend |
| #3540/#3542/#3543 deadman migration, QA suppression, supervised loops | as-designed ×3 | full condition lifecycle wired; Gate 5.5 suppression tested; 9 lifespan loops supervised |
| #3514/#3539/#3541 delegation wake spec, core group, birthday-gift producer | as-designed ×3 | D1–D5 implemented faithfully; producer live with advisory-lock dedup — retires run-06's "0 rows ever" |
| #3535 durable delegated-answer wake routing | with-gaps | backend genuinely real (crash/duplicate/conflict handling); `wake_state` columns never surfaced by the read API or any frontend |

## Tier board (movement vs 2026-07-22 baseline)

| Surface | Verdict | Movement | One-line reason |
|---|---|---|---|
| dashboard | solid | unchanged | cost honesty landed (#3523); top-session drill-down still discards `session_id` (`TopSessionsTable.tsx:105`) |
| butlers roster | solid | **improved** | run-06 chrome all-clear closed; detail Pause/Resume bypasses the board's undo window (`ButlerDetailActions.tsx:143`) |
| sessions + timeline | solid | unchanged | /timeline zero shortcuts; j/k never moves DOM focus (`SessionTable.tsx:41` vs impl); Load-older fails silently |
| chronicles | functional | **improved** | voice-dump/date-mismatch fixed mid-flight; truth contract not landed; Refresh invalidates 5/11 families (`ManualRefreshButton.tsx:44`) |
| education | **weak** | unchanged | four core widgets drop isError — outage renders "still building it" (`MindMapGraph.tsx:96`); two badge color systems |
| entities | solid | **improved** | tombstone redirect + merge confirm landed (#3525); Plex halo failure = "no non-person entities" (`PlexPage.tsx:1475`) |
| calendar | solid | unchanged | verdict clauses are text not doors (`CalendarVerdictOpener.tsx:27`); event move pointer-only |
| ingestion | solid | **improved** | reauth/pause gaps closed; archive one-click, no confirm, no UI path back (`ArchiveCandidatesList.tsx:122`) |
| issues + audit | solid | **improved** | audit log zero bus wiring (`use-audit-log.ts:9`); `request_id` inert text (`AuditLogTable.tsx:375`) |
| attention (approvals/notifs/decisions) | solid | **improved** | #3521/#3471 landed; decision rows fully unlinked (`DecisionsPage.tsx:99-219`); ack-all no confirm |
| memory | solid | new | 2/5 attention-rail conditions zero out on error (`AttentionRail.tsx:180`) |
| health | solid | new | insight links discard record context (`HealthOverviewPage.tsx:276`); six add/log actions keyboard-dead |
| spend | solid | new | transient poll failure clobbers good cached data (`SpendPage.tsx:883`); drag-only reorder |
| settings + secrets | solid | **improved** | console doors real (#3518); audit reel + webhooks render calm empties on failure (`SettingsPermissionsPage.tsx:414,954`) |
| qa | solid | new | absent-vs-unreachable conflated as "not found" (`QaInvestigationDetailPage.tsx:28`) |
| system + chat | functional | unchanged | tiles honest; **Stop still doesn't stop** (`FloatingChatWidget.tsx:343`, `conversations.py:361`) — carryover critical |
| cross: shell | solid | unchanged | palette verbs never generalized — registry docstring cites never-built commands (`command-registry.tsx:6`); ⌘K hover-only |
| cross: visual | solid | **improved** | fork narrowed; no shared status-color registry, ~15 hand-rolled maps (`StateDot.tsx:56` private) |
| cross: speed | solid | unchanged | lazy-all + poll-default landed; lint 8/57; zero route prefetch (`Sidebar.tsx`) |
| cross: a11y | solid | unchanged | floor + skip link landed (#3531); Gantt SVG keyboard-dead; passport strips focus outlines; 5 raw `<th>` tables |
| eco: reliability/conditions | functional | **improved** | condition lifecycle live end-to-end (bu-27dxl.6.x) but reachable only via psql |
| eco: cross-butler collaboration | functional | **improved** | wake loop + real producer landed, "0 rows ever" retired; `wake_state` invisible; digest still a fragment list |
| eco: knowledge-graph | **weak** | unchanged | graph doesn't learn: no recall reinforcement (`storage.py:1809`), no corroboration axis (`storage.py:2478`), no predicate promotion |
| eco: connectors/perception | functional | unchanged | email-exhaust perception; no bank feed, flight status, or atmosphere signal behind explicit manifesto promises |
| eco: interfaces/conversation | functional | unchanged | `dead_lettered` notify invisible to owner; channel threads stateless; `message_inbox` substrate unsurfaced |
| eco: inference/model-routing | functional | new lens | `duration_ms` measured then discarded (`spawner.py:1827` vs `:313`); blind round-robin; 5–6 serial pre-spawn round-trips |
| eco: proactivity | functional | new lens | edge-triggered only; context bus never consulted at delivery; level lifecycle infra-only (`infra_conditions.py:1-38`) |

**Movement summary: 10 improved, 0 regressed, 11 unchanged, 6 new.** Run-06 marked
butlers/entities/ingestion/cross:visual "regressed" — all four verifiably improved this run
via landed `bu-27dxl` slices.

## Systemic themes

1. **Landed-on-paper.** A slice can close its bead with zero runtime code: #3512's commit
   touches only `openspec/changes/` (`git show 55506cf1e --stat`), yet the program lists it
   landed while `editorial.py:100-131` and `ChroniclesPage.tsx:263` still fabricate quiet.
   The epic ledger needs a grading rule, not just this one fix.
2. **Durable truth with no door.** `infra_conditions` (554 lines, zero router/frontend, and
   Gate 5.5 now suppresses QA dispatch on it invisibly), `delegation_ledger` wake columns
   absent from the read API, `dead_lettered` in `001_messenger_tables.py` reaching no owner
   surface. New ledgers must ship with their door and full column parity, or the trace spine
   ends at psql.
3. **Generalize means only-the-subset-touched.** Poll lint 8/57 files
   (`eslint.config.js:213-232`, "~140 other call sites"); InlineActionLink 7 files vs 336 raw
   affordances; `command-registry.tsx:6-8` docstring citing palette verbs never built; ~15
   hand-rolled status→color maps around a private canonical (`StateDot.tsx:56`); empty-state
   action slot used 6/19.
4. **Last-hop identity dropped.** `TopSessionsTable.tsx:105` links the butler aggregate not
   the session; `AuditLogTable.tsx:375` renders `request_id` inert; `DecisionsPage.tsx:99-219`
   has zero links; `TimelineEventDrawer.tsx:115` links a bare list; health insight hrefs and
   ingestion event rows dead-end.
5. **Fabricated calm retreated into leaf widgets.** `MindMapGraph.tsx:96` ("still building
   it" on outage), `PlexPage.tsx:1475`, `SettingsPermissionsPage.tsx:414/954`,
   `SpendPage.tsx:883` (error clobbers cached data), `AttentionRail.tsx:180`,
   `ChroniclesDrilldownPanel.tsx:113` (failure = quiet day). The degraded-source convention
   stopped one layer above the leaves.
6. **Fabricated control.** Stop detaches the client while the butler keeps spending
   (`conversations.py:361`); Chronicles Refresh claims all, invalidates 5/11; one-click
   archive with no path back; ack-all with no confirm; detail Pause bypassing the undo window.
7. **Keyboard chassis broad, hottest loops dead.** 18 pages on the shared registry, yet
   /timeline and /system register zero shortcuts, sessions j/k moves no DOM focus, the Gantt
   SVG and secrets passport are keyboard-dead, health's six add actions and spend's reorder
   have no keyboard path.
8. **Edge-triggered, fragment-level, forgetful.** The intelligence substrate does not
   compound: level conditions exist only for infrastructure (`infra_conditions.py:1-38`), the
   digest is a flat fragment list (`insight-delivery/spec.md:336-345`), the context bus is
   never read at delivery, facts earn nothing from recall (`storage.py:1809`), corroboration
   never strengthens trust (`storage.py:2478`), and the router discards its own latency
   evidence (`spawner.py:1827`).

## Ranked moves (16)

| # | Move | Kind | Cost |
|---|---|---|---|
| 1 | Land the Chronicles narrative-truth contract for real (bu-27dxl.1.1 shipped zero runtime code) | engineering | M |
| 2 | Make Stop actually stop: server-side session cancellation | engineering | M |
| 3 | Open the doors on the two new ledgers (infra_conditions panel; delegation wake_* API parity + surface) | ux | M |
| 4 | Delivery-receipt spine: dead-lettered notifications become a visible attention door | ecosystem | M |
| 5 | Fabricated-calm leaf sweep + Chronicles Refresh completeness (+ `?? []` coercion lint) | ux | M |
| 6 | Owner condition ledger: generalize infra-conditions lifecycle to owner-facing standing concerns (+ ingestion-time watchers, calendar radar) | ecosystem | L |
| 7 | Last-hop door repair pack (session ids, request_id, decision rows, notification deep-links, health/ingestion rows) | ux | M |
| 8 | Conversational spine v2: channel-agnostic conversation anchor + provider session resume + first-token streaming + action receipts | ecosystem | L |
| 9 | Insight broker v2: correlated-candidate synthesis + presence-aware delivery (context-bus gating, first-active briefings) | ecosystem | M |
| 10 | Domain-event bus: standing cross-butler subscriptions (pub/sub beyond ask/answer, reusing wake plumbing) | ecosystem | L |
| 11 | One safety envelope for consequential actions (confirm-or-undo primitive, pending-safe button) | ux | M |
| 12 | Keyboard chassis completion + primary-action manifest (hot loops, focus reality, visible ⌘K, chords) | ux | M |
| 13 | Inference efficiency pack: persist duration_ms, cost×latency×success routing, one-round-trip gates, speculative prewarm | engineering | M |
| 14 | Knowledge-graph trust mechanics: recall reinforcement, corroboration axis, active re-verification, predicate promotion | ecosystem | L |
| 15 | Population-coverage gates: poll lint repo-wide, exported status-token registry, a11y off-primitive lint, nav prefetch | engineering | M |
| 16 | Authoritative-source perception tier: weather/AQI (S), flight status (M), SimpleFIN bank feed (L) | ecosystem | L |

Overlap flags (full detail in `.synthesis.ranked_moves`): move 1 **is** `bu-27dxl.1.1`
(reopen, don't duplicate); moves 3/6 extend landed `bu-27dxl.5.x/.6.x`; move 5 continues
`bu-27dxl.7.x` below the chrome; move 8 is the ledger/provider layer beneath in-flight
`bu-27dxl.9` (coordinate slices); move 9 integrates with `bu-ckkpz` Decision Desk; move 12
extends landed #3532; move 14 is sequenced **after** in-flight `bu-27dxl.4` lands; move 15
completes the exact with-gaps subsets of `bu-27dxl.11/.12/.13`; move 16 is distinct from
in-flight `bu-27dxl.15`. Nothing here duplicates an open slice.

## Dropped (nothing silently vanishes)

16 agent-proposed moves were cut or folded at the line — cross-domain missions/Chief-of-Staff
(deferred until the event bus proves standing coordination + a manifesto-ownership decision),
attention/time-budget broker, derived-advisory layer (folded into move 10), standing
subscriptions (split into moves 6/10), cross-butler entity dossier, RFC-0010 cross-schema fact
read federation (**held for an explicit owner decision — moves the schema-isolation boundary;
do not fleet-dispatch**), per-entity fact compaction (folded into move 14), Oura wearable,
Google Photos evidence, shipment tracking (manifesto-ownership gate), multi-modal inbound
capture (behind bu-27dxl.9 voice), conversational capability discovery, answer memoization
beyond routing decisions, presence-gated briefing cadence (folded into move 9), proactive
calendar radar (folded into move 6), and the standalone Refresh fix (folded into move 5).
Full list with reasons: `.synthesis.dropped`.

## Corrections to standing memory

- Run-06 tiered butlers/entities/ingestion/cross:visual as **regressed**; all four verifiably
  **improved** this run via landed slices. Retire the regressed reading.
- `bu-27dxl.1.1` (#3512) is tracked as landed but shipped zero runtime code. Correct the epic
  ledger; adopt the grading rule that a commit touching only `openspec/changes/` is not landed.
- Run-06's "delegation ledger 0 rows ever" is resolved at the data layer (#3541 producer live,
  advisory-lock dedup). The residual gap is visibility, not production.
- Run-06's chronicles headline (raw LLM planning monologue as owner voice, mismatched date) no
  longer reproduces — fixed mid-flight. The chronicles deficit is now the unlanded truth
  contract plus Refresh completeness.
- The "stale move-beads in bd ready" memory note gains its converse: verify landings via git
  diff content, not bead state — this run found a closed bead whose code does not exist.
