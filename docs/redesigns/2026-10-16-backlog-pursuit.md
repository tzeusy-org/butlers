# Backlog pursuit, run 22 (2026-10-16/17)

**Reader:** the owner deciding what to release, and any coordinator or agent planning work from the
beads backlog. **Status:** point-in-time record of one run; the live state is in bd. **Lens:**
non-standard. Instead of an ecosystem or UX audit, this run groomed every live bead so the fleet
can actually dispatch it, under the beads-orchestration writer spec and the th-engineering and
th-projects principles. No product surface was tiered, so the tier board does not move.

Naming: this run overlapped the JARVIS architecture pursuit, which landed first as run 21. The
working files, bead titles and the `backlog-pursuit-run21` label written during the run call this
one "run 21"; in the ledger it is run 22.

- Window: 2026-10-16 22:55 to 2026-10-17 09:30 +08:00, staggered: 12 batches about 55 minutes
  apart, at most 3 agents at a time, 36 agents in total (opus, high effort).
- Baseline: main@490d6e008 and a full `bd export` at 2026-10-16 22:39 +08:00 (669 live beads).
- Release gate: **bu-l25s6i** (owner). Epic: **bu-1mbyoa** (backlog graph hygiene).
- Data (archived): `git show 218607d8e75b7f9bd465335360a87d62b141a30a:docs/redesigns/2026-10-16-backlog-pursuit-data.json`. One record per agent:
  `git show 218607d8e75b7f9bd465335360a87d62b141a30a:docs/redesigns/2026-10-16-backlog-pursuit-data.json | jq '.audits[] | select(.page=="<key>")'`
  (keys are partition names such as `rfc0038` or `dots-lead`, and the cross-cutting passes
  `x-owner-asks`, `x-owner-vet`, `x-dedupe`, `x-critic-1`, `x-critic-2`, `x-leftovers-1..3`).
  `.metrics`, `.defect_classes` and `.owner_packet` hold the run-level numbers.

## North star for this lens

The pursuit north star (`pursuit-ledger.md`) measures the mental labor the system absorbs for one
owner. The backlog version: every live bead is either claimable now with a full Dispatch Readiness
Packet, or held by a real, visible blocker that names who or what releases it. Nothing waits on a
dead session, a label, or a question someone already answered.

## Outcome

| Measure (live beads) | Before (10-16 22:39) | After (10-17 09:25) |
|---|---|---|
| Live beads | 669 | 676 |
| Status open / blocked / in progress / deferred | 277 / 357 / 24 / 11 | 461 / 192 / 8 / 15 |
| Claims held by dead coordinators or agents | 239 | 0 |
| Beads with a full packet (design, acceptance, one complexity label) | 294 | 549 |
| `blocked` with no open blocking edge (includes legitimate PR-review and owner-action holds) | 233 | 106 |
| Open, unassigned, `owner-gated` by label only (still claimable) | 27 | 5 |
| Unassigned non-epic beads in `bd ready` with a full packet | about 19 | 143 |
| Decision-bead linter (`scripts/lint_decision_beads.py`) | 18 of 26 failing | 0 of 20 failing |

Of the 669 baseline beads, 107 are now closed (most by this run's groomers, some by the live
coordinator merging PRs during the run), 367 open, 175 blocked. Groomers closed 114 beads in total:
42 already done on main, 49 obsolete (mostly superseded by RFC 0038), 23 duplicates. They created
108 (split-out first slices, missing review beads, reconciliation children, and owner decisions in
the repo convention), of which 95 are live.

The fleet used the result while the run was still going. The live Codex coordinator
(`coordinator:codex-20261016-03`) claimed beads within minutes of a groomer making them runnable
(bu-n7zmbp.1, bu-fkoexp, bu-stbwjc) and opened PRs #4572 to #4587 during the night.

## Method

1. **Partition.** The 669 beads were clustered into 28 disjoint partitions by epic tree,
   lens and age (for example `rfc0038`, `dots-lead`, `old-0508`, `testlanes`), so no two
   concurrent agents could edit the same bead.
