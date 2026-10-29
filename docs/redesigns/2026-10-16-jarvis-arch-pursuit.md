# JARVIS architecture pursuit: run 21 (2026-10-16 to 2026-10-17)

**Reader:** owner deciding which architectural changes ride the RFC 0038 switch and what to release. **Status:** proposed planning record; not adopted requirements or deployment evidence. Researched at baseline `51ec60a37` (Asia/Singapore, 2026-10-16 22:40 to 2026-10-17 about 12:00).

The owner asked for the JARVIS pursuit to start from the Dots diff matrix (the 2026-10-15 ChatGPT Dots gap study, rows R1-R47, A1-I6, J1-J50, K1-K6) and find the feature enhancements and the architectural changes Butlers needs to become a true JARVIS. Run 20 had already mined the matrix for features, so this run's centre of gravity is architecture, with features as each change's first consumers. Twenty-five read-only agents ran in twelve batches, never more than three at once: matrix and RFC 0038 readiness readers, two use-case walks, eleven architecture lenses, four feature lenses, three deep designs, a target-architecture synthesizer and an adversarial critic. 81 candidate moves and 96 owner questions reduce to fifteen ranked moves, five verified bugs, sixteen grouped owner decisions and 16 vetted follow-ups.

Structured evidence (archived): `git show 218607d8e75b7f9bd465335360a87d62b141a30a:docs/redesigns/2026-10-16-jarvis-arch-pursuit-data.json`

## Decision in view

Epic `bu-dhloi1` is held by owner gate `bu-276pli`. Closing the gate releases sixteen children (fifteen moves plus a terminal reconciliation); the owner may close only chosen children instead. Every child carries a full Dispatch Readiness Packet (`bd lint`: zero warnings on all 39 beads this run filed). Sixteen owner-decision beads (label `decision`, assigned to the owner) are separate from the gate; eleven of them block specific children, so answering them is part of releasing the work. The five verified bugs are standalone and dispatchable now.

Most urgent: draft PR #4574 (`bu-fkoexp`, the goal ledger) is writing the old goal status enum, boolean exposure columns and an immutability trigger that blocks `thread_id`, labels and plan carry. Ranks 6 and 7 need those changed while the PR is a draft; otherwise the switch ships a second enum migration and a label backfill. The note is on `bu-fkoexp`.

## North star

A JARVIS for one owner: it knows the owner's world in typed, honest, labelled form; it answers at conversational speed on any surface and can be interrupted; it notices without spending tokens and stays quiet when nothing matters; it finishes whole errands inside readable grants with one tap; every value it shows carries its source and label; it knows when it is blind or broken and says so; and it grows new senses through a governed path that never widens what a third party can make it do.

## The answer

RFC 0038 builds the trust plane (sandbox, catalog, origin ceilings, assertions, effect ledger, exact approvals, fenced Stop) and carries qualities 4 and 5 most of the way. It gives the mind no world (situational context regresses when D15 deletes the preamble with no successor), no conversation that lasts (D9 has no resume; a lead awaiting approval holds a sandbox slot), no way to notice without spending tokens, no self-model (the catalog has no availability field) and no governed growth path, and its goal-wide exposure bit leaves 13 of 15 traced leads exposed.

The smallest coherent set of changes is six program-level moves on four planes, plus the self-model and egress layers that follow on them:

| Plane | What it owns | Ranked moves |
|---|---|---|
| Labels (trust substrate) | Per-invocation labels instead of a goal-wide exposure bit; typed boundaries later; one sensitivity vocabulary; the owner's law over every grant | 6, 10, 15 |
| Perception spine | The owner's world now, next and past as typed, text-free, labelled facts; owner clock and modes | 9 |
| Goal lifecycle | Goals that wait without a session, one thread and inbox, plans across epochs; decision sets and evidence-closed completion later | 7 |
| Condition evaluator | One predicate vocabulary and one deterministic evaluator for waits, watches, rules, guards, modes and expectations | 11 |
| Starting context and recall | What a lead knows at turn one, and a contracted context.search | 8 |
| Owner loop | Latency SLO, owner slot, streamed reply with Stop, one delivery ledger, derived interrupt rights, surface selector | 3, 12, 13 |
| Self-model and healing | Capability health as data, cause lines, outside witness, bounded heal actions | 14 |

