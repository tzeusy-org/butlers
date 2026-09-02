# JARVIS pursuit — run 11 (2026-09-03)

Eleventh recurring generative audit toward a world-class JARVIS-like Butlers system, run under the
standing owner emphasis (2026-09-02): **feature exploration first**. 18 agents over 16 hourly
batches (≤3 concurrent; eco/deep/cross = opus/high, surfaces = sonnet/medium, synthesis inline on
the fable orchestrator): 9 ecosystem lenses (inference, connectors, cross-butler, knowledge graph,
proactivity, owner-absent operation, home/IoT, finance & commerce, agent self-improvement), 2
deep-design agents (entity graph dossier, fleet case file), 1 cross-cutting mobile/on-the-go sweep,
and 6 coarse surface journeys. Zero agent errors.

Full per-agent structured output lives in `2026-09-03-jarvis-pursuit-data.json`. Access pattern:

```bash
jq '.audits[] | select(.page=="eco: home-iot")' docs/redesigns/2026-09-03-jarvis-pursuit-data.json
jq '.synthesis.ranked_moves[] | {rank, title, cost}' docs/redesigns/2026-09-03-jarvis-pursuit-data.json
```

## North star (unchanged from run 09)

> Five-second fleet verification with earned calm: nothing fabricated, failure never impersonates
> health, staleness never wears current-data authority, and every consequential clause is a door on
> an unbroken signal-to-session-to-evidence spine. The interface is keyboard-first, follows
> Dispatch as if built by one hand, respects specialist manifesto ownership, and meets the
> repository engineering and lifecycle bars.

## Tier board and movement

Run 11 grouped routes into six coarse owner journeys plus one new cross-cutting lens (per the
feature-first emphasis), so tiers map onto run-09 constituents rather than 1:1. Runs 09/10 epics
were released after filing, but **no QC pass ran this run**, so no movement is claimed from their
shipping — every claim below rests on this run's own audit evidence against the run-09 board in
`2026-09-01-jarvis-pursuit.md`.

| Surface (run-11 grouping) | Run-09 constituents | Run 11 | Movement |
|---|---|---|---|
| Command & control (dashboard · butlers board · butler console · approvals) | Dashboard functional · Butlers roster solid | **solid** | ↑ Dashboard constituent (seam walk found 3 defects, none fabricating) |
| Activity spine (sessions · timeline · notifications · issues · audit · decisions) | Sessions+timeline solid · Issues+audit solid | **solid** | — (two-chronicle fork and DecisionsPage shell drift noted) |
| Health & education records | Health solid · Education functional | **weak** | ↓ new evidence on the six satellite record pages run 09 never walked (error impersonating calm in Education; zero keyboard surface; two header dialects) |
| Life graph (entities · calendar · chronicles · memory) | All four solid | **weak** (as one journey) | ↓ qualified — page-grain quality holds; the verdict grades the cross-page journey (entity_ids inert in 3+ components, one-way memory bridge) |
| Ops & QA (ingestion · connectors · QA) | Ingestion solid · QA solid | **functional** | ↓ seam evidence (dead-end attention strip, two divergent attention classifiers) |
| Config & governance (settings · models · permissions · spend · secrets) | Settings+secrets functional · Spend solid | **solid** | ↑ Settings constituent; two critical audit-ledger holes filed |
| Cross: mobile / on-the-go | never audited | **weak** | NEW |

Ecosystem lens tiers: knowledge-graph stays **weak** (a second forgery-class hole found this run);
connectors/inference/cross-butler/proactivity carry forward **functional**. Home/IoT, finance &
commerce, self-improvement, and owner-absent operation were audited generatively for the first
time and are not tiered.

## Systemic themes

1. **The writer's clock impersonates the world's clock.** Home stamps `captured_at = now()` on
   every snapshot cycle whether or not HA reported anything, then judges presence freshness on
   that same writer clock (`roster/home/modules/__init__.py:2078-2084`,
   `src/butlers/jobs/context_producers.py:184-199`); finance computes per-account
   `never_synced`/`stale` verdicts the accounts endpoint never selects
   (`roster/finance/api/router.py:453-477`); the frontend serves 30s-stale cache across a dead
   phone link with no client-side stamp (`frontend/src/lib/query-client.ts:6`).
