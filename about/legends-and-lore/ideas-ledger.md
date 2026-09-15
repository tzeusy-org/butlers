# Ideas Ledger

This is the durable home for ideas that were raised, judged real, and then
explicitly **parked** rather than pursued immediately. It exists so that
deferred work has a place to live with a stated reason and a concrete
**unpark condition** — the signal or milestone that should cause someone
(owner or a future th-projects milestone-synthesis pass) to reconsider it —
instead of evaporating into closed dossiers that nobody re-reads.

## Provenance

Every entry below is sourced from the **JARVIS relentless pursuit** dossiers
under `docs/redesigns/` (skill: `.claude/skills/butlers-development/subskills/butler-relentless-jarvis-pursuit/`),
a recurring generative audit of the Butlers ecosystem against
`about/heart-and-soul/vision.md`:

- **2026-07-03** (`2026-07-03-jarvis-audit.md`) — the first run. It predates
  the dropped-ledger convention: it has no explicit "Dropped" section because
  its 14 ranked moves were nearly all pursued via epic `bu-86c4c`
  (17/19 children landed). Nothing from this run needed parking.
- **2026-07-04** (`2026-07-04-jarvis-pursuit.md`) — second run, first to carry
  an explicit "Dropped (dedup ledger)" section (27 items).
- **2026-07-10** (`2026-07-10-jarvis-pursuit.md`) — third run, broadened to a
  three-bar audit (design/engineering/projects). Its own "Dropped" section
  (26 items) both adds new parked ideas and re-confirms or re-disposes several
  from 07-04.

Where the same idea recurs across runs, this ledger merges it into one entry
and notes the lineage — a recurring idea is a stronger signal than a
one-off, and its unpark condition usually tightens each time it resurfaces.
Ideas that a later run's dossier confirms were absorbed into a *landed* move
are marked **resolved** rather than carried forward.

## How to read an entry

Each entry has:
- **Idea** — what was proposed.
- **Why parked** — the stated reason it wasn't pursued this cycle.
- **Unpark condition** — the concrete event that should trigger revisiting it.
- **Source** — which dossier/date raised it (and re-raised it, if recurring).

---

## Ecosystem: cross-butler collaboration

### Cross-butler delegation ask/answer ledger
**Idea:** Let one butler ask another a structured question and get a
tracked answer back, instead of only the one-way hub-and-spoke fan-out that
exists today.
**Why parked:** 07-04 deferred it behind the `memory_catalog` flip (so
butlers could at least *read* each other's facts before they could *ask*
each other questions). By 07-10 the catalog flip had shipped, but the
delegation ledger itself (`bu-gxmfx`) turned out to be zero-row machinery
with no producers or readers — built, but nothing calls it.
**Unpark condition:** the Owner Decision Desk (07-10 move 8) resolves
adopt-or-descope for `bu-gxmfx`. If adopted, treat "first producer wired"
(some real cross-butler question flow) as the actual unpark signal, not
the ledger's mere existence.
**Source:** 07-04 Dropped #1; 07-10 Dropped #24 (Decision Desk seed queue), QC verdict on `bu-gxmfx`.

### Domain-event subscriptions + cross-domain case files
**Idea:** Let butlers subscribe to each other's domain events and build
shared "case files" spanning multiple butlers' data (e.g. a finance event
that a chronicler episode should know about).
**Why parked:** premature — both the knowledge catalog and the delegation
ledger above need to land and prove out first; this is L-cost with a
dependency chain on both.
**Unpark condition:** delegation ask/answer (above) has a live producer/consumer
pair in production; only then does a subscription layer have anything to
subscribe to.
**Source:** 07-04 Dropped #2.

---

## Ecosystem: connectors and perception

### New connectors (SimpleFIN, flight status, ActivityWatch, weather, parcels)
**Idea:** Expand perception with five sound, manifesto-grounded connector
integrations.
**Why parked:** correctly-scoped ecosystem expansion, but deliberately kept
out of the top-15 ranked slots against trust-defect fixes each cycle.
**Unpark condition:** owner opens a dedicated connector epic (per
`adding-connectors-and-modules`), or explicitly requests one of the five by
name. Note the 07-10 dossier's `eco:reliability` finding that existing
connectors already go dark 7+ weeks unnoticed on pull-only health — fix that
monitoring gap before *adding* more unmonitored sources.
**Source:** 07-04 Dropped #5.

