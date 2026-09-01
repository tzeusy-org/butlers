# 2026-09-01 JARVIS Pursuit — Run 09

Ninth recurring pursuit audit toward a world-class JARVIS-like Butlers system. 30 agents over 12
staggered hourly batches (4 QC verification passes over the run-08 release, 16 page-surface
audits on sonnet/medium, 4 cross-cutting sweeps and 6 ecosystem lenses on opus/high), synthesized
inline on the fable orchestrator. Zero agent errors. Prior run: `2026-08-09-jarvis-pursuit.md`
(epic `bu-6jv4m`, released 2026-08-10; leftovers still open: `.1 .5 .6 .12 .16`).

**Full structured output**: `docs/redesigns/2026-09-01-jarvis-pursuit-data.json`. Access pattern:

```bash
jq '.audits[] | select(.page=="page: spend")' docs/redesigns/2026-09-01-jarvis-pursuit-data.json
jq -r '.audits[] | "\(.page)\t\(.verdict)"'   docs/redesigns/2026-09-01-jarvis-pursuit-data.json
```

Labels: `qc: <slice>` (4), `page: <surface>` (16), `cross: <sweep>` (4), `eco: <lens>` (6).

## North star (unchanged from run 08)

> Five-second fleet verification with earned calm: nothing fabricated, failure never impersonates
> health, staleness never wears current-data authority, and every consequential clause is a door
> on an unbroken signal-to-session-to-evidence spine. The interface is keyboard-first, follows
> Dispatch as if built by one hand, respects specialist manifesto ownership, and meets the
> repository engineering and lifecycle bars.

## QC verdicts on the run-08 release

Four sonnet QC passes verified the 11 closed run-08 moves against their specs:

| Slice | Verdict |
|---|---|
| bu-6jv4m.3 Issues ledger, .4 heartbeat, .8 domain-event contracts, .10 education, .11 connectors, .13 shell, .14 owner-time, .15 token guards | **as-designed** — real, wired, CI-enforced where claimed |
| bu-6jv4m.2 routing-rule delete Undo | **landed-with-gaps** — client-side recreate-at-position with an honest disclaimer; neither spec-sanctioned outcome |
| bu-6jv4m.9 evidence-bearing graph | **landed-with-gaps** — bitemporal half deferred to open beads `bu-h3b7t` `bu-4ss0u` `bu-1ypjo` `bu-uedyz` (not re-filed) |
| bu-6jv4m.7 model-routing hard-fit | **landed-with-gaps** — filtering is real, but the AC4-5 resolution receipt is computed and **discarded** (`model_routing.py:1729-1734`: "The resolution receipt is dropped here"); no table, route, or frontend surface exists. The explainability half — *why did this model win* — did not ship. Re-filed as move 9. |

## Tier board and movement

Movement is claimed only where this run's evidence supports it; ↑ marked **QC** is anchored to a
QC-confirmed run-08 move, ↑ marked *audit* is this run's page-audit observation on a surface QC
did not directly cover. Downgrades rest on new defect evidence found this run.

| Surface | Run 08 | Run 09 | Movement |
|---|---|---|---|
| Dashboard | functional | functional | — |
| Butlers roster | solid | solid | — |
| Sessions + timeline | solid | solid | — |
| Chronicles | functional | **solid** | ↑ *audit* |
| Education | weak | **functional** | ↑ **QC** (.10 as-designed) |
| Entities | solid | solid | — |
| Calendar | solid | solid | — |
| Ingestion | functional | **solid** | ↑ **QC** (.11 as-designed) |
| Issues + audit | functional | **solid** | ↑ **QC** (.3 as-designed) |
| Attention | solid | solid | — |
| Memory | solid | solid | — |
| Health | solid | solid | — |
| Spend | functional | **solid** | ↑ **QC** (.2/.4 landed) |
| Settings + secrets | solid | **functional** | ↓ (SettingsPermissionsPage regression evidence) |
| QA | solid | solid | — |
| System + chat | functional | **solid** | ↑ *audit* |
| Cross: shell/discoverability | solid | solid | — |
| Cross: visual language | solid | **functional** | ↓ (deep sweep: 2 competing type scales, 166-site Badge divergence, enforcement holes) |
| Cross: interaction speed | solid | solid | — |
| Cross: accessibility | solid | solid | — |