2. **Gates that exist as metadata, not enforcement.** Home loads `[modules.approvals]` but never
   enables it while computing `arg_sensitivities` for the disabled gate (`roster/home/butler.toml:38`,
   `src/butlers/config.py:526`); `memory_catalog_search`'s sensitivity ceiling is caller-asserted
   (`src/butlers/modules/memory/__init__.py:1814-1824`); owner-absent authority is five fail-open
   booleans (`src/butlers/modules/approvals/permissions.py:28-35`); the catalog's documented
   "DELETE withheld" guardrail is contradicted by init-db's blanket public grants
   (`scripts/init-db.sql:376-379`); butlers rewrite their own schedules ungated while an emoji
   reaction is approval-gated (`src/butlers/core_tools/_scheduling.py:60-220` vs
   `roster/messenger/butler.toml:83`).
3. **Aggregates that denominate nothing.** Mixed-currency spend totals labeled with
   `MAX(currency)` (`roster/finance/api/router.py:552`); own-account transfers counted as both
   income and expense, inflating savings_rate (`roster/finance/tools/overview.py:520-570`);
   net-worth totals with no currency field at all (`overview.py:414-443`); energy digests in kWh
   with the money half of the manifesto promise unrepresented (`src/butlers/jobs/home.py:1612-1677`).
4. **Built twice, unified never.** Two suggestion pipelines with divergent status vocabularies
   (switchboard rule promotion vs autonomy suggestions); two Timeline chronicles sharing one
   `?trace=` contract with zero cross-links; two connector attention classifiers disagreeing
   across sibling pages; two header dialects across six sibling Health pages plus a third page
   shell on DecisionsPage. Run 09's sibling theme (built once, generalized nowhere) also recurs:
   the catalog's entity anchor is written and indexed on every fact and read by nothing; cluster
   synthesis is paid for with a metered LLM call and discarded every cycle; sessions success/error
   are stored and never aggregated.
5. **The last hop has no door.** Approval escalation is one hardcoded Telegram push, emitted once,
   never retried (`src/butlers/modules/approvals/notifications.py:140,186`); subscription renewal
   warnings have no cancellation URL, notice period, or cancel-by date anywhere in schema; the
   Timeline connector strip is styled as a link and navigates nowhere
   (`frontend/src/components/ingestion/TimelineTab.tsx:852-884`); entity-dossier facts have no
   route to their canonical memory record; N urgent pings about one situation have no container
   to open.
6. **The pane's primary entry is a phone, and the phone was never designed.** Approval and secrets
   deep links are minted into Telegram, yet the binding design spec contains zero occurrences of
   breakpoint/viewport/touch, the codebase has zero `pointer: coarse`/safe-area/dvh occurrences,
   Playwright runs one desktop-only project, and a dropped LTE link is announced as "Fleet event
   stream offline".

## Ranked moves

Trust/correctness defects lead (1–6); 10 of 15 are ecosystem/feature moves. Full Dispatch
Readiness Packets (outcome, non-goals, surface map, behavior matrix, verification) are in the data
JSON under `.synthesis.ranked_moves`.

1. **Server-derived catalog read authority** (S · knowledge graph). `memory_catalog_search` trusts
   the caller's own `max_sensitivity` claim — a forgery-class hole in the fleet's shared memory
   index. Derive each butler's ceiling server-side from held config; add `memory_catalog_fetch`
   so provenance pointers become walkable. Evidence: `src/butlers/modules/memory/__init__.py:1814-1824`.
2. **The physical consequence gate** (L · home). The only tool that can unlock the front door is
   the roster's one consequential write with no approval gate; the actuation log has no actor,
   session, approval, or status column, and failures are structurally identical to successes.
   Enable approvals with a declared physical-risk map, add the receipt columns + post-condition
   verification, declare `home.actuation_executed` domain events. Evidence:
   `roster/home/butler.toml:38`, `roster/home/migrations/001_home_tables.py:88-99`,
   `roster/home/modules/__init__.py:1881-1935`.
3. **Honest absence: `public.expected_signals`** (M · proactivity). Health's measurement-gap job
   fires on elapsed time with no liveness join — a dead connector becomes a fabricated claim about
   the owner's behaviour. Land the shared absence primitive with `unmeasurable` semantics and join
   every gap-detector through it. Evidence: `roster/health/jobs/health_jobs.py:340-410`,
   `src/butlers/core/liveness.py:35`.