---

## Ecosystem: inference economics

### Deterministic precondition gates on scheduled prompt tasks
**Idea:** Add cheap deterministic checks before a scheduled prompt task pays
for an LLM spawn, to cut recurring spend waste on tasks that had nothing to
do.
**Why parked:** real and recurring waste, but efficiency-only — below the
trust-weighted cutoff both cycles have applied (honesty/safety defects rank
first).
**Unpark condition:** once the top-ranked honesty/reliability moves in a
cycle are exhausted (i.e., a cycle's dropped list has no P0/P1 trust defects
left), this becomes a natural next pick — or file it standalone if spend
pressure forces the question sooner.
**Source:** 07-04 Dropped #3.

### Latency spine: per-phase spawn timings
**Idea:** Instrument the phases of a cold CLI spawn (bootstrap, MCP connect,
model call, teardown) so the API-direct inference lane's win is measurable.
**Why parked:** narrowly cut for slots; it's an S-cost enabler, not a
user-facing win on its own.
**Unpark condition:** unpark alongside (or just before) work on the
API-direct inference lane, so the "halved latency" claim has a baseline to
measure against.
**Source:** 07-04 Dropped #4 ("file as a standalone bead alongside move 12").

---

## Ecosystem: proactivity

### Speculative reply drafting, wake-anchored delivery, per-category insight feedback
**Idea:** Three refinements to how insights reach the owner: pre-drafting
likely replies, timing delivery to when the owner is actually awake, and
letting the owner give per-category feedback that tunes future insight
volume.
**Why parked:** all three assume the attention ledger and decision-loop
primitives exist first.
**Unpark condition:** RFC 0021's decision loop ships (tracked via
`bu-24lu6`) and the attention ledger (07-10 move 1) is live with a real
reader — then these become the natural next slice of proactivity polish.
**Source:** 07-04 Dropped #6.

### Telegram reaction/reply capture, per-origin scoring modulation, weekly relevance digest, LLM insight-quality audit
**Idea:** Close the feedback loop on proactive messages — capture how the
owner actually reacts (thumbs/reply/ignore), weight future scoring by
message origin, send a weekly "was this useful" digest, and periodically
audit insight quality with an LLM pass.
**Why parked:** deferred behind the attention ledger's own reader existing
and the engagement signal being owner-gated (see Data Quality entry below —
today the engagement proxy reads connector noise, so any feedback loop built
on it would be measuring the wrong thing).
**Unpark condition:** 07-10 move 1's "gate the 60-minute engagement proxy on
owner-authored ingress" slice ships — building a feedback loop on a poisoned
signal would just calcify the poisoning.
**Source:** 07-10 Dropped #18 (`eco:proactivity` remainder).

---

## Ecosystem: knowledge graph and data quality

### Durable provenance ledger, confidence calibration, cross-butler contradiction sweep, right-to-forget cascade, staleness SLAs, entity dedup at create-time
**Idea:** A cluster of substrate-hardening moves for the shared memory/fact
layer: provenance that survives longer than 7 days, confidence values that
aren't hardcoded to 1.0, a sweep that detects when two butlers believe
contradictory things, a cascade that actually removes forgotten facts from
every downstream index, staleness SLAs, and preventing duplicate entities at
creation instead of merging them after the fact.
**Why parked:** correctly sequenced as the substrate roadmap *after* move 9
(07-10) lands — atomic catalog disownment (forget/expire/purge actually
propagating) has to exist before contradiction sweeps or staleness SLAs mean
anything.
**Unpark condition:** 07-10 move 9's cascade wiring ("make catalog
disownment atomic") ships and is verified live (a forgotten fact
demonstrably disappears from `public.memory_catalog`) — that closes the
prerequisite gap this cluster is deferred behind.
**Source:** 07-10 Dropped #19 (`eco:data-quality` remainder).

---

## Page-local UX: recurring across cycles

