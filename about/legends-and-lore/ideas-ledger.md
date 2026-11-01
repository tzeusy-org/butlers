# Ideas Ledger

This is the durable home for ideas that were raised, judged real, and then
explicitly **parked** rather than pursued immediately. It exists so that
deferred work has a place to live with a stated reason and a concrete
**unpark condition** — the signal or milestone that should cause someone
(owner or a future th-projects milestone-synthesis pass) to reconsider it —
instead of evaporating into closed dossiers that nobody re-reads.

## Provenance

The first sections cover dossiers through 2026-07-10 as per-idea entries. The
"Folded from the 2026-07-12 to 2026-10-16 pursuit runs" section carries every
later run through 2026-10-16 as themed clusters; per-run lists live in git
history, and run metadata in `docs/redesigns/pursuit-ledger.md`. The 07-22 run's
15 cut moves are read with
`git show b23d5b76afab:docs/redesigns/2026-07-22-jarvis-pursuit-data.json | jq .synthesis.dropped`.
The 09-22 run has no Dropped section; its cuts are dedup records in its data
file.

Except for the RFC 0019 egress entries, every entry is sourced from the **JARVIS relentless pursuit** dossiers
under `docs/redesigns/` (skill: `.claude/skills/butlers-development/subskills/butler-relentless-jarvis-pursuit/`),
a recurring generative audit of the Butlers ecosystem against
`about/heart-and-soul/vision.md`:

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

(The delegation ask/answer ledger and domain-event subscriptions / fleet case
files from 07-04 have since been built; see RFC 0032.)

---

## Ecosystem: connectors and perception

### Discord connector (deferred 2026-10-04)

Deferred from v1 by owner decision: only a non-production draft existed and no butler depended on it. **Unpark condition:** the owner names a Discord community or DM flow a butler should perceive; re-enter through a new proposal using the bot-token path (RFC 0018).

### New connectors (flight status, weather, parcels)
**Resolved in part:** SimpleFIN (Finance bank feed, `simplefin-sync` job in the
`butler-finance` spec) and ActivityWatch are admitted to v1 per
`about/heart-and-soul/v1.md`. Three of the original five remain parked.
**Idea:** Expand perception with sound, manifesto-grounded connector
integrations.
**Why parked:** correctly-scoped ecosystem expansion, but deliberately kept
out of the top-15 ranked slots against trust-defect fixes each cycle.
**Unpark condition:** owner opens a dedicated connector epic (per
`adding-connectors-and-modules`), or explicitly requests one of the three by
name (flight status, weather or parcels). Note the 07-10 dossier's `eco:reliability`
finding that existing connectors already go dark 7+ weeks unnoticed on pull-only health — fix that
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

### Speculative reply drafting, wake-anchored delivery
**Idea:** Two refinements to how insights reach the owner: pre-drafting
likely replies, and timing delivery to when the owner is actually awake.
(Per-category insight feedback, the third 07-04 idea, shipped as RFC 0011
Amendment 7.)
**Why parked:** both assume the attention ledger and decision-loop
primitives exist first.
**Unpark condition:** RFC 0021's decision loop and the attention ledger
reader have both shipped, so these are the natural next slice of
proactivity polish; open a bead when prioritized.
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
**Why parked:** deferred behind moves 1 (proactivity spine) and 7 (deploy
spine). The former move8 product prerequisite was retired by owner decision
bu-g2zuv2 = A.
**Unpark condition:** moves 1/7 (07-10 numbering) are shipped and stable
in production, then instrument them, starting with
the intervention/days-autonomous gauge since it's the vision's most-cited
unmeasurable marker (SC-6/SC-8 per `proj:direction`).
**Source:** 07-10 Dropped #16.

---

## Folded from the 2026-07-12 to 2026-10-16 pursuit runs

Themed clusters, not per-item rows. Each cluster merges the Dropped,
deferred and "vetted, not ranked" sections of the runs listed in its Source
line; the full per-run lists stay in git history (`git log -- docs/redesigns/`
for the dossier bodies and their data files). Where a cluster extends an
entry above, it says so rather than restating it.