4. **Escalation ladder + owner reachability + safe_hold** (M · owner-absent). A parked approval is
   one Telegram push, emitted once, never retried, with no expiry sweep; lapsed approvals die
   unannounced. Build the reachability state, the multi-channel retry ladder, and a `safe_hold`
   terminal so silence has a defined outcome. Evidence:
   `src/butlers/modules/approvals/notifications.py:140,186`, `park.py:109`, `module.py:437`.
5. **Currency-honest money spine** (M · finance). Spend, net-worth, and cash-flow aggregates blend
   currencies under a fabricated label and count own-account transfers as income and expense.
   Slice 1 is defect-only: per-currency aggregates, transfer exclusion, degraded envelope when >1
   currency; then owner-sourced fx_rates; rider: surface the already-computed per-account feed
   staleness. Evidence: `roster/finance/api/router.py:552`, `roster/finance/tools/overview.py:414-570`.
6. **Surface honesty batch** (S · six small fixes). (a) audit `model.create`/`model.delete` +
   add `spend.%` to the privileged allowlist; (b) board spend cell → `formatCostUsd`;
   (c) ReviewTimeline threads `isError` so a dead source stops reading as "no reviews scheduled";
   (d) TimelineTab strip → canonical `AttentionStrip`; (e) degraded envelope on entity activity so
   a chronicler outage stops rendering as quiet weeks; (f) real blast-radius count in the
   model-delete dialog. Each independently shippable.
7. **Fleet Case File** (L · cross-butler). One situation = one durable object: the broker already
   computes multi-butler clusters and pays for an LLM-synthesized name, then discards both every
   cycle; the urgent bypass is per candidate, so one illness noticed by five butlers breaks quiet
   hours five times. `public.fleet_cases` + evidence + arbitrated posture + typed closure, with
   the lapse sweep the only writer of `lapsed`. Evidence:
   `roster/switchboard/tools/insight/broker.py:840-1046`, `src/butlers/core/attention_ledger.py:174`.
8. **Public entity graph + fleet dossier** (L · knowledge graph). The catalog's entity anchor is
   written and indexed on every fact and read by nothing; the relationship graph is invisible to
   every other butler. `public.entity_graph_edges` written write-behind by owning butlers, a
   zero-LLM recursive-CTE `entity_graph_walk` core tool, and a fleet-level
   `/api/entities/{id}/dossier` with per-source receipts and honest sensitivity-withheld counts.
   Evidence: `alembic/versions/core/core_009_memory_catalog.py:218-223`,
   `src/butlers/modules/memory/search.py:830-908`.
9. **Friction ledger + outcome-carrying self-observation** (M · self-improvement). Guardrail
   terminations, classification timeouts, and recovered tool errors are computed, marked
   non-actionable, and routed nowhere; `sessions_summary` can only answer "how much did I spend".
   Derive typed friction episodes deterministically at session close; add outcome aggregates so a
   butler can see its own failures. Evidence: `src/butlers/core/qa/sources/session_records.py:67-88`,
   `src/butlers/core/sessions.py:714-780`.
10. **Forward obligation ledger with a cancellation door** (M · finance). The manifesto promises
    no un-warned renewal; price changes are detected only after the charge posts and no
    cancellation URL/notice-period/cancel-by exists in schema. Add them, register deadlines with
    thresholds derived from notice periods, and put the door in the insight payload. Evidence:
    `roster/finance/tools/alerts.py:334-356`, `roster/finance/migrations/001_finance_tables.py:110-127`.
11. **Owner-scoped, room-resolved presence with arrival lead time** (M · home). `at_home` is
    asserted fleet-wide by any person entity (housemate, guest phone) on writer-clock freshness;
    `away`/`commuting` are declared signals with zero producers while OwnTracks data sits unread.
    Slice 1 is the honesty fix (owner scoping + HA clock); then `in_space` occupancy and
    OwnTracks-derived commuting/ETA for genuine pre-conditioning. Evidence:
    `src/butlers/jobs/context_producers.py:184-199`, `src/butlers/context_bus.py:31-33`.
12. **`home.observations` perception ledger** (L · home). The house has no memory of itself — one
    upserted row per entity — while the connector already writes every state change durably to
    `connectors.home_assistant_history`, which home has SELECT on and never reads. Slice 1 is
    `ha_source_health` + a freshness guard so an HA outage stops reading as a healthy house.
    Evidence: `roster/home/modules/__init__.py:2069-2086`,
    `alembic/versions/core/core_084_home_assistant_history.py:7-19`.