These ideas have now been deferred across **two or three** consecutive
pursuit runs. Recurrence itself is a signal — each is a real, evidenced gap
that keeps losing to trust-defect fixes on ranking, not a rejected idea.

### Calendar: truth pass, masthead freshness, timezone unification, geometry/keyboard work
**Idea:** A calendar-wide batch: fix error-as-benign rendering (fetch
failures currently show "Tomorrow is clear"), add a freshness plaque, unify
the two timezones visible on one surface, fix overlap geometry, add a
now-line, add `?entry=` deep links, and decompose the 6,472-line workspace
file.
**Why parked:** calendar already grades "solid" on the tier board, so it
keeps losing ranking slots to weaker surfaces; deferred whole both cycles.
**Unpark condition:** routed under the calendar roadmap epic `bu-l3k0zg` —
unpark when that epic is opened for its next slice, or when the
error-as-benign defect specifically is promoted (it is the one item in this
batch that is a trust defect, not polish, and the strongest candidate to
split out early).
**Source:** 07-04 Dropped #13; 07-10 Dropped #3 ("deferred whole").

### Education / Chronicles: Dispatch conversion, truth-and-lifecycle pass, teaching-flow rail
**Idea:** Convert Education off its pre-Dispatch Card-based visual language,
give it a real event-bus/keyboard/verdict-opener treatment, surface the
five-phase teaching-flow state machine that's currently invisible, and give
Chronicles day-scoped evidence coverage strips and keyboard SVG timeline
marks.
**Why parked:** each piece individually folds into a fleet-wide primitive
move (Dispatch base-layer flip, event bus, palette, verdict opener) rather
than justifying page-local work; Chronicles' evidence-coverage half is also
explicitly coordinated with the in-flight Chronicler IEA epic.
**Unpark condition:** Education is now confirmed (07-10 tier board) as
**the last pre-Dispatch page in the product** — unpark as soon as the next
cycle's ranking has room for one page-local conversion bead, or when the
Chronicler IEA epic (`bu-jc6htw` / `bu-8whey5`) lands and Chronicles'
day-ribbon can inherit fixes instead of duplicating them.
**Source:** 07-04 Dropped #11 (education), #12 (chronicles); 07-10 Dropped #26.

### Health: one-scope measurements page, insight-door vocabulary fix, adherence denominator clamp, dialect unification
**Idea:** Consolidate Health's six sibling sub-pages behind one scope,
fix the category vocabulary mismatch that sends every insight door to
`/measurements` regardless of category, stop adherence fabricating red for
new/PRN medications, and finish the dialect unification (largely covered by
the base-layer flip elsewhere).
**Why parked:** page-local M-cost work that keeps losing to fleet-wide
trust defects on ranking; the insight-door bug specifically was "cut for
slots, queue first next cycle" in 07-10, i.e. it is next in line.
**Unpark condition:** next cycle's ranking should promote the insight-door
vocabulary fix and adherence clamp first (owner's own stated priority per
the 07-10 dossier) — file a standalone S bead for those two rather than
waiting for the whole cluster.
**Source:** 07-04 Dropped #10; 07-10 Dropped #2.

### Settings / Secrets: credential-health feed, PageSystem six-state band, `last_used` persistence, permissions rebuild, passport keyboard triage
**Idea:** Give Settings a real credential-health feed and DB size-history
endpoint, replace the binary green/red PageSystem band with a real six-state
model, persist `last_used` on secrets instead of leaving it a dead axis,
rebuild the permissions page's inherited-cell controls (currently dead), and
add keyboard triage to the secrets passport.
**Why parked:** page-local, below cutoff both cycles; the permissions
self-contradiction specifically routes to the doctrine-spec-code
reconciliation epic rather than being fixed ad hoc.
**Unpark condition:** `bu-9q1dx` (permissions spec reconciliation) resolves
the inherited-cell contradiction — the rest of the cluster can then land
alongside it as one settings/secrets sweep.
**Source:** 07-04 Dropped #18; 07-10 Dropped #10.

