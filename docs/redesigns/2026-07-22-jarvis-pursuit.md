# JARVIS Pursuit — Run 06 (2026-07-22)

Sixth recurring generative audit pursuing a world-class JARVIS-like system across the
Butlers ecosystem. Run 05's program (epic `bu-kqnum`, released 2026-07-19) was mid-execution
throughout this run (~20 slices merged, ~10 in flight), so run 06 opened with a QC pass over
the merged landings, audited the surfaces the fleet is not churning, and ran the first
dedicated sweeps in three runs on reliability and the conversational layer.

**Orchestration note:** the initial 2026-07-22 launch was killed after a usage spike
(~10 concurrent agents, 5%→80% in 15 min) and restarted the same day as 25 agents in 16
hourly batches of 1–2 (owner cap 3), staggered over ~16 hours; QC + page audits on
`sonnet/medium`, cross-cutting sweeps + ecosystem lenses on `opus/high`, synthesis inline on
the `fable` orchestrator. Every batch was checkpointed to a durable harvest file. Completed
2026-07-23.

**Data:** full per-agent structured output in
[`2026-07-22-jarvis-pursuit-data.json`](2026-07-22-jarvis-pursuit-data.json).
Access pattern: `jq '.audits[] | select(.page_key=="page:chronicles")' docs/redesigns/2026-07-22-jarvis-pursuit-data.json`
(keys: `qc:*`, `page:*`, `cross:*`, `eco:*`); synthesis under `.synthesis`.

## North star

Five-second fleet verification; earned calm — nothing fabricated, failure never impersonates
health, staleness never wears current-data authority; every clause a door on an unbroken
trace spine (signal → session → evidence); keyboard-first; one visual language (Dispatch)
built by one hand; plus the th-engineering bar and the th-projects lifecycle mandates.

## Headline

Run-05's arc pathology has graduated into systemic law: **every truth mechanism the fleet
lands is eroded by the next producer it never covered** — 7 of 17 QC'd slices sit with-gaps
for exactly that reason, including the flagship consolidation-ouroboros fix, which a second
ungated episode producer quietly reopened within days. Meanwhile the deepest deficit has
moved inward: the **knowledge graph is 89–99% untraceable** with 40–75% of its remaining
citations dangling, and the **owner's daily chronicle live-renders raw LLM planning
monologue as the owner's own briefing voice** under a mismatched date. The program that
follows is one idea applied fifteen ways: stop fixing call sites; ship population-level
coverage — ledgers, primitives, lints, and gates that the next producer inherits by default.

## QC verdict on the run-05 landings

17 merged slices graded: **10 as-designed, 7 with-gaps, 0 regressed, 0 failed.**

| Slice | Grade | Note |
|---|---|---|
| #3455 owner-routed memory hooks | as-designed (live) | 0 foreign-butler episode rows across all 9 schemas post-merge |
| #3462 timeline maintenance lens | as-designed (live) | 0/20 raw prompt dumps in timeline head (baseline 179/200) |
| #3451 deploy provenance + red worktree clause | as-designed (live) | truthfully reporting the current degraded serving state; 0 `source:"deploy"` rows yet |
| #3474/#3481/#3475/#3486/#3472 health vocabulary pack | as-designed ×5 | endpoint genuinely data-derived; TYPE_META crash path closed |
| #3466 overdue schedule facts; #3467/#3476/#3498 patrol truthfulness | as-designed | suppressed never wears clean green, live-confirmed |
| #3454 ouroboros episode-skip | **with-gaps** (live) | reopened by ungated fact-write provenance auto-episodes (`storage.py:1027`); 4 live post-merge placeholder rows already re-consolidated |
| #3463 audit failure spine | **with-gaps** (live) | sibling `model_breaker_open_notified` still stamps NULL result today |
| #3471 stalled-approvals radar | **with-gaps** (live) | derives from a 30-row decided page; undercounts the 109-row approved-never-executed backlog ~13× |
| #3458 notification metadata | **with-gaps** (live) | object shape fixed, but `session_id`/`trace_id` NULL in 100% of rows ever written |
| #3487 shared verdict primitive | **with-gaps** (live) | still unions 7/10 system tiles (DbSize + EgressCatalog excluded) |
| migration-chain CI gate | **with-gaps** | path filter covers 1 of 12 chains its own test checks |
| #3473 permissions matrix | **with-gaps** (live) | mechanism correct, but core_121 seeded 100% explicit cells — the dim-vs-foreground language is visually dead |