Ecosystem lens tiers carry forward unchanged from run 08 (lens audits are generative, not
tiering): connectors/inference/cross-butler/proactivity/conversation **functional**,
knowledge-graph **weak**.

## Systemic themes

Six cross-cutting defect shapes recur across otherwise-unrelated audits:

1. **Computed-then-discarded truth.** The system does the work to know *why* or *which* and
   throws it away at the last hop: the model-resolution receipt is computed and dropped
   (`model_routing.py:1729`), timeline Now-rows discard `event.id` (`model.ts:1002`), butler
   replies carry no `request_id` back to the inbound message (`deliver.py:197-226`), failover
   token spend never reaches the ledger (`spawner.py:2531-2542`), provider resume-vs-cold is
   recorded and surfaced nowhere.
2. **Acceptance impersonating completion.** The Telegram 👍 fires on routing *acceptance*, not on
   a reply (`_switchboard.py:333-345`); the green AUTHORIZED pill renders on offline/stale
   connector rows (`connector-auth.ts:98-172`); QA detail routes fold *unreachable* into *not
   found*; insights that expire unseen leave no attention-ledger row (`broker.py:289-307`); the
   monthly ceiling reads a ledger that misses failover burn.
3. **Doors missing on the last hop.** Standing Conditions rows are inert text; the vitals KPI
   strip is the one domain strip with no hrefs; breaker-reset evidence IDs are unclickable inside
   the very dialog built so "the operator must not reset blind"; 36 of 38 empty states render no
   action; an insight is prose with no action verb or evidence anchor.
4. **Primitives built once, generalized nowhere.** `useListTriage` on 8 surfaces but not memory's
   registers; `Tip` adopted by 8 files against 54 raw `title=`; the `Page` primitive (title +
   route announcement) on half the routes; five KPI-strip forks; the live event bus on 8 of ~20
   domains; the one page still on hand-rolled `fetch()` (`SettingsPermissionsPage`). Every
   mechanism exists somewhere and is enforced nowhere.
5. **Consequential writes ungated while reversible ones are.** The monthly-ceiling input can
   silently halt fleet-wide dispatch with no confirm (`SpendPage.tsx:568-635`) and webhook
   delete / signing-secret regeneration are single-click irreversible — on the same page where a
   cleanly reversible permission flip sits behind a modal.
6. **Second unmetered channels around a governed spine.** `notify(intent='send')` bypasses the
   insight broker's budget/dedup/cooldown entirely (education, relationship); per-message
   Telegram anchors silently void the shipped conversation-resume ledger; lint exemptions have
   minted an unsanctioned "informational blue" role; bare `fetch()` bypasses the timeout-bounded
   client. Governance is real but the ungoverned path is still open beside it.

## Ranked moves (run 09)

Fifteen moves, UX and ecosystem mixed, ranked by owner value against the north star. Each child
bead cites the full evidence; details live in the data JSON under the named audit.

1. **Per-attempt spend truth** (eco: inference, M) — ledger every dispatch attempt with
   `usage_source ∈ {measured, unmeasurable}`; today up to 10 failover attempts burn real provider
   tokens while only the winner is ledgered and `check_monthly_ceiling()` reports calm
   (`spawner.py:2038, 2531-2542, 3244-3259`).
2. **Conversation identity fix on the primary channel** (eco: conversation, M) — split
   conversation identity from reply target in ingest.v1 and rekey the anchor; today every
   Telegram message mints a new anchor (`telegram_bot.py:1352-1354`), so the shipped
   provider-resume ledger (core_185) is a silent no-op and every turn cold-starts.
3. **Ceiling edit consequence gate** (page: spend, S) — confirm-with-consequence when the new
   ceiling is at/below MTD (a fat-fingered save silently DoSes fleet-wide dispatch), invalidate
   the fleet-halt query on success, add aria-label + Enter/Escape (`SpendPage.tsx:568-635`).
4. **Settings/permissions process-fidelity repair** (page: settings-secrets, S) — port the six
   hand-rolled `fetch('/api/…')` calls onto `apiFetch` (page is dead on path-mounted deployments,
   no timeout), and gate webhook delete + secret regeneration behind confirms
   (`SettingsPermissionsPage.tsx:131-214, 1043-1106`).