### Entities: contact-era vestige excision, archive-with-undo, one entity-type vocabulary, one fact spine, Telegram provisioning relocation
**Idea:** Remove the dead contact-era section that permanently shows "No
linked contact" and blocks owner setup completion, add an undo path to
entity archiving, unify entity-type vocabulary, unify the two competing
fact stores (see `[[butlers-relationship-two-fact-stores]]`), and move
Telegram provisioning into `/secrets` where credentials already live.
**Why parked:** deferred as a page-local cluster; entity-detail stays at
"functional" rather than climbing a tier without it.
**Unpark condition:** file as an entity-detail bead cluster with the dead
contact-era section and duplicates-endpoint fix (the page's worst
fabrication per 07-10) landing first — those two are S-cost and
trust-critical, unlike the rest of the cluster.
**Source:** 07-04 Dropped #25 (duplicates endpoint, two-h1 headline,
Telegram wizard); 07-10 Dropped #9 (entities cluster moves).

### Spend: rule-effects honesty (per-rule application record, `saved_7d` fix-or-retire, evidence-based rule verbs, truthful per-call-cap copy)
**Idea:** Make spend-rule effects auditable: record what a rule actually
did, fix or retire the `saved_7d` figure (currently credits fleet-wide model
usage to individual rules), add "Cap this" verbs driven by real evidence,
and correct copy that overstates what a per-call cap enforces.
**Why parked:** deferred below cutoff; the `saved_7d` mislabel is tracked as
part of the broader "one fact, many numbers" systemic theme rather than
fixed in isolation.
**Unpark condition:** unpark alongside 07-10 move 11 (ledger-first MTD
unification) — both are instances of the same "displayed number ≠ enforced
number" defect class and share a fix pattern.
**Source:** 07-10 Dropped #21.

### Sessions: live tool-call tail, cache-true token/cost columns
**Idea:** Stream the tool-call tail live for a running session, and make
the cost column and dossier's Token Usage include prompt-cache tokens
(currently silently excluded, causing sessions and `/spend` to disagree to
the cent).
**Why parked:** cost-cut; the cache-token mismatch is tracked under the
"one fact, many numbers" systemic theme rather than as a standalone fix.
**Unpark condition:** unpark the cache-token column fix alongside 07-10
move 11 (same defect family as Spend's rule-effects honesty above); the
live tool-call tail remains a genuine L-cost UX nice-to-have with no
forcing function yet.
**Source:** 07-10 Dropped #4.

### Timeline / Notifications: saved-view scoping migration, session/trace IDs into `timeline_v1`, errors-lens for failed deliveries, jump-to-time, chronicle list triage
**Idea:** Fix saved views cross-contaminating between Timeline and
Ingestion, thread session/trace IDs through the unified timeline table so
`?id=` addressing works, add an errors lens that actually shows failed
deliveries, add jump-to-time navigation, and add keyboard list triage to the
chronicle view.
**Why parked:** deferred; the degraded-honesty core of these two pages
(unread `source_available`/`isDown` flags) is the part that actually landed
via 07-10 move 2/3, leaving this batch as the residual polish.
**Unpark condition:** unpark once move 2/3's flag-consumption work (this
epic bu-os64u's sibling work, if tracked, or a future cycle) is verified
live — this batch is the next layer on the same two pages.
**Source:** 07-10 Dropped #22.

### Issues / Audit-log: windowed occurrences, group-predicate door, principal/subsystem schema split, heartbeat-store reachability, ledger substring search
**Idea:** Add a windowed, partially-indexed occurrences query so "Seen 47x"
can open its 47 rows without an unbounded scan; add a `group=` predicate to
the audit API; split the audit schema by principal/subsystem; make the
heartbeat store reachable from the UI; add substring search to the ledger.
**Why parked:** deferred; the degraded-honesty core (issues returning `[]`
on any exception and rendering all-clear) is the part promoted to 07-10
move 3, leaving this as residual read-path work.
**Unpark condition:** unpark once move 3 (honest fan-out primitive) ships —
the occurrences/group-predicate work builds directly on top of it.
**Source:** 07-10 Dropped #20.

### QA: rail truncation count, j/k rail navigation, claim-evidence focusability
**Idea:** Show how many rows a truncated rail is hiding, add j/k keyboard
navigation to the QA rail, and make claim-evidence rows focusable.
**Why parked:** truncation-count and status-mapping fixes already landed
via 07-04 move 2 (confirmed-lies hotfix batch); the keyboard half awaits
the fleet-wide shortcut-registry adoption sweep.
**Unpark condition:** unpark the keyboard half alongside 07-10 move 8's
keyboard-triage adoption batch (below) rather than as a standalone QA
fix — same primitive, same rollout mechanism.
**Source:** 07-04 Dropped #19.

### Approvals: decision record (`decided_by`/`decided_at`/deny reason/execution result) + real expiry sweep
**Idea:** Make a decided approval's dossier show who decided it, when, why
(if denied), and what the execution actually returned — today the decided
dossier omits the whole decision record. Add a scheduled sweep that expires
approvals per their stated expiry instead of leaving it advisory.
**Why parked:** cut at the 15-move ranking cap in 07-10 — explicitly called
out as "the strongest next-cycle UX candidate."
**Unpark condition:** promote to the top of the next cycle's ranked moves;
no further gating condition — this is queued, not blocked.
**Source:** 07-10 Dropped #1.

### Entities-Plex: server-side attention verdict, optimistic retier undo, find fall-through, canvas keyboard parity
**Idea:** Move the plex's cluster verdict computation server-side (it is
currently a frontend heuristic), make retiering an entity's tier an
optimistic+undoable action, fix search's find-fall-through behavior, and
give the canvas the same keyboard parity as list views.
**Why parked:** the verdict-computation half folds into 07-04 move 9's
deterministic verdict primitive (landed); the rest are page-local S/M beads
with no forcing function.
**Unpark condition:** file the remaining three as a standalone entities-plex
polish bead once a cycle has ranking room; no cross-dependency blocks them.
**Source:** 07-04 Dropped #14.