Only the shape of each (columns, enums, descriptor fields, deletions with successors, gate metrics) must ride the switch; the behaviour can ride a named second cutover with its own adversarial gate (decision `bu-09lapg`).

## North-star scorecard

From the target-architecture lens; the evidence for each cell is in `.synthesis.scorecard`.

| Quality | Today | After the switch as written | After this architecture |
|---|---|---|---|
| Q1 Situational awareness | presence-only 12-signal TTL bus, model-writable, free text in every… | regresses, preamble deleted with no successor and situation reads expose… | typed four-state slots with source, age and label; /now; timeline.query… |
| Q2 Presence | 8-35 s to first reply with nothing visible, Telegram thread 15 min/30… | 8-23 s turn-bound, D9 no resume, awaiting_approval holds a sandbox slot… | first word ~1.3-2 s, durable thread with inbox join, one subject on every… |
| Q3 Anticipation | 99 schedules (87 jobs, 12 model), correlation only at digest assembly… | obligations with declared grants but no situation guards, no wake… | typed observations, declared rules, owner watches and wake_when on one… |
| Q4 Finishes inside readable grants | boundary is a configuration convention, cosmetic tool toggle, per-tool… | ceilings, assertions, effect ledger, exact approvals, but a refund chase… | 1 goal and 1 tap for the refund chase, decision sets with subset… |
| Q5 Transparent memory and receipts | four sensitivity vocabularies, projections copy third-party text without… | D17 ledger with lineage on cards, boolean taint, goal-wide exposure | per-invocation labels with writer lineage on every row, one vocabulary… |
| Q6 Knows its own health and limits | 2 of 16 owner-felt failure modes page the owner, dead-man pings… | catalog has no availability field, only a blind-spot prose layer fed by… | system.dep slots with four states, capability health in grants and result… |
| Q7 Grows through a governed path | 3.2k-10.4k lines over 24-42 files per connector, catalog has zero rows… | catalog complete and every tool declared, but no provenance or lifecycle… | declared sources as data with generated typed stores and read… |

## Ranked moves

Trust and correctness fixes first, then the architecture whose shape must ride the RFC 0038 switch, then the layers that finish later. Cost is S/M/L.