### Surface truth drive-bys, one list per dashboard page
**Idea:** The small per-page honesty repairs every audit run found and cut for
slots: degraded voices on attention lists, one number for one fact (the
sessions-24h trio), owner-timezone buckets and filters (the fourth instance of
the timezone class), optimistic-update reverts, `isError` legs on stat bars,
dead doors and dead tabs, one failed definition per page, mutation-failure
pixels, virtual-clock and ticking countdowns. Pages covered: notifications,
sessions, timeline, issues and audit, approvals and decisions, calendar,
health, spend, memory, education, chronicles, entities, QA, ingestion,
settings and secrets, butler detail and the roster board. Also from 07-22:
sessions model and complexity filters with corrections surfacing, the audit-log
keyset migration with a privileged-predicate index (real, live-measured), and
a calendar staleness severity ladder.
**Why parked:** S-cost polish below the trust line while ranked moves
repaired the underlying contracts; many are one-line drive-bys.
**Unpark condition:** a PR already touching that page, or an owner-opened
roster-truth mini-epic; take the item as a drive-by with its own test. Items
that named a real history-rewrite or fabricated-day defect (immutable `acked_at`,
1904 archive clamp, fleet-honest re-embed) deserve their own bead the next
time that page is edited.
Three 07-22 cuts were folded into filed moves (categorical-token fixes, the
permissions-matrix un-seeding, the roster board's bus-aware refresh caption).
**Source:** 07-12, 07-17, 07-22, 08-09, 09-05 runs. Extends the page-local UX
section above. Audit-log keyset and index unpark when audit-log latency hurts;
the index slice is a good S for a quiet week.

### Keyboard, accessibility and visual-language adoption debt (extension)
**Idea:** Global focus-visible floor with an `outline-none` lint, one
announcement primitive (so chat is audible), icon-button label lint,
skip-to-content link, accessible-chart generalization, focusable hint
primitive replacing `title=`, `useListTriage` adoption on remaining registers,
scope-ranked command registry, `@theme` Dispatch retokening (the 170-site AA
contrast fix alone is a candidate S), status registry and attention primitive,
Badge-to-KindTag collapse, single rem type scale, one pending vocabulary,
mobile/coarse-pointer lane and phone Playwright gate.
**Why parked:** repeatedly cut for trust-weighted ranking; adoption debt
rather than a defect.
**Unpark condition:** the accessibility and keyboard batches above are
opened; fold these in as their next slices. File the global focus-visible
floor and the announcement primitive first.
**Source:** 07-12, 07-17, 09-01, 09-03, 09-05, 09-12 runs.

### Cross-butler collaboration: cases, loops, joint work
**Idea:** Fleet case consumers and archetypes, answerable-question registry,
typed answer contracts, owner-hold register and disposition verbs, delegation
loop closure (answer return path, multi-target, roster adoption), joint
objectives, `delegate_act`, co-signed conditions, fan-out join, negotiated
handoff, single-voice reply for multi-domain messages, cross-butler burst
coalescing, owner experiments, care circle, standing context subscriptions
and life-event ripple, cross-referential briefing, delegated-QA memory
catalog.
**Why parked:** each depends on the case-file and delegation substrate landing
and proving use; several would collide with the in-flight cross-butler bet of
the run that dropped them.
**Unpark condition:** fleet case consumers are live with real rows, and a run's
evidence shows delegation traffic that the existing ask/answer ledger cannot
express. Anything that moves the schema-isolation boundary (RFC 0010
cross-schema fact-read federation) is held for an explicit owner decision and
must not be fleet-dispatched.
**Source:** 07-12, 07-17, 07-22, 07-25, 09-03, 09-05, 09-12, 10-03 runs.
Extends the cross-butler collaboration section above. Standing subscriptions
and ripple are the explicit next candidate once delegation has rows.

### Knowledge graph, memory and provenance
**Idea:** Belief-revision ledger and contradiction docket, evidence digest
that outlives the episode, cited per-artifact provenance, entity-anchored
fleet dossier and graph traversal, expected knowledge and entity-grounded
recall, chronicler correction verb for owner testimony, reinforcement-aware
decay, rule-application loop and rule maturity (226 of 226 rules candidate
with zero applications), earned-autonomy bridge from scope-template
fingerprints, challenged-not-overwritten facts, measured shelf life.
**Why parked:** need a design pass on what "helped" and "believed" mean, or
wait on the graph substrate's first consumer.
**Unpark condition:** the entity graph has a first live consumer and a
rule-maturity definition has an owner-approved spec; open the design bead
before any slice.
**Source:** 07-12, 07-17, 09-03, 09-05, 09-12, 10-03, 10-06 runs. Extends the
knowledge-graph section above.

### Proactivity, owner state and attention
**Idea:** Earned-voice ratchet, weekly unasked receipt, waiting-on-them
producer, watch registry, rhythm-deviation and week-ahead forecast, expiry
harvest, owner-absent autonomy envelopes and compensating-action registry,
outcome-evidenced attention shaping, "the fleet keeps its word", presence-gated
briefing cadence, owner-question and sensory-outage lanes, free-moment
delivery, briefing-to-insight bridge.
**Why parked:** each assumes delivery is trustworthy and an absence or
ownership primitive exists first.
**Unpark condition:** the insight-delivery path is stable for a full cycle and
the absence primitive from the last ranked proactivity move is live; then
take the cheapest producer as the first consumer.
**Source:** 07-12, 07-17, 07-25, 09-03, 09-05, 09-12, 10-03, 10-06 runs.
Extends the proactivity section above.

### Inference economics and spawn scheduling
**Idea:** Two-lane spawn scheduler so interactive work never queues behind
maintenance, warm-worker session continuity (RFC-shaped), cache-affinity and
volatility-ordered prefixes, shift-session coalescing, local-inference
adapter, tier-fit calibration and session effects manifest, dispatch SLOs and
consequence-gated verification, exploration and regret budgets, guarded
self-escalation, per-butler spend ceiling, telemetry coalescing at the route
boundary, roster-wide schedule tiering and budgets, policy-conformant fallback
constant, queue-depth metrics for the three-slot consolidation semaphore, and a
manual stop primitive for running sessions (needs a new spawner kill
capability).
**Why parked:** efficiency-only or measurement-gated; two dropped runs
measured the win as small. The spawn-phase latency spine is the enabler.
**Unpark condition:** the latency spine and receipt vocabulary exist and a
day of composition data shows a queueing or cache-miss cost worth the
change; warm-worker continuity needs an owner decision first.
**Source:** 07-12, 07-17, 07-22, 08-09, 09-03, 09-05, 09-12, 10-03 runs.
Extends the inference-economics section above. The stop primitive unparks with
the spawner kill capability; the semaphore metrics are observability only.

### Connectors and perception (extension)
**Idea:** Bank e-statement capture, GitHub owner-events digest, flight status,
multi-device ActivityWatch spec, lifestyle media capture (Immich, scrobble,
read-later), Singapore civic and LTA commute feeds, voice-call metadata,
owner-documents registry, HIBP breach perception, Oura wearable, Google
Photos, shipment lifecycle, clinical FHIR, authenticated web-action module,
IMAP, Todoist, Paperless-ngx, HA companion-app phone perception, sovereign
gazetteer, jurisdiction-true civic calendar, FX reference-rate feed.
**Why parked:** roadmap-lane work gated on actual owner use, manifesto
ownership or a privacy review; connectors are expensive to keep monitored.
**Unpark condition:** the owner names the source, or the connector-roadmap lane
opens it, after the existing connector fleet shows seven days of reliable
heartbeat. Manifesto-gated items (shipments, travel promises without a
substrate) need an owner-decision bead first.
**Source:** 07-12, 07-17, 07-25, 08-09, 09-01, 09-03, 09-05, 09-12, 10-03,
10-06, 10-15 runs. Extends the connectors section above.

### Domain follow-ons: finance, travel, health, education, home, relationship
**Idea:** Finance: statement-ledger parsing (a Finance manifesto scope question, so an
owner decision first), statement ingestion, income streams and safe-to-spend,
return, refund and warranty desk, cancel-and-verify pack, pay-versus-use,
subscription shadow price. Travel: destination admissibility, trip money,
trip pack, destination brief, timezone spine, fare economics. Health: baseline
deviation follow-ons, regimen and dose loop, appointment loop closure,
overnight strain composite, load versus recovery, symptom "what preceded this".
Education: answer door in chat, concepts as entities, curriculum readiness date,
misconception ledger. Home: tariff-aware energy, usage-metered maintenance,
home safety and presence sentinels beyond the first slice, house attended
versus owner away. Relationship: rule practice ledger, disclosure boundary,
introductions, counterparty-aware scheduling.
**Why parked:** each waits on the ranked move that lands its substrate (cost
claims, absence primitive, mastery honesty, owner places).
**Unpark condition:** the named substrate move is merged; then the first
slice is the one the substrate's own dossier called the natural successor.
**Source:** 07-12, 07-22, 09-03, 09-05, 09-12, 10-03, 10-06, 10-15, 10-16 runs.

### Owner surfaces: Telegram, phone and chat
**Idea:** Telegram media ingress (voice to transcript, photo to artifact),
pocket command lane, doors in outbound messages, "what I held back" recap,
live pinned "Now" card, offline trip pack, Telegram Mini App console
(conflicts with the `web_app` refusal and needs its own auth design),
long-audio transcription, real chat token streaming, mobile PWA and offline
shell, witnessed citations, answer drift, a unified cross-channel conversation
ledger (the structural successor named by the parity slices), page-context
enrichment beyond detail pages.
**Why parked:** the delivery path had to be honest first, and token streaming
is blocked by the SDK's `communicate()` call.
**Unpark condition:** outbound doors and delivery receipts are stable; take
the recap and doors first. Streaming waits for an SDK change.
**Source:** 07-12, 07-22, 09-02, 09-03, 09-12, 10-06, 10-15 runs. The
conversation ledger unparks when the S and M parity slices have landed;
page-context enrichment follows the grounding loop proving out.

### Sovereignty, security posture and owner decisions
**Idea:** Last-copy ledger, sealed envelope, incident ledger and dependency
map, sovereignty page, digital-estate registry, domain and certificate expiry
probes, distrust-a-source containment, mail disposition ledger with undo
receipt, capability-gap ledger and governed growth proposal, network-less
analysis sandbox, region data lifecycle registry, measured recovery point,
content-blind estate registry.
**Why parked:** need an owner decision on the data boundary or wait for
recovery proof from the last ranked sovereignty move.
**Unpark condition:** the owner opens a sovereignty or digital-stewardship
epic, or a recovery drill proves the substrate; start with the expiry probes
(S).
**Source:** 09-12, 10-03, 10-15, 10-16 runs.

### Deliberation, household, place and life-event ideas
**Idea:** Decision-premise tripwires, option sheets, personal trials, care
circle and need-to-know grants, handover brief, leg ledger, venue binding,
vehicle as sensed asset, safety check-in, emergency card, food-nutrition
loops (eaten-out sense, itemized baskets, recall watch), employer custody,
vocation butler, employment record, commute briefing podcast.
**Why parked:** vetted real but outside every ranked lens; most need a new
domain owner or a manifesto amendment.
**Unpark condition:** the owner states the life domain matters, or a butler
manifesto is amended to own it.
**Source:** 10-03, 10-06 runs.

### Platform and SDLC remainder
**Idea:** Import-graph test selector, selective merge-group for docs-only and
frontend-only PRs, affected-Vitest on PRs, retire `frontend/src/api/index.ts`,
`renderWithProviders` harness home, migration chain rebaseline behind a
catalog-equivalence proof, retire the never-run release pipeline, compose-era
retirement, flake ledger, one durable owner outbox, one model-attempt loop,
one roster namespace, strict `butler.toml` schema, constants for never-set
env knobs, `meta.sources_degraded`, one `KeysetPage` and cursor codec, one
empty/error/loading kit, grep-grade replacement for the visual-role ESLint
rule, shared Home Assistant websocket client, calendar monolith decomposition (7,199
lines, hygiene without owner-visible payoff; see the hot-path decompositions
entry).
**Why parked:** later slices behind the ranked CI and simplification moves,
or owner decisions that narrow a gate.
**Unpark condition:** the ranked move each item names has merged; owner
decisions (selective merge-group, compose retirement, release pipeline) need a
decision bead.
**Source:** 07-22, 10-06 (SDLC velocity) and 10-13 (simplification) runs.

### Dots-superset and architecture remainder
**Idea:** Returns desk, find-a-time with internal holds, weekly time
intentions, protected work block per promise, grounding receipts, research
library, public context in meeting prep, decision projects, paperwork ledger,
rule rehearsal, drafting-voice learner, obligation preconditions and cost
cards, cache-stable lead prefix, analysis runs, pixels never reaching the
lead, declared sources and gap-to-declaration growth loop, routine-anchored
obligations, typed-slot research templates for exposed goals. The homecoming
routine is excluded on doctrine: it would actuate from tainted trip rows.
**Why parked:** held behind the 15 ranked moves of their runs.
**Unpark condition:** the move each item rides (per the pursuit ledger) has
landed and its gate is closed.
**Source:** 10-15 and 10-16 runs. Run indexes: `docs/redesigns/pursuit-ledger.md`.

---

## Engineering owner-decision seed queue

These are **decisions the owner has not yet made**; only an owner choice can
unpark them. Review these through local `bd` or an owner session; the former
product destination is retired by owner decision bu-g2zuv2 = A. (Delegation-ledger adopt-or-descope and `PRODUCT.md`
adopt-or-delete are resolved: the ledger is built and wired, and `PRODUCT.md`
no longer exists.)

- **Dashboard memory-catalog search wiring** — the fleet-knowledge search
  consumer for `public.memory_catalog` (07-04 move 15's third slice) never
  landed; needs an owner call on priority against other dashboard search
  surfaces.
- **`api`-runtime-adapter (API-direct inference lane) re-enable** — sits
  disabled in the live model catalog with no recorded provenance for why;
  needs an owner decision on whether to re-enable, keep disabled, or remove.

**Source:** 07-10 Dropped #24; 07-10 move 8 (seed queue list).

---

## Egress: owner-delegated actions (RFC 0019)

Each of these needs its owning butler's manifesto amended to permit the action,
plus the doctrine decision on the parked automation rule engine in
[RFC 0019](rfcs/0019-proactive-egress-and-automation-parked.md).

### Payment / transfer initiation (Finance)

- **Idea:** Initiate bank or payment-API transfers.
- **Why parked:** `critical` risk; irreversible, money leaves the account.
- **Unpark condition:** Finance manifesto amended to permit transfers, with mandatory per-transaction confirmation and no standing rules.
- **Source:** RFC 0019 (2026-06-14).

### Booking confirmations / modifications (Travel)

- **Idea:** Change or cancel flights, hotels, and reservations via provider APIs or email workflows.
- **Why parked:** `high` risk; fees and irreversible cancellations.
- **Unpark condition:** Travel manifesto amended to permit provider-side changes, and the per-event approval path covers them.
- **Source:** RFC 0019 (2026-06-14).

### Grocery / delivery ordering (General + `shopping` module)

- **Idea:** Submit orders to delivery services.
- **Why parked:** `high` risk; a financial transaction plus logistics, with no owning butler today.
- **Unpark condition:** An owning butler's manifesto claims ordering, and a delivery-service integration exists.
- **Source:** RFC 0019 (2026-06-14).

### External contact enrichment (Relationship)

- **Idea:** Look up contacts against external social-profile APIs (LinkedIn, etc.).
- **Why parked:** Privacy- and rate-limit-sensitive; `high` risk for any external lookup.
- **Unpark condition:** Relationship manifesto amended to permit external lookups, with a privacy review of what leaves the system.
- **Source:** RFC 0019 (2026-06-14).

### Photo / media organization with face recognition (General + `media` module)

- **Idea:** Tag media by the people in it via face recognition.
- **Why parked:** Significant new infrastructure; privacy-sensitive even though filing is reversible.
- **Unpark condition:** A media store exists and the owner accepts on-device face recognition.
- **Source:** RFC 0019 (2026-06-14).

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