## Tier board (movement vs 2026-07-17 baseline)

| Surface | Verdict | Movement | One-line reason |
|---|---|---|---|
| dashboard | solid | unchanged | act loop hardened; CostWidget fabricates $0.00 on fetch failure; KPI strip doorless |
| butlers roster | functional | **regressed** | board chrome renders all-clear during fetch failure; healthy-pill arithmetic wrong; 5-min crons labeled "hourly" |
| sessions | solid | unchanged | corrections table invisible on dossier; no model/complexity filters |
| chronicles | **weak** | **regressed** | voice paragraph is a raw LLM planning dump; day/content mismatch; 122 years of fabricated quiet days |
| education | **weak** | regressed | 31-day zero-node phantom curriculum narrated as normal progress |
| entities | **weak** | regressed | merge strands viewer on an unmarked tombstone; Merge has less friction than Forget |
| calendar | solid | unchanged | honesty mature; no keyboard period-nav; flat staleness severity; bu-twb2f actually shipped |
| ingestion | functional | regressed | reauth → raw JSON 404; audited pause/run-now has zero UI; `supports_backfill` is a phantom |
| issues + audit | functional | unchanged | audit log: seq-scan privileged view, offset pagination, no keyboard |
| decisions | functional | regressed | closed bead renders "waiting"; badge collapses outage to 0; rows are dead ends |
| settings + secrets | functional | unchanged | reddest console signal routes to a dead `?tab=` param (live) |
| chat widget | functional | new | 2 lanes only; wrong butler in header; Stop doesn't stop; cosmetic streaming |
| cross: shell | solid | unchanged | palette↔shortcut pairing drifted; Issues key and palette act on different rows |
| cross: visual | functional | **regressed** | 2.5 dialects; unguarded blue/purple; 49 Card files; new landings extended the fork |
| cross: speed | solid | unchanged | every speed pattern generalized to a subset (3/39 split routes, 2/105 poll opt-outs) |
| cross: a11y | solid | **improved** | fresh landings landed accessible; shell-level floor + skip link still missing |
| eco: collaboration | **weak** | regressed | delegation ledger 0 rows ever (promoted with root cause + fix precedent) |
| eco: knowledge-graph | **weak** | regressed | provenance collapse 89–99%; dangling citations 40–75%; 0/112 rules ever matured |
| eco: reliability | functional | **improved** | apparatus broader than believed, but escalate-once leaves chronic failures silent |
| eco: connectors | functional | unchanged | blind to what the owner builds/learns/reads; Spotify discards podcasts; HA dark 15d |
| eco: interfaces | functional | unchanged | Telegram: stateless classifier, dead air, voice notes = "[Voice message]" |
| spend, memory, circles, health, qa, system, notifications, timeline, approvals | — | not-audited | fleet mid-flight or QC-covered only |

## Systemic themes

1. **Next-producer erosion is systemic law.** Ouroboros reopened via `storage.py` provenance
   placeholders; #3463's sibling writer still NULL; #3471's 30-row window; #3487's 7/10 union;
   CI gate on 1/12 chains. Call-site fixes must become population-level coverage.
2. **Fabricated calm at the chrome/badge layer.** Board header all-clear over an error card;
   $0.00 on fetch failure; decisions badge 0 on outage; connector `state` column healthy at
   4d15h stale. The degraded-source convention stopped one layer below the chrome.