13. **Viewport & modality contract + client-link honesty + phone entry routes** (L · mobile).
    Amend Dispatch with the three device bands, 44px coarse-pointer floor, and
    no-hover-only-facts rule; move gutters into the Page archetype with dvh + safe-area; land
    `useClientLink()` so a dropped LTE link stops impersonating a fleet outage; register the
    phone-entry routes (approvals, secrets focus) and walk them at 375px in CI. Evidence:
    `openspec/specs/dashboard-design-language/spec.md` (zero viewport/touch content),
    `frontend/src/components/layout/LiveIndicator.tsx:45,66`,
    `src/butlers/modules/approvals/notifications.py:92-97`.
14. **Owner device bridge + watched-source registry** (M · connectors). A DB-backed source-channel
    registry replaces the hour-offset idempotency hack (capped at 12 channels), brings Discord
    into Dunbar, and opens the path to telephony calls/SMS as first-class perceived channels
    (`sms` is a validated NotifyChannel with no adapter). Evidence:
    `roster/relationship/jobs/relationship_jobs.py:803-828`,
    `src/butlers/core/contracts.py:62`, `src/butlers/daemon.py:901`.
15. **One improvement-proposal spine + `propose_amendment`** (L · self-improvement). The fleet
    built the evidence→proposal→owner-decision lifecycle twice (rule promotion, autonomy
    suggestions) with divergent vocabularies and no target kind for prompts, skills, schedules, or
    tiers; the spec-declared AGENTS.md self-note channel has no callers. Unify into
    `public.improvement_proposals`; identity targets adopt only by rendering a PR through the QA
    healing machinery — a runtime never writes a git-tracked identity file. Evidence:
    `src/butlers/modules/approvals/autonomy_suggestions.py:79-261`,
    `src/butlers/core/skills.py:267-287`.

## Dropped (deduped or cut, so nothing silently vanishes)

- Inference lens (all six moves: two-phase dispatch, cache-affinity economics, shift-session
  coalescing, context-yield ledger, resume proof, local tier) — rank pressure; no trust defect;
  candidates for a dedicated inference-economics run.
- Cross-butler joint objectives, delegate_act, co-signed conditions, fan-out join, forward-draw
  register, negotiated handoff — the case file (move 7) is this run's cross-butler bet; these
  would collide with it mid-flight.
- Knowledge-graph belief-revision ledger, contradiction docket, fleet ontology, integrity metrics,
  proposal spine, evidence-aware decay — moves 1+8 land the substrate they all need.
- Proactivity waiting-on-them producer, watch registry, rhythm-deviation event, expiry harvest,
  seasonal mining — move 3 lands the absence primitive they build on.
- Owner-absent autonomy envelopes, compensating-action registry, homecoming review,
  committed-cost authority, travel-drafted absence plans — move 4 is the prerequisite spine.
- Home spaces, butler-owned routines, tariff-aware energy, condition-driven maintenance —
  sequenced behind moves 2/12 (gate and ledger before composition).
- Finance statement ingestion, income streams / safe-to-spend, fleet cost-claim contract, spend
  envelopes, return/warranty windows — moves 5+10 first; `relationship.loan_outstanding` is the
  natural first cost-claim slice next run.
- Self-improvement retrospective skill, adoption verification/revert door, report_friction relay —
  follow moves 9/15.
- Connectors clinical FHIR, authenticated web-action module, leave-by feed, shipment lifecycle —
  L-cost or manifesto-amendment-gated; deferred.
- Mobile PWA/offline shell, coarse-pointer modality lane (Revealable/gestures), viewport ESLint
  guard, phone Playwright full gate — follow move 13's contract.
- Surface UX repairs held for a batch epic: PersonChip primitive for inert entity_ids,
  entity→fact provenance door, chronicles↔calendar cross-links, episode vocabulary split, Health
  j/k + palette verbs + navigable condition graph + one header dialect, EducationPage Dispatch
  migration, board restore keyboard verb, ButlerApprovalsTab inline decide, /timeline vs
  /ingestion collapse, DecisionsPage onto the Page shell, connectors-roster j/k, QA↔connector
  evidence door, shared oauth-error hook, SpendPage local change history — all fully specified in
  the data JSON.

## Filing

Beads epic (gated, fleet NOT triggered): see the epic and `[HOLD]` gate ids in the final session
report and the run-11 `bd remember` reference memory. Closing the gate bead releases the children
to the autonomous fleet — that release is the owner's move.