### Destructive-without-recovery trio: webhook delete, spend-rule delete, timeline saved-view delete
**Idea:** Apply the models-page `DeleteConfirmDialog`/undo-toast pattern
(already built once) to three destructive actions that currently have no
recovery path.
**Why parked:** small, mechanical, but never made it into a ranked slot
either cycle.
**Unpark condition:** file as one S bead independent of any epic — this is
pure backlog, not blocked on anything. Good candidate for a slow week.
**Source:** 07-04 Dropped #17.

---

## Cross-cutting infra adoption batches

The pattern across both cycles: a primitive (accessibility choreography,
keyboard registry, poll-policy lint, Dispatch atoms) ships once, proves
correct, and then adoption freezes at 1-4 consumers while dozens of sites
keep the old pattern. 07-10's own systemic-theme analysis names this
explicitly ("primitives built once, adoption frozen at the beachhead") and
recommends a structural fix (born-with burn-down beads, ratchet tests)
rather than another manual sweep.

### Accessibility adoption batch
**Idea:** Wire `useModalChoreography` into `FloatingChatWidget` and the
shared dialog-suspension guard everywhere; add narrated j/k triage; lint-fence
`tr role="button"` tables; add an axe burn-down ratchet so the skip-manifest
can only shrink; extend the contrast token-family test.
**Why parked:** cut for ranking slots in 07-10; the underlying primitives
(choreography, announcer, axe registry, contrast test) are real — this is
purely an adoption gap.
**Unpark condition:** add the burn-down ratchet test first (small, forces
the rest over time by making the skip-manifest's current 30 entries a
one-way-shrinking budget instead of a static allowlist).
**Source:** 07-10 Dropped #8.

### Keyboard-triage adoption batch
**Idea:** Adopt `useListTriage` on the board, timeline, audit ledger,
ingestion ledger, memory registers, secrets passport, and sessions
focus-sync — the densest triage lists in the product, none of which have it
yet.
**Why parked:** deferred; carried under the same "primitives built once"
systemic theme.
**Unpark condition:** same lever as accessibility above — a coverage test
that fails CI when a new dense list ships without triage wired, rather than
another sweep bead.
**Source:** 07-10 Dropped #23.

