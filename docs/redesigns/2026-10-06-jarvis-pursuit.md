# JARVIS pursuit: run 16 (2026-10-06)

**Reader:** owner deciding what new work to release. **Status:** proposed planning record; not adopted requirements or deployment evidence. Audited at baseline `091af61db` (Asia/Singapore, 00:15 to 12:00); main did not move during the run.

Fifteen moves survive deduplication against fifteen prior dossiers and about 400 non-closed beads: seven trust repairs lead, eight new capabilities follow. Run 15's fleet shipped 13 of its 15 moves in three days; QC confirms eight as designed and finds five partial, and their untracked residues are ranks 1, 3 and 4. All five rotating lenses were new to the pursuit.

[Structured evidence](2026-10-06-jarvis-pursuit-data.json)

## Decision in view

Epic bu-s11n0s is held by owner gate bu-9zmre6. Closing that gate releases the sixteen children (fifteen moves plus one terminal reconciliation). It does not adopt specs, deploy anything, provision credentials or authorize external actions; the owner may instead close only chosen children. Every child carries a full Dispatch Readiness Packet in its structured fields (`bd lint`: zero warnings on 18 beads).

| Rank | Move | Kind | Cost | Bead |
|---|---|---|---|---|
| 1 | Endpoint custody holds: 'my phone is gone' or 'that sign-in wasn't me' strips that channel's ow | trust | L | bu-s11n0s.1 |
| 2 | Owner-verified means the owner verified it: server-derived authority on relationship.entity_fac | trust | M | bu-s11n0s.2 |
| 3 | Finish run 15's seams: a writer for allowance_account, posture on the birthday push, and the be | trust | S | bu-s11n0s.3 |
| 4 | Calendar context truth: RSVP, transparency and the event type the owner declared decide whether | trust | S | bu-s11n0s.4 |
| 5 | Count strips tell deaf from quiet: time-keyed buckets with per-bucket listening state, and no f | trust | M | bu-s11n0s.5 |
| 6 | Served-identity attestation: every attempt records what actually served it, and requested-not-s | trust | S | bu-s11n0s.6 |
| 7 | Keep the location retention promise: raw fixes expire on a declared horizon once their places a | trust | M | bu-s11n0s.7 |
| 8 | Break-through list: the owner declares who and what may reach them in quiet hours, checked on e | capability | M | bu-s11n0s.8 |
| 9 | Payment-redirect guard: a request to pay a known counterparty somewhere new is checked jointly  | capability | M | bu-s11n0s.9 |
| 10 | Absence Plans: owner-signed, time-boxed grant bundles that keep the fleet working while the own | capability | M | bu-s11n0s.10 |
| 11 | Say a name, get the person: deterministic mention anchoring in memory context and recall, with  | capability | M | bu-s11n0s.11 |
| 12 | Ask promotion: a question the owner keeps asking becomes an offered standing push, and an unrea | capability | M | bu-s11n0s.12 |
| 13 | Who covers what: a duty-of-care ledger that names a carer for every occurrence and flags the on | capability | M | bu-s11n0s.13 |
| 14 | Auto-reply and bounce perception: an out-of-office reply becomes 'away until', a hard bounce be | capability | M | bu-s11n0s.14 |
| 15 | Insight outcome ledger: premise-tracked nudges with natural holdouts, per-class efficacy verdic | capability | M | bu-s11n0s.15 |

Capability share: 8/15 (53%), below the 60% aim. Seven trust defects were found and none was demoted below a feature; the three smallest run-15 residues were bundled into rank 3.

## North star