3. **Dead ends concentrate on the reddest signals.** `auth_renewal` routes to a param nobody
   reads (live); decision rows have no door to their bead; reauth 404s; analytics clicks no-op.
4. **Built once, generalized to a subset.** Triage verbs on 5 pages / palette-paired on 1;
   code-splitting 3/39; page-context 1/40; an inert focus-visible floor. Move patterns into
   primitives/registries/lints so surfaces inherit them.
5. **The trace spine is thinnest at its origin.** KG provenance collapse; notification trace
   ids 0% populated; corrections invisible; chronicle caches undated. Doors are landing on
   data that cannot answer "why do you believe this?"
6. **Edge-triggered attention in a level-condition world.** Escalate-once + investigate-per-tick:
   silent 110-day outages alongside 67 wasted LLM investigations of one dead connector.
7. **The conversational layer lags the dashboard by a full tier.** Telegram has no continuity,
   no receipt, no voice; the widget declines plain questions and fakes both streaming and stop.

## Ranked moves (15)

| # | Move | Kind | Cost |
|---|---|---|---|
| 1 | Chronicles narrative truth repair (cache shape gate, date assert, archive floor, no-data state) | ux | M |
| 2 | Close the consolidation-ouroboros provenance loophole (+ placeholder recall filter) | engineering | M |
| 3 | Run-05 landing completion pack — population coverage for the 7 eroded mechanisms | governance | M |
| 4 | KG provenance spine: per-fact evidence, durable digests, graph-health panel, rule-loop decision, entity edges | ecosystem | L |
| 5 | Activate the delegation loop: wake-the-asker spec + return path + prompt surface + briefing seed | ecosystem | M |
| 6 | Reliability doctrine: infra-condition ledger, escalation-aging ladder, supervised watchers | ecosystem | L |
| 7 | Fabricated-calm chrome sweep (board chrome, cost widgets, decisions badge, entity tombstones, phantom flags) | ux | M |
| 8 | Dead-end door repair pack (auth_renewal route, KPI doors, decision wiring end-to-end, education panel) | ux | M |
| 9 | Conversational layer: Telegram parity (sticky routing, context, ack, voice STT, inline confirms) + widget honesty | ecosystem | M |
| 10 | Keyboard chassis: verbs emit palette commands, lint ban raw keydown, add missing hot loops | ux | M |
| 11 | A11y shell floor: real `:focus-visible`, skip link, `InlineActionLink`, `TableHead` scope | ux | M |
| 12 | Dispatch dialect consolidation: full-palette lint, one StateDot, `Tile` primitive retiring Card | engineering | L |
| 13 | Perceived-performance generalization: lazy-all routes, background-poll default off, prefetch registry | ux | M |
| 14 | Education lifecycle integrity — or the principled cut (owner decision) | engineering | M |
| 15 | Perception expansion: GitHub, Spotify podcast lens, YouTube, reading capture | ecosystem | L |

Full what/why/evidence/slice-plans/collision-notes: `.synthesis.ranked_moves` in the data
file. Every move carries a collision note against in-flight `bu-kqnum` children and open
epics (`bu-ckkpz`, `bu-24lu6`); nothing here duplicates their scoped slices.

## Dropped (nothing silently vanishes)

15 agent-proposed moves were cut or folded at the line — sessions stop-primitive and
model/complexity filters, calendar monolith decomposition, audit-log keyset + privileged
index, semaphore metrics, standing context subscriptions, finance statement ledger, unified
conversation ledger (named as move 9's structural successor), chat token streaming,
page-context spread, and five folds into filed moves. Full list with reasons:
`.synthesis.dropped`.

## Corrections to standing memory

- Epic `bu-twb2f` (butler-event → subcalendar routing) is **shipped and closed**, not gated —
  the standing memory note is stale and should be retired.
- Run-05's "adjacent evidence points down" on reliability undersold the apparatus: eight
  deadman families exist; the gap is lifecycle doctrine, not absence.