### Interaction-speed remainder: poll-policy vocabulary completion, mutation-classification sweep, prefetch-registry growth, infinite-query generalization
**Idea:** Extend the poll-policy lint from 9 files to all of `src/**`,
classify the remaining ~86 of ~90 mutation sites as optimistic vs
honest-pending, grow the prefetch registry from 3 routes using the existing
route-registry, and generalize the head/committed infinite-query pattern.
**Why parked:** bus-aware polling intervals were kept as 07-10 move 13; the
rest deferred as the long tail of the same adoption problem.
**Unpark condition:** same lever — a lint/CI gate that treats un-migrated
sites as a shrinking budget rather than tracking via bead sweeps.
**Source:** 07-10 Dropped #25.

### Dispatch polish batch: switchboard hue slot, H1 flip completion, BoardHeader stale comment, Kbd/KbMono merge, mover-dot neutrality
**Idea:** A grab-bag of small visual-language finishing touches left over
after the base-layer flip (07-10 move 7) landed.
**Why parked:** deferred as polish; none individually trust-critical.
**Unpark condition:** batch these into one small PR whenever a contributor
is already touching the Dispatch primitives file for another reason —
genuinely low-priority, no dedicated unpark trigger needed.
**Source:** 07-10 Dropped #7.

---

## Engineering

### Hot-path decompositions: `spawner._run`, `calendar.py`, `api/types.ts`+`client.ts`, `CalendarWorkspacePage`, `qa.py` router
**Idea:** Break up the repo's largest, least-decomposed files
(`spawner._run` at 1,408 lines, `pipeline.process` at 1,395, `calendar.py`
at 10,202, the frontend's two biggest churn-conflict magnets, and the QA
router) into smaller units.
**Why parked:** L-cost, deferred; noted as a systemic theme (churn-conflict
magnets) rather than fixed immediately.
**Unpark condition:** unpark the specific file the next time it is the
proximate cause of a merge conflict or a hard-to-review PR — don't schedule
a speculative refactor; let repeated pain be the trigger, per
`[[feedback-prefer-cruft-cleanup-over-compat]]`-adjacent judgment on
avoiding premature restructuring.
**Source:** 07-10 Dropped #11.

### Boundary reconciliations: insight-origin spoofability, cross-schema view mediation, switchboard-routing-into-src, approvals-gate-into-core
**Idea:** Fix the remaining architecture-boundary violations beyond the
event-transport fix (07-10 move 13): RFC 0011's origin-spoofability gap,
mediate the cross-schema memory→chronicler writes through a proper view
(RFC 0010 pattern), move switchboard routing logic that leaked into `src/`
back to its home, move the approvals gate into core.
**Why parked:** deferred behind move 13's first arrow-fix; the
`eng:boundaries` lens is deliberately graded "weak" to keep ranking pressure
on this cluster in future cycles.
**Unpark condition:** unpark once move 13 (cross-process event transport +
dead-import deletion) ships and the boundary graph is re-measured — pick the
worst-remaining violation from that fresh grep.
**Source:** 07-10 Dropped #12.

### Docs remainder: deployment-truth sweep, invisible-butlers coverage vertical, frontend spec-authority restore, AGENTS.md verify-or-delete compaction
**Idea:** Fix the quickstart doc that starts a deleted postgres service and
the pre-April deployment map; add doc coverage for butlers not yet
documented; restore the frontend's spec authority; compact AGENTS.md's
~150 "contract" notes by verifying each is still true or deleting it.
**Why parked:** deferred except for the prompt-unification slice, which
landed as 07-10 move 10.
**Unpark condition:** the quickstart/deployment-map fix should unpark
alongside 07-10 move 7 (deploy spine) — same underlying "deploy story is
undocumented and dark" defect. The AGENTS.md compaction unparks under
`th-tooling`'s memory-compaction workflow.
**Source:** 07-10 Dropped #13.

---

## Governance and th-projects