See the [pursuit ledger](pursuit-ledger.md#north-star); unchanged.

## What landed since run 15 (scoped QC)

Three agents traced the thirteen run-15 moves the fleet shipped (#4344 to #4356, with #4371, #4375 and #4377), producer through every consumer, in source. 8 are confirmed as designed; the five partial verdicts are below. Open follow-up beads bu-q7vx1q.17 to .81 were treated as known residuals and not re-reported. None of this is deployment evidence.

| Slice | Verdict | Confidence |
|---|---|---|
| #4346 RSVP- and transparency-honest conflict radar (bu-q7vx1q.3) | partial | source-confirmed |
| #4352 time-true trend grammar and surface honesty | partial | source-confirmed |
| #4349 owner-asserted person posture | partial | source-confirmed |
| #4353 Account-security event sensor | partial | source-confirmed |
| #4351 Provider allowance windows | partial | source-confirmed |

The untracked residues: the meeting context producer ignores RSVP and transparency (rank 4); the birthday push ignores posture, filtered-message previews keep one-time codes, and `allowance_account` has no writer (rank 3); the account-security sensor's 'was this you?' prompt was never delivered, and its bead closed with no follow-up (rank 1). Evidence: `jq '.audits[] | select(.page|startswith("qc-run15")) | .qc_verdicts'`.

## Tier board and movement

No surface moved for the third run in a row. All ten surfaces and both sweeps were walked against the design bar with the minimum-depth rule; each recorded the capability candidates it rejected. Line and area charts improved under #4352, but not enough to move the visual-language tier. These are heuristic source judgments; no surface is claimed world-class and nothing was observed live.

| Surface | Run 15 | Run 16 | Movement |
|---|---|---|---|
| surface-command | solid | solid | unchanged |
| surface-chat | functional | functional | unchanged |
| surface-activity | solid | solid | unchanged |
| surface-health | functional | functional | unchanged |
| surface-education | broken | broken | unchanged |
| surface-life-graph | weak | weak | unchanged |
| surface-calendar | functional | functional | unchanged |
| surface-ops | functional | functional | unchanged |
| surface-settings | functional | functional | unchanged |
| surface-spend | weak | weak | unchanged |
| cross-shell | functional | functional | unchanged |
| cross-visual | functional | functional | unchanged |
| cross-speed | functional | functional | unchanged |
| cross-accessibility | functional | functional | unchanged |

Per-route tiers for the ops surface are in `jq '.audits[] | select(.page=="surface-ops") | .tiers'`.

## Systemic themes

### Whoever holds a channel holds the owner

A lost phone keeps Telegram approval authority, a hijacked mailbox writes owner-class memory, any session can mint 'owner-verified' on a person's phone number, and a bill can redirect payment to a new account with nothing comparing it to where the payee was paid before. Ranks 1, 2 and 9 separate holding a channel from being the owner: a custody hold that is cheap to place and expensive to lift, server-derived authority on the canonical graph, and a joint Finance, Relationship and Switchboard check on payment destinations.

Evidence: `src/butlers/connectors/telegram_bot.py:1034-1054`; `src/butlers/modules/memory/content_authority.py:86-89`; `roster/relationship/modules/tools.py:1138`.

### A fix reaches most readers, not all

Run 15 moved shared predicates into most of their readers and left one sibling behind each time: the context producer still counts declined meetings, the birthday push still names the dead, the drop buffer still stores one-time codes. Ranks 3 and 4 close them; failure-taxonomy shape 16 ('sibling reader left behind') now asks every packet for a reader census.

Evidence: `src/butlers/jobs/context_producers.py:156`; `roster/relationship/tools/dates.py`; `src/butlers/connectors/filtered_event_buffer.py:296-345`.

### The world states facts the fleet throws away

Google Calendar says the owner is out of office, focused or working from the office; contacts' mail servers say they are on leave or that an address is dead; Telegram contacts publish their birthdays; the phone knows tomorrow's alarm. The fleet discards all of it and then guesses. Ranks 4 and 14 read the declared state deterministically, at the connector, with no model.

Evidence: `src/butlers/modules/calendar.py:891-962`; `src/butlers/connectors/gmail_policy.py`; `src/butlers/modules/contacts/telegram_provider.py:146-200`.

### Instruments that cannot say 'I wasn't looking'

A connector strip draws an hour with no heartbeat as a calm zero, a session shows the model it asked for as the model that served, and location points outlive the retention the security doctrine promises. Ranks 5, 6 and 7 make each instrument state what it observed, what served, and what it has forgotten. Failure-taxonomy shape 15 records the zero-fill pattern.

Evidence: `src/butlers/api/routers/ingestion_connectors.py:173-192,548-575`; `src/butlers/core/runtimes/claude_code.py:136-154`; `about/heart-and-soul/security.md:259-261`.

### Life beyond the owner has no substrate

The five rotating lenses produced 19 of the 58 candidates, and they converge on people and circumstances the system cannot represent: dependents and the carers who cover them, guests' dietary constraints, a disruption week, the owner's own stated aims. Rank 13 starts with the duty-of-care ledger; intentions and the disruption window are retained as deep designs.

### Method: the generative value sits in the ecosystem lenses

Ecosystem, rotating and deep-design agents produced 38 of 58 candidates; the eight UX agents produced 15, and no tier has moved in three runs. QC of the previous epic is now the highest-yield UX activity: it found four of the seven trust repairs.

## Ranked moves

Full packets (surface map, behavior matrix, verification, slice plan, novelty record) live in each bead's structured `design` and `acceptance_criteria` fields and in the source audit's move entry; the paragraphs below are the outcome only.

### 1. Endpoint custody holds: 'my phone is gone' or 'that sign-in wasn't me' strips that channel's owner authority fleet-wide in one write

**Bead:** bu-s11n0s.1 · **Kind:** trust · **Cost:** L · **Owner value:** H · **Source:** rot-crisis-disruption#0, eco-cross-butler#0, qc-run15-capability#0, surface-ops#1

One owner act from any surviving surface (dashboard, Telegram, email, or a 'no' answer to the now-delivered 'was this you?' prompt) records a custody hold on an owner endpoint. While held, the endpoint resolves to no owner (approvals, owner routing, email guard), its inbound content is stamped non-owner authority, Messenger refuses to send through a held account, owner notices fall back to the next unheld channel, and a fleet case collects the last known fix, Finance's activity since the hold began and any later security alerts. Release only from an owner-authenticated dashboard session or the host CLI.

### 2. Owner-verified means the owner verified it: server-derived authority on relationship.entity_facts, with third-party contact handles held as candidates

**Bead:** bu-s11n0s.2 · **Kind:** trust · **Cost:** M · **Owner value:** H · **Source:** eco-knowledge#0

relationship.entity_facts rows carry a server-stamped content authority and author entity; the MCP wrapper can no longer set verified; only owner-class authority, approval dispatch or the dashboard mark-verified route mint the badge; a third party's new phone, email or handle for a known person lands as a candidate excluded from recipient and inbound identity resolution until the owner adopts it.

### 3. Finish run 15's seams: a writer for allowance_account, posture on the birthday push, and the bearer scrub on filtered-message previews

**Bead:** bu-s11n0s.3 · **Kind:** trust · **Cost:** S · **Owner value:** M · **Source:** qc-run15-capability#1, qc-run15-speech-memory#0, qc-run15-trust#1

(a) Every catalog route records its provider allowance_account so two accounts on one runtime are distinct and an exhausted account no longer blocks a healthy one. (b) Relationship upcoming_dates and the daily birthday reminder exclude memorial, quiet and no_contact people. (c) FilteredEventBuffer.record scrubs subject_or_preview with the shared bearer detector, so a blocked one-time-code message never persists its code.

### 4. Calendar context truth: RSVP, transparency and the event type the owner declared decide whether they are in a meeting, away, focused or working somewhere

**Bead:** bu-s11n0s.4 · **Kind:** trust · **Cost:** S · **Owner value:** M · **Source:** qc-run15-trust#0, eco-connectors#1

The calendar context producer counts only events that count toward owner load (accepted, opaque); a Google out-of-office event asserts 'away' and never 'meeting'; focus time asserts 'focused' structurally; a working-location event names where the owner works today as a context signal; the broker stops producing meeting-prep nudges for out-of-office blocks.

### 5. Count strips tell deaf from quiet: time-keyed buckets with per-bucket listening state, and no fabricated liveness zeros

**Bead:** bu-s11n0s.5 · **Kind:** trust · **Cost:** M · **Owner value:** H · **Source:** cross-visual-a11y#0

Connector and activity strips place each bucket at its real time with relative labels; a bucket where the endpoint sent no heartbeat renders as 'not listening', distinct in shape from a measured zero; buckets beyond heartbeat retention render 'liveness unknown'; the stats contract drops its default-0 liveness fields; a lint rule bans positional zero-filled chart derivations.

### 6. Served-identity attestation: every attempt records what actually served it, and requested-not-served becomes evidence

**Bead:** bu-s11n0s.6 · **Kind:** trust · **Cost:** S · **Owner value:** M · **Source:** eco-inference#1

Each dispatch attempt persists a bounded served-identity record (served model ids, CLI version, provider-reported cost, error subtype) parsed from the adapter output; the session dossier shows 'served' beside the resolution receipt; spend rows carry per-served-model usage; a requested model that did not serve is recorded and visible, never shown as if it ran.

### 7. Keep the location retention promise: raw fixes expire on a declared horizon once their places and trips are projected

**Bead:** bu-s11n0s.7 · **Kind:** trust · **Cost:** M · **Owner value:** M · **Source:** rot-place-movement#3

Raw OwnTracks points and location point events older than the declared horizon (default 30 days, owner-editable shorter) are purged only after their place episodes and movement episodes are projected; summaries survive at reduced precision with a forgotten-rows receipt; the Chronicles map says when the trail expires.

### 8. Break-through list: the owner declares who and what may reach them in quiet hours, checked on every inbound channel and delivered within minutes

**Bead:** bu-s11n0s.8 · **Kind:** capability · **Cost:** M · **Owner value:** H · **Source:** eco-proactivity#1

Owner-only clauses (a person, a sender domain, a channel, a security kind; always or quiet-hours-only; expiring) are evaluated deterministically at ingest for every channel; a hit emits a metadata-only break_through_hit event and an urgent owner notice within minutes that bypasses quiet hours, and is withdrawn if the owner has already handled the message.

### 9. Payment-redirect guard: a request to pay a known counterparty somewhere new is checked jointly by Finance, Relationship and Switchboard

**Bead:** bu-s11n0s.9 · **Kind:** capability · **Cost:** M · **Owner value:** H · **Source:** eco-cross-butler#1

Finance fingerprints payment destinations per counterparty from bills and invoices; a bill naming a new destination for a known payee gets a verdict (known, new_for_counterparty, unverifiable) combined with Switchboard's sender-authentication verdict and Relationship's verified channels; a redirect reaches the owner with a door to call back on a channel the fleet already trusts, before any payment.

### 10. Absence Plans: owner-signed, time-boxed grant bundles that keep the fleet working while the owner is away, with atomic use claims and one-row revoke

**Bead:** bu-s11n0s.10 · **Kind:** capability · **Cost:** M · **Owner value:** H · **Source:** deep-delegate#0

The owner signs a plan for a window (from a trip or by hand) granting named butlers bounded tool uses; the approval gate claims a use atomically and auto-approves with decided_by naming the plan grant; out-of-window or exhausted claims park; a hold-until-return posture keeps parked items alive to the window end; one revoke stops every grant; a close receipt lists applied, refused and waiting items. Slice 1 also fixes rule max_uses races and rule placement.

### 11. Say a name, get the person: deterministic mention anchoring in memory context and recall, with honest ambiguity and known unknowns

**Bead:** bu-s11n0s.11 · **Kind:** capability · **Cost:** M · **Owner value:** H · **Source:** deep-remember#0

Before a session runs, names and aliases in the trigger text are matched deterministically against public.entities; anchored people's facts are injected with references and dates, ambiguous names produce 'which Chloe?' rather than a guess, posture is respected, and open knowledge gaps appear as 'still unknown, asked on <date>'.

### 12. Ask promotion: a question the owner keeps asking becomes an offered standing push, and an unread push offers to retire

**Bead:** bu-s11n0s.12 · **Kind:** capability · **Cost:** M · **Owner value:** H · **Source:** eco-proactivity#0

A daily deterministic scan clusters recurring owner questions by target butler and weekday and hour (at least three distinct weeks); the owner is offered a scheduled push at that time through the improvement-proposal path, with the target butler owning the schedule; a re-ask after a push counts as a miss, and a push that goes unread offers its own retirement.

### 13. Who covers what: a duty-of-care ledger that names a carer for every occurrence and flags the ones nobody covers

**Bead:** bu-s11n0s.13 · **Kind:** capability · **Cost:** M · **Owner value:** H · **Source:** rot-household-dependents#0

The owner declares duties for dependents (school pickups, medication, pet care) as calendar-anchored or completion-anchored; each occurrence carries an assumed or confirmed carer; a deterministic job crosses occurrences with the owner's busy time and trips and flags uncovered ones ahead of time in the weekly view and as one attention item.

### 14. Auto-reply and bounce perception: an out-of-office reply becomes 'away until', a hard bounce becomes a dead address, with no LLM involved

**Bead:** bu-s11n0s.14 · **Kind:** capability · **Cost:** M · **Owner value:** H · **Source:** eco-connectors#0

Gmail classifies auto-replies and delivery-status reports deterministically from headers and MIME report types; the connector emits a metadata-only typed event; Relationship records the contact's availability interval or marks the address dead; nudges and drafts respect it, and auto-reply text never reaches a model as instructions or costs a session.

### 15. Insight outcome ledger: premise-tracked nudges with natural holdouts, per-class efficacy verdicts, and shaping that follows whether the world changed

**Bead:** bu-s11n0s.15 · **Kind:** capability · **Cost:** M · **Owner value:** H · **Source:** deep-close-loop#0

Every premised insight candidate is enrolled with its exposure (delivered, held by budget, held by context, shadowed); a daily sweep records whether the premise resolved and how fast; per-class verdicts (effective, self_resolving, ignored, unmeasured) with at least five per arm override the click proxy under an opt-in shaping mode; self-resolving classes fold into the digest and the owner is told once, with counts.

## Design addenda (deep designs retained, not ranked)

- **Intentions: an owner aim held as a versioned object bound to a signal the fleet already perceives, judged per period as kept, behind or unmeasured, with an explicit 'let it go'** (rot-goals-habits#0, cost L, value H). vision.md:145 measures success by mental labor absorbed, and remembering and self-auditing an aim is exactly that labor. The owner says it once and stops tracking it, and the fleet never invents progress. Fixes the inert goal predicate (002_seed_predicates.py:45-48). Full design: `jq '.audits[] | select(.page=="rot-goals-habits") | .moves[0]'`.
- **Disruption window: 'I'm out of action until Friday' opens a case that holds the noise, computes the fallout across specialists, drafts every counterpart notice, and closes only when each item has a disposition** (rot-crisis-disruption#1, cost L, value H). vision.md:145-146 measures success by mental labor absorbed. A disruption (hospital, illness, bereavement, stranded travel) is the moment that labor peaks. Today the `sick` signal has no deterministic consumer (attention_ledger.py:537) and nothing lists who will be let down. Full design: `jq '.audits[] | select(.page=="rot-crisis-disruption") | .moves[1]'`.
- **Source recall: 'what do you know because of X?' and one verb to hold or retract everything a source taught the fleet** (eco-knowledge#1, cost M, value H). Vision success measure is mental labor absorbed. Today, when a contact's account is hijacked, a newsletter proves wrong, or the owner's own mailbox is compromised, the owner cannot even list what the fleet learned from that source (finding 6). Full design: `jq '.audits[] | select(.page=="eco-knowledge") | .moves[1]'`.

## Vetted candidates not ranked

Each carries a novelty record in the data file; none is a duplicate of prior work. They are listed so nothing vanishes silently.

The phone's next alarm as declared wake intent (eco-connectors, S); Birthdays contacts declared themselves on Telegram, with (eco-connectors, S); Provider weather (eco-inference, M); Who has been shown what (eco-inference, M); Challenged, not overwritten (eco-knowledge, S); Measured shelf life (eco-knowledge, M); Owner welfare ladder (eco-cross-butler, L); Eaten-out sense (food-nutrition, M); The table card (food-nutrition, M); Itemized baskets (food-nutrition, L); Recall watch (food-nutrition, M); Calendar-honest dinner plan (food-nutrition, L); Done, and by whom (household-dependents, M); Need-to-know grants and a handover brief (household-dependents, M); Leg ledger (place-movement, M); Venue binding and running-late door (place-movement, M); Vehicle as a sensed asset (place-movement, M); Let it go with reach (goals-habits, M); Hear what the owner means to do (goals-habits, S); Safety check-in (crisis-disruption, M); Emergency card (crisis-disruption, S); Witnessed citations (chat, M); Answer drift (chat, M); Run lineage (command-activity, M); Misconception ledger (health-education, M); Symptom 'what preceded this' (health-education, M); Counterparty-aware scheduling (life-calendar, M); Subscription shadow price (settings-spend, M); Routing resilience matrix (settings-spend, M); Expiring holds (ops, M); The pane knows which build it is (cross-shell-speed, M); One input (cross-shell-speed, S); Measured pace (cross-shell-speed, M); One arrival contract (cross-visual-a11y, S)

## Method and limitations

- 24 agents in 12 batches of 2, never more than 3 in flight; about 2.5M subagent tokens in total. The owner moved the cadence from 60 to 55 minutes mid-run to stay inside the prompt-cache window.
- Source-only: no live stack, no secrets, no bead writes, no git mutations by any auditor. Source-connected is not deployed.
- Dedup used a gitignored ledger of 1,684 prior proposal titles plus 408 non-closed beads; agents recorded their searches in each move's `novelty` field, and the synthesizer dropped nothing for novelty reasons.
- Ranks 10 and 15 each name a placeholder core migration number from their source design; implementers take the next free revision.
- Access pattern: `jq '.audits[] | select(.page=="<key>")' docs/redesigns/2026-10-06-jarvis-pursuit-data.json`; keys are the batch-plan labels in `orchestration.batch_plan`. `jq '.synthesis.ranked_moves[] | {rank, title, bead}'` lists the moves.
- Retention: this PR removes `2026-09-22-jarvis-pursuit-data.json` from the tree per the two-newest-runs rule; read it with `git show 091af61db399b48b78809486dbab43e60bb6d122:docs/redesigns/2026-09-22-jarvis-pursuit-data.json`.