5. **Status words stop impersonating calm on ingestion** (page: ingestion, S) — the green
   AUTHORIZED pill renders on offline/stale/degraded rows (`connector-auth.ts:98-172`); make
   liveness gate the status word.
6. **One attention budget** (eco: proactivity, L staged) — route unsolicited owner-directed
   `notify()` through the insight broker; slice 1 is pure measurement (solicited/unsolicited
   provenance on the attention ledger), then migrate the education/relationship push paths.
7. **One merge authority with a rebind ledger** (eco: knowledge-graph, L) — collapse the two
   divergent entity-merge paths, rebind `public.memory_catalog`, and stop `except Exception:
   continue` from converting repoint failures into success counts
   (`entities.py:1199-1262`, `entity_merge.py:162-294`).
8. **Shell scroll memory** (cross: interaction-speed, M) — one route-boundary scroll authority
   for the persistent `<main>`; today every list→detail→back drops the operator at the top
   (`Shell.tsx:89`, zero `ScrollRestoration` repo-wide).
9. **Ship the dropped model-resolution receipt** (QC follow-up on bu-6jv4m.7, M) — persist and
   surface *why this model won* (the receipt is already computed at
   `model_routing.py:1729-1734` and discarded); completes the explainability half of the run-08
   move.
10. **First-frame identity** (cross: visual-language, S) — pre-hydration dark stamp, vendored
    woff2 fonts (offline instance renders banned generic stacks today), real `<title>` and
    favicon replacing the Vite starter's (`index.html:5-10`, `useDarkMode.ts:33-37`).
11. **One measured focus token** (cross: accessibility, M) — mint `--focus` at ≥3:1 both themes
    and make the floor unbeatable; the five core primitives currently override the 18:1 outline
    with a 1.52:1 ring (`index.css:619` vs `button.tsx:8` et al.).
12. **Advertise the chords where destinations live** (cross: shell-discoverability, S) — render
    chord chips in sidebar tooltips and palette Pages rows, assign chords to the uncovered
    globals (QA is escalation-badged with no chord), add per-group palette counts + overflow
    rows.
13. **Voice egress: answer in the room that was heard** (eco: connectors, M) — `notify()` gains a
    Messenger-owned `voice` channel; live_listener hears rooms the system can only answer on the
    owner's phone (`core-notify/spec.md:302`). The single most JARVIS-shaped gap found this run.
14. **Turn-closure ledger + continuity receipt** (eco: conversation, L) — one durable terminal
    disposition per inbound owner message with `in_reply_to_request_id` on outbound rows; move
    the Telegram 👍 onto it; surface resumed-vs-cold as an owner-visible chip.
15. **Insight feedback verbs + expired-unseen accounting** (eco: proactivity, M) —
    useful/not-now/never verbs with per-category budget shaping (the current ratchet is global
    and one-way), and an `expired` outcome in the attention-ledger vocabulary so unseen expiry
    stops being invisible.

## Dropped (worthy, not top-15 this run)

Retained in the data JSON; candidates for run 10 or opportunistic pickup: eyebrow-as-heading spec
amendment (a11y); `useListTriage` on memory registers + rules-ordinal offset fix; Badge→KindTag
collapse (L); single rem type-role scale + `text-[Npx]` ban (L); payload-carrying fleet events +
live-spine extension to all domains (L); Fleet Case File multi-domain situation object (L);
stakeholder consultation on approvals; context-bus exclusivity arbitration; delegated-answer
write-back; `calendar_event_upcoming` lead-time trigger; conflict-fix proposal producer;
free-moment delivery; Immich / media-scrobble / mobile-notification-mirror / read-later
connectors; Open Food Facts nutrition provenance; entity-anchored fleet dossier; canonical-graph
traversal tool; catch-all route + clear-recents; Education tab URL-addressability; condition
care-context view; Tip migration completion; recoverable mutations (retry-with-input); tier-fit
observations; interaction-class spawn admission; typed classification brief; request-scoped
inference budget / delegation depth guard; declared fleet participation manifest; standing
conditions evidence doors; `r`-refresh honesty on /system; topology keyboard activation.

## Execution

Beads epic: see `bd` — `[HOLD]` gate + epic assigned to the owner; every child blocked on the
gate. **Closing the gate bead is the owner's release move: the moment it closes, the children
enter `bd ready` and the autonomous fleet begins executing them.**