### AGENTS.md contract amnesty, Source References enforce-or-repeal, generated RFC status index, manifesto amendment protocol, spec catalog, snapshot-artifact eviction
**Idea:** A cluster of knowledge-architecture hygiene moves: verify or
delete each of AGENTS.md's ~150 "contract" notes (they've become a shadow
spec layer), decide whether the "Source References" convention (149/176
violating) is enforced or repealed, auto-generate the RFC status index
instead of hand-maintaining it, define a protocol for amending the
manifesto, build a spec catalog, and evict stale snapshot artifacts.
**Why parked:** deferred; carried under 07-10's systemic theme #5
("governance stalls at the last step").
**Unpark condition:** the coordinator-protocol clause from 07-10 move 14
("closing an epic archives its OpenSpec change in the same delivery") is
adopted and proven for one full epic-close cycle — that's the concrete
signal that the fleet can sustain metabolism work, at which point this
cluster is the natural next governance investment.
**Source:** 07-10 Dropped #14.

### Spec-writing moves: switchboard classification fast-lane spec, runtime-api capability spec, discretion-honesty vocabulary deltas, core-notify `msg_context` amendment
**Idea:** Write the missing OpenSpec deltas for capabilities that shipped
spec-less: the classification fast lane, the runtime API adapter,
discretion-honesty vocabulary, and a `core-notify` amendment for
`msg_context`.
**Why parked:** deferred; explicitly routed to the doctrine-spec-code
reconciliation epic to mint as children rather than write ad hoc.
**Unpark condition:** `bu-17axsl` (doctrine↔spec↔code reconciliation epic)
reaches a triage pass and mints these as its children.
**Source:** 07-10 Dropped #15.

### Direction verticals: intervention ledger / days-autonomous gauge, proactivity-funnel telemetry, routing-accuracy live scoreboard
**Idea:** Instrument the vision's own unmeasurable success criteria: a
gauge for consecutive days the fleet ran without owner intervention, funnel
telemetry for the proactivity pipeline, and a live scoreboard for routing
accuracy.
**Why parked:** deferred behind moves 1 (proactivity spine), 7 (deploy
spine), and 8 (decision desk) — each of those has to exist and be reliable
before its own telemetry is worth building.
**Unpark condition:** moves 1/7/8 (07-10 numbering) are all shipped and
stable in production — then instrument them, in that order, starting with
the intervention/days-autonomous gauge since it's the vision's most-cited
unmeasurable marker (SC-6/SC-8 per `proj:direction`).
**Source:** 07-10 Dropped #16.

---

## Owner Decision Desk seed queue

These are not "ideas" so much as **decisions the owner has not yet made** —
routed here rather than ranked because no amount of engineering work can
unpark them; only an owner choice can. 07-10 move 8 proposes a first-class
"Decisions" lane for exactly this reason.
**Unpark condition (all four):** the Owner Decision Desk (07-10 move 8)
ships and the owner clears its seed queue — until then these sit as genuine
open questions, not backlog.

- **Delegation-ledger adopt-or-descope** (`bu-gxmfx`) — see the
  cross-butler collaboration entry above; duplicate cross-reference kept
  here because it is simultaneously a parked idea and a pending decision.
- **Dashboard memory-catalog search wiring** — the fleet-knowledge search
  consumer for `public.memory_catalog` (07-04 move 15's third slice) never
  landed; needs an owner call on priority against other dashboard search
  surfaces.
- **`api`-runtime-adapter (API-direct inference lane) re-enable** — sits
  disabled in the live model catalog with no recorded provenance for why;
  needs an owner decision on whether to re-enable, keep disabled, or remove.
- **`PRODUCT.md` adopt-or-delete** — an artifact whose relationship to the
  live `openspec/` and `about/` doctrine tree was never resolved.

**Source:** 07-10 Dropped #24; 07-10 move 8 (seed queue list); QC verdicts on
`bu-gxmfx` and the API-direct lane.

---

## Maintenance note

This ledger is a synthesis input for th-projects milestone-synthesis passes,
not a live bead tracker — beads remain the source of truth for what's
actively being worked. When a parked idea's unpark condition fires, open (or
re-open) a bead referencing this file's entry, then delete or mark the entry
resolved here so the ledger doesn't accumulate stale duplicates of live
work. When a future JARVIS pursuit dossier adds a new "Dropped" section,
fold its genuinely new ideas in here and note where it re-confirms or
resolves an existing entry, following the same merge discipline used above.