2. **Groom.** Each groomer read one shared rules file and a generated brief (every bead with its
   status, claim age, heartbeat, open blockers and notes tail, roots first), then for each bead:
   reality-checked it against `git log origin/main --grep` and open PRs, released dead claims with
   guarded `bd update --if-assignee` (never `--force`), rewrote the packet into the structured
   fields, removed over-strict edges, added real ones, closed what was done, duplicated or
   superseded, decided engineering calls with a recorded rationale, and filed true owner questions
   as convention decision beads. Every mutation carried `--actor backlog-pursuit-2026-10-16/<key>`
   and a stamped note.
3. **Learn between batches.** Each batch's lessons were folded into the shared rules before the
   next batch (78 by the end), so later groomers started where earlier ones stopped.
4. **Cross-cutting passes** after every partition was done: owner asks into one packet
   (`x-owner-asks`), an adversarial vet of that packet plus an overreach audit of groomer decisions
   (`x-owner-vet`), a whole-backlog dedupe (`x-dedupe`), a dispatch-gate audit of the ready set
   (`x-critic-1`), an anomaly audit of what was still blocked (`x-critic-2`), and three passes over
   the 300-odd items groomers handed to other partitions or to the coordinator (`x-leftovers-1..3`).

Agents had no code, commit or PR authority. PR-state changes (mark ready, merge, close) stayed with
the fleet coordinator: 154 Step 0 handoffs are recorded as plain-prose notes on their beads.

## Defect classes

What kept work away from the fleet was mostly mechanical graph state, not missing ideas.

1. **Dead claims (239).** Coordinator sessions that ended without releasing their beads. One
   (`coordinator:01a13679…`, last seen 2026-10-16 09:49 +08:00) held 144. A bead assigned to a
   dead actor looks taken to every live one, so nobody touches it.
2. **Hand-set `blocked` with no edge (233).** Status set by a coordinator for a reason that lived
   only in its context: "file custody" holds that serialized a whole file behind stalled PRs,
   waits on migration numbers someone else had since used, waits on PRs that touched other files.
   About 60 of the 106 that remain are legitimate (`pr-review-task` beads, which sit `blocked` by
   convention, and owner actions).
3. **Owner gates as labels (71 labelled, 27 still claimable).** An open, unassigned bead labelled
   `owner-gated` still appears in `bd ready`. A real gate is an edge to an owner decision bead.
4. **Graph shapes that hide children.** Parents holding a `blocks` edge on their own child (bd then
   hides every child from `bd ready`), leaves waiting on their own epic (deadlock), a closed epic
   with live children, open parent tasks that look claimable.
5. **Packets in prose, or in jargon.** 375 beads lacked structured design or acceptance fields, or
   carried compressed dead-coordinator appendices ("candidate packet - NOT ADOPTED") that a worker
   cannot act on. The coordinator's readiness gate silently skips them.
6. **Whole-move stalls.** Multi-slice moves (several run-15 and run-17 moves) waited as one unit
   on their hardest slice. Scoping each to its first slice, often a real bug fix on a surviving
   seam, made it runnable; the later slices became their own beads.