1. **Close memory-laundering paths before the switch** (`bu-dhloi1.1`; trust fix, cost S; Wave 0). Consolidation mixes owner-class and third-party episodes in one batch, so a crafted email can become an owner Profile Fact; batch membership was never persisted, so the only fail-closed repair is demotion.
2. **Close situation-poisoning and fail-open paths on the context bus** (`bu-dhloi1.2`; trust fix, cost S; Wave 0). Third-party calendar text and General's LLM writes reach every session's prompt as 'explicit' context (context_producers.py:176-184; roster/general/modules/tools.py:98-146), and DND and sleep suppression fail open (core/attention_ledger.py:554-578…
3. **Interrupt rights are derived, never requested** (`bu-dhloi1.3`; trust fix, cost S; Wave 0 + switch). priority='high' skips quiet hours and context suppression (_notifications.py:1060-1065), the RFC 0038 core-notify delta keeps it (spec.md:8), and third_party origin keeps owner notify (RFC 0038:384), so an injected email can wake the owner at 03:00 today and…
4. **Authorship-carrying transforms** (`bu-dhloi1.4`; trust fix, cost S; switch). RFC 0038 D18 (:811-816) and collision-dispositions.md:59 class every transcript as third-party content, so every owner voice note would start an exposed goal at the switch and voice becomes a second-class owner surface.
5. **Owner principal strength** (`bu-dhloi1.5`; trust fix, cost S; Wave 0 + switch). Every guarantee in the harvest rests on 'the verified owner', which on Telegram is one sender id (telegram_user_client.py:1156): a borrowed or taken-over phone is the owner for every standing rule, Vouch and Contain.
6. **Label plane** (`bu-dhloi1.6`; architecture, cost L; switch + M7). D6 makes exposure goal-wide and monotonic, so 13 of 15 traced leads end exposed and lose live research, steering writes and promotion credit.
7. **One goal lifecycle** (`bu-dhloi1.7`; architecture, cost L; switch + M7). architecture.md:396-399 wants outcomes that distinguish completed from partial and continuity outside any transcript, but D9 says 'There is no resume', D11 keeps a lead and its sandbox slot alive in awaiting_approval under a 5-slot cap, and D13 parks one…
8. **Starting context and recall** (`bu-dhloi1.8`; architecture, cost L; switch). D15 and the core-spawner delta delete today's situational preamble and the 3,000-token memory_context and leave the lead to 'retrieve everything else' through a context.search that is one line in the RFC and in bu-u5s4w8, with no ranking, budget, latency…
9. **Perception spine** (`bu-dhloi1.9`; architecture, cost L; Wave 0 + switch + M7). Situational context today is a model-writable TTL bus whose free text reaches every prompt (context_bus.py:74-93, :301, :453), and every reader fails open.
10. **Owner law layer** (`bu-dhloi1.10`; trust fix, cost M; switch). Today the owner can loosen (approval rules) but cannot tighten; RFC 0038 has no owner-writable policy (D4 approval_policy is TOML, :294), and bu-svqpzo.4 removes the only visible control.
11. **One condition evaluator** (`bu-dhloi1.11`; architecture, cost L; switch + M7). Six proposals each invent a predicate language (wake_when, routine anchor, when_situation, read_check, mode entry rules, correlation rules) and nobody owns the evaluator (deep-design-2 finding 9).
12. **Owner-latency lane** (`bu-dhloi1.12`; architecture, cost L; switch + M7). RFC 0038 has no latency target: D3 starts a cold sandboxed CLI per goal, D16 has no interactive role, and the gate (:976-980) requires only no regression against a baseline nobody records, while bu-z0b0ah.4 records 10-60 s of silence.
13. **Owner delivery ledger and surface selector** (`bu-dhloi1.13`; architecture, cost L; switch + M7 + later). There are at least six delivery stores and none records attention; a decided card stays live on other surfaces (RFC 0023:38-40; delivery_lifecycle.py:30-47); the model types channel='telegram' (_notifications.py:282-294); four callback families exist and a…
14. **Capability self-model with no silent failures** (`bu-dhloi1.14`; architecture, cost L; Wave 0 + switch + M7). Health is spread across about nine ledgers and the lead sees only health-module prose, so a dead source looks like 'nothing new'; 2 of 16 owner-felt failure modes page the owner; a failed owner message gets a space-invader emoji (channel_reactions.py:19-20).
15. **Egress plane** (`bu-dhloi1.15`; trust fix, cost M; Wave 0 + switch + later). security.md:381-385 makes egress a membership convention.

Each bead holds the full packet: outcome, non-goals, governing intent, surface map, behaviour matrix, slice plan and acceptance. Payoffs are in `.synthesis.ranked_moves[].payoff`.

## Program

- **Wave 0: before the switch.** Normal releases; deterministic, dark or fix-only; no frozen-path features. Ranks 1-3 and 5 slice 1, the five bugs, spine S1 dark with the /now card and departure edge (`bu-dhloi1.9`), egress default-deny (`bu-dhloi1.15` slice 1), the owner-turn latency baseline and Telegram long-poll (`bu-dhloi1.12`), approval-card retraction (`bu-dhloi1.13`), the gated dead-man ping in Helm (`bu-dhloi1.14`).
- **Wave 1: the RFC 0038 switch (M5 gate, M6 release).** Shape and successors only: label columns and per-invocation gating (`bu-dhloi1.6`), lifecycle enum and tables while PR #4574 is a draft (`bu-dhloi1.7`), the starting packet and context.search contract (`bu-dhloi1.8`), spine S2 with user_context deleted (`bu-dhloi1.9`), owner policy and the ordered grant intersection (`bu-dhloi1.10`), the minimal evaluator (`bu-dhloi1.11`), the interactive role, owner slot and SLO (`bu-dhloi1.12`), delivery rows and one token (`bu-dhloi1.13`), depends_on and cause lines (`bu-dhloi1.14`), one catalog descriptor change, egress profiles for the new hosts (`bu-dhloi1.15`), ASR authorship (`bu-dhloi1.4`).
- **Wave 2: second cutover M7, 'label precision and goal completion' (own adversarial gate).** Typed boundaries; decision sets, broker-computed completion and terminal wakes; streamed replies with native Stop; the live surface selector and Why door; owner watches and correlation rules; the semantic witness and heal actions; the analysis host.
- **Wave 3: incremental.** Declared sources and shadow admission; gap loop; PWA push; routine-anchored obligations, week-ahead forecast and rhythm drift; regimen and dose loop, strain composite, appointment closure; household voice only if decision `bu-muwrlx` un-defers it.

## Amendments the moves carry

Each lands only with the owner's approval of its exact text, which is in the move's bead.

- `bu-dhloi1.3`: RFC 0038 D4 or D7: interrupt class is set by code from origin and owner rules, never by an invocation; the core-notify delta loses model-chosen priority.
- `bu-dhloi1.4`: RFC 0038 D5 and D18: an ASR-class transform preserves the authorship of its input.
- `bu-dhloi1.5`: one security.md sentence: owner authority on a surface is a possession factor; owner-only actions need a second factor.
- `bu-dhloi1.6`: security.md Exposure bullet; RFC 0038 D5, D6, D8, D9, D13 (slot principals), D14, D15, D17, D18, D20.
- `bu-dhloi1.7`: architecture.md core loop gains a Wait step; RFC 0038 D5 and D8-D15, D17.
- `bu-dhloi1.8`: RFC 0038 D15: the runner composes starting context only from owner-authored text and labelled typed values.
- `bu-dhloi1.9`: architecture.md:408-411 rewritten; RFC 0009 superseded; one-line amendments to RFC 0014 and RFC 0029.
- `bu-dhloi1.10`: RFC 0038 D4: owner tightening, never grantable.
- `bu-dhloi1.11`: RFC 0019 narrowed for notify-only owner watches (decision `bu-nt2eda`).
- `bu-dhloi1.12`: RFC 0038 D10, D11, D14, D16 (interactive role and SLO).
- `bu-dhloi1.14`: RFC 0038 D4 (depends_on, typed unavailable results) and D6 (health stamps are trusted).
- `bu-dhloi1.15`: security.md principle 2: every workload's egress is a declared profile in git.

## Owner decisions

Grouped from the agents' questions; each bead lists every sub-question with its options and the agent's recommendation.

| Bead | Decision | Blocks | Pursuit recommendation |
|---|---|---|---|
| `bu-09lapg` | Program shape | `bu-dhloi1.6`, `bu-dhloi1.7`, `bu-dhloi1.12`, `bu-dhloi1.13` | (a) two cutovers, M7 scoped as a child epic of bu-4c7nvp; typed boundaries unconditional in… |
| `bu-o3638f` | Standing rules and third-party values | `bu-dhloi1.6`, `bu-jv1moa.10` | Slot values must have owner, instrument or code data principals unless the rule declares the… |
| `bu-ob3bbc` | Web research under exposure | — | Cached search for non-owner-sender sessions until the switch; after the switch, live research… |
| `bu-2utmy5` | Owner voice | `bu-dhloi1.4` | Yes to owner-input transcripts (ASR backends only; forward_origin forces third-party); yes to… |
| `bu-muwrlx` | Household voice and room audio | — | Defer the household endpoint until labels and disclosure lineage ship; delete the live… |
| `bu-k3wfma` | Second factor | `bu-dhloi1.5` | Yes, in two slices: standing-rule creation and promotion first, then Vouch, Contain, mode… |
| `bu-qlx055` | Goal lifecycle approvals | `bu-dhloi1.7` | One tap on an envelope card satisfies the rule; waits on the owner's own answer need no… |
| `bu-ywqvyh` | Situation visibility | `bu-dhloi1.8`, `bu-dhloi1.9` | Third-party goals see busy/free only; exposed goals cannot read location, body or routine… |
| `bu-u33sb7` | Owner law, interrupts and modes | `bu-dhloi1.3`, `bu-dhloi1.10`, `bu-dhloi1.13` | Adopt the tighten-only overlay; only owner-origin goals, sentinels and owner rules break… |
| `bu-mq929j` | Owner latency | `bu-dhloi1.12`, `bu-dhloi1.14` | Yes to an absolute SLO at the owner-felt seam (first word), drafts allowed (owner-only, inert… |
| `bu-w9z7me` | Self-healing and recovery | `bu-dhloi1.14` | Witness on a second always-on owner device if one exists, otherwise a hosted dead-man service… |
| `bu-nt2eda` | Proactivity | `bu-dhloi1.11` | Narrow RFC 0019 to effects beyond notifying the owner (and fix its citation); the owner… |
| `bu-l8y3fn` | Egress and isolation | `bu-dhloi1.15` | Widening only by a reviewed manifest line; admit analysis runs as read; sentinels hold… |
| `bu-7ry1sy` | Memory authority | `bu-dhloi1.1`, `bu-dhloi1.8` | Demote (reversible from receipts, with Vouch as the way back); steering writes take cited… |
| `bu-iuode4` | Doctrine walls and reach scope | — | Record the walls as recommended (no coding executor; free reservations yes; named shares… |
| `bu-3vm4xa` | Health, routines and household data | — | Keep health records indefinitely with owner-run erase and export; dose cards per regimen… |

## Bugs filed

Each was found by a lens, confirmed by the critic, re-checked by the coordinator at file:line, and filed with steps to reproduce as a standalone bug outside the gate:

- `bu-r9mlu3` (P1): 'traveling' holds routine insights and the daily digest for a whole trip: the producer re-asserts it every 15 minutes and the upsert resets set_at, so the 6-hour max-hold never elapses.
- `bu-fz56hh` (P2): Focus blocks hold nothing while ordinary meetings hold routine insights: the broker's suppressing set omits 'focused'.
- `bu-ikq7vr` (P2): The 06:55 Health briefing reports every active medication without a dose since midnight as 'Missed dose(s) today', whatever its frequency or schedule.
- `bu-8ptawc` (P2): Owner data export of scope 'memory' or 'all' fails: the export map queries a memory schema that does not exist.
- `bu-bvzjhw` (P2): Medication and dose records decay out of 'active' readers after about 201 days, so adherence and history undercount.

## Security during the run

- `bu-svqpzo.13` (filed mid-run): the Switchboard migrations grant relationship, messenger and travel `SELECT ON ALL TABLES IN SCHEMA switchboard` plus default privileges, which reach `butler_secrets` and, after the switch, the Switchboard-owned goal ledger. It blocks `bu-svqpzo.6`.
- `bu-svqpzo.8`: live Codex web search keeps a provider-side `open_page` exfiltration channel in every session. The note offers cached search for sessions whose routed sender is not the owner; decision `bu-ob3bbc` asks the owner.
- `bu-jv1moa.10` (standing replies): the critic's injection. A template-pinned standing rule accepts attacker-chosen typed slot values, so a clean lead could send an owner standing reply with attacker-chosen content and no tap. The bead now requires the slot-principal rule and is blocked by decision `bu-o3638f`.

## Critic's verdicts

- Novelty: of the 22 most valuable harvest moves, 10 are novel, 11 partly filed (closest beads named in the packets) and 1 a duplicate (measured recovery point = `bu-10bbqg`, `bu-kqnum.8`, `bu-e1410`; ledger feedback, not a move).
- Direction: nothing spends, books or touches Slack; nothing gives the lead web or shell; nothing reopens the schedule-origin follow-up rule. Typed-slot research templates would pre-empt the open web-search decision, so they appear only as a decision. The household voice endpoint creates a new non-owner audience and is deferred. A settlement wait needs no new envelope only inside an already-approved effect.
- Claims verified: Telegram `sendMessageDraft` (Bot API 9.3) with a native stop (10.3); no RFC 0038 runtime code on main; 12 `Asia/Singapore` literals; no egress policy for domain pods; the dead-man is wired only in compose, so production has no outside heartbeat.
- Missing lenses: owner-principal strength (now rank 5), the cost of a five-session JARVIS, a rehearsal corpus for qualities 1-3, and third-person privacy beyond erasure. The last three are next-run material.

## Conflicts resolved

The target-architecture lens settled 10 overlaps the deep designs left open (for example three owner-interaction records with two token families become three records and one token; four predicate languages become one evaluator; six grant-narrowing inputs get one order and a receipt; three sensitivity vocabularies become one). The resolutions are folded into the packets and listed in `.synthesis.conflicts`.

## Vetted, not ranked

Real candidates held back as later slices or next-run material; each is in `.synthesis.deferred`.

- Analysis runs
- Measured recovery point
- Region data lifecycle registry
- Household voice endpoint under a household origin (decision `bu-muwrlx`…
- Pixels never reach the lead
- Declared sources
- Gap-to-declaration growth loop
- Routine-anchored obligations
- Week-ahead rhythm forecast
- Rhythm drift as one typed co-deviation observation that states…
- House attended vs owner away
- Owner-written absence evidence
- Regimen and dose loop
- Overnight strain composite
- Appointment loop closure
- Typed-slot research templates for exposed goals

## Ledger feedback

The lenses left 162 notes on existing beads. Grouped per bead, with each agent's 'move N' rewritten to bead ids, they were appended to 85 open beads; closed beads were skipped. No bead's scope, status or priority was changed. Read them with `jq '.synthesis.ledger_feedback[] | {bead, note}'`. The ones that change what to do:

- `bu-fkoexp` (draft PR #4574): change the status enum, labels and trigger while the PR is a draft.
- `bu-pkuny2`: stamp label columns and a lineage pointer, not a boolean taint; memory reads must stop bumping ranking.
- `bu-stbwjc`: one descriptor change carrying the category set, depends_on, provenance and an owner-fast tag, not four.
- `bu-u5s4w8`: the runner's starting context is the four-section packet; context.search needs its contract before anything is built against it.
- `bu-4c7nvp`: add a named second cutover (M7); `bu-0ynlk.6` and `bu-60pwv6.11` re-home onto the owner-latency lane.
- `bu-10bbqg`: RPO measurement and an off-node copy as acceptance (blocked by P0 `bu-e1410`); `bu-viat6h.6`: deny-by-default egress now.
- `bu-h40h2b.8` and `bu-8cdl1.12` fold into the perception spine's timeline; `bu-r0dsz` becomes a D18 transcription function.

## Method and access

- Orchestration: 25 agents in 12 batches about 55 minutes apart over the requested 16-hour window, never more than three in flight; later briefs were written from the durable harvest mid-run. Zero agent errors. Models: opus/high; target-architecture and critic on fable/high.
- Agents were read-only (scratch files outside the tree). No test suite, DB or live stack was run, so costs and payoffs are estimates.
- Access: `git show 218607d8e75b7f9bd465335360a87d62b141a30a:docs/redesigns/2026-10-16-jarvis-arch-pursuit-data.json | jq '.audits["<lens>"]'` (lens keys in `orchestration.batch_plan`); `jq '.synthesis.ranked_moves[] | {rank, title, bead}'` lists the moves; `.synthesis.owner_decisions[].questions` holds every sub-question. Long strings in `audits` are capped for size; the harvest file was not committed.
- Retention: this PR removes `2026-10-14-qa-remediation-pursuit-data.json` from the tree per the two-newest-runs rule; read it with `git show 25983009a9fbdeb82db8743c384fdae6a49fc8d8:docs/redesigns/2026-10-14-qa-remediation-pursuit-data.json`.