7. **Over-escalation.** 106 raw owner asks shrank to 19 decisions. The rest were already answered
   (merged spec text, the RFC 0038 collision dispositions, owner answers recorded in notes, the
   2026-09-30 escalation batch applied in PRs #4314 to #4319 and #4329), or were engineering calls
   that decision-autonomy assigns to agents. The overreach audit sampled 33 groomer decisions on
   grants, retention, security posture, doctrine and scope and reversed none.
8. **Claims under the owner's identity (31).** Agent sessions that ran with the owner's git name
   (`Tzeusy`, `tzeusy-orca`) left claims every other agent treated as the owner's. 20 with proof of
   an agent claim and no activity for 7 days were released, each with an undo command in the owner
   packet; 5 without proof are in the packet for the owner's yes; the rest are genuine owner work.

## Owner packet

The packet (`.owner_packet` in the JSON; also on each decision bead) lists 19 decisions, ordered by
what they unblock, each with options, a recommendation and a status-quo default; all pass the
decision linter. One more, bu-ilqhh0 (cached Codex web search for non-owner sessions before the
switch, a security-posture call, due 10-30), was filed by the last leftovers pass after the vet and
gates nothing today. The largest releases:

| Bead | Ask | Recommendation |
|---|---|---|
| bu-i4khaz [HOLD] | Release the 15 run-18 simplification moves | Release; remove QA Retry; defer S4 |
| bu-xmd7yu [HOLD] | Approve the D19 dashboard change (PR #4553) | Approve as written |
| bu-gjmx8w [HOLD] | Adopt the QA remediation direction (PR #4517 merged) | Adopt and release |
| bu-cs33zx | Approve D20 brokered web research (PR #4540) | Approve; yes to the three sub-answers |
| bu-4awqyt | Relationship temporal gate G | Plain hard cutover (reverses your 10-04 choice, so yours) |
| bu-t1vo54 | Narrow REQ-testing-053 exception for PR #4583 (due 10-17) | Approve |
| bu-met99c | Enable delete-on-merge and prune about 2,167 stale branches | Approve (destructive, so yours) |

It also lists owner actions agents cannot do (Docker-host harness runs, a dev Codex
reauthorization, prod facts), 11 PRs that need only an owner review or merge (#4512 and #4519 are
CLEAN with a passing review), six spec drafts coming as PRs for adoption, and everything settled
without the owner, each reopenable.

## Ranked moves (gated)

All children depend on gate **bu-l25s6i**; the epic is assigned to the owner. `bd lint` is clean on
all five beads.

1. **Backlog graph lint** (bu-1mbyoa.1). `scripts/lint_backlog_graph.py`, read-only, same input
   and JSON contract as `scripts/lint_decision_beads.py`: G1 parent blocks its own child, G2 bead
   waits on its own ancestor, G3 blocked with every blocker closed, G4 blocked with no edge and no
   exempt label, G5 claimable parent, G6 label-only owner gate, G7 stale claim (warning). Local and
   Makefile only; CI cannot reach Dolt.
2. **Weekly digest row** (bu-1mbyoa.2). The existing decision-review digest runs the lint against
   the mounted export and records one aggregated attention event when G1 to G6 findings exist, with
   the same failure handling as the decision lint.
3. **Reconciliation, gen-1** (bu-1mbyoa.3). Run the lint on a live export, fix or explain every
   finding, confirm the digest row, close the epic. Known edge cases to settle there: bu-24lu6.8
   (a closeout blocked by its own owner-authorization child), bu-q1hzqn and bu-s1u4br (open parents
   with their own acceptance criteria).

This run also landed the durable rules directly, in this PR: `AGENTS.md` (coordinator guardrails:
graph states that hide work, guarded claim release, own bd actor) and
`docs/operations/decision-beads.md` (gating work on a decision).

## Recommendations outside this repository

These concern the beads-orchestration skill package, which lives in the owner's dotfiles:

- **Release dead claims in coordinator preflight.** A coordinator that finds a `coordinator:*`
  claim whose heartbeat and `updated_at` are both older than the stall threshold should release
  it with a guarded update and a note. Without that, every crashed session strands its claims
  (239 here).
- **Give every agent its own bd actor.** Default `BEADS_ACTOR` per session so claims never fall
  back to the owner's git name.
- **Readiness gate: skip owner-assigned and owner-gated beads**, and treat a `pr-review` original
  with no open review bead as an orphan to file, not as work.
- **Before escalating, search for the answer**: `git log --grep <id>`, the change's
  collision-dispositions file, adopted RFC tables, and owner notes.

## Lessons for the next backlog pass

- Trust `git log origin/main --grep <id>` over notes: many "pending" beads had landed.
- Brief dependent counts must be direct edges; transitive counts overstate a bead's leverage.
- Prefer status `open` plus a real `blocks` edge over hand-set `blocked`; check the bead then
  appears (or does not) in `bd ready` as intended.
- Before re-scoping a parent to its first slice, grep for "split from <parent>": another pass may
  already have split it.
- Pre-RFC-0038 changes: read `openspec/changes/generalist-lead-focused-delegation/collision-dispositions.md`
  first; it states archive-first or re-scope per change. Deterministic jobs (D2) are not frozen.
- Title similarity finds sibling slices, not duplicates; real overlaps came from full-field
  comparison and from beads that name each other with no edge.
- A design refreshed without its acceptance criteria can keep a stale owner gate: make the
  description, design and acceptance agree.

## Retention

This PR adds the run-22 data file and prunes `2026-10-15-dots-superset-pursuit-data.json`
(run 20); its dossier now reads it from `git show 3df72ae7c552:docs/redesigns/...`. Runs 21 and 22
keep their data in the tree.
