# JARVIS pursuit: run 15 (2026-10-03)

**Reader:** owner deciding what new work to release. **Status:** proposed planning record; not adopted requirements or deployment evidence. Audited at baseline `32f38feb5`, with main advancing to `252b77c76` during the run (Asia/Singapore, 02:30 to 13:30).

Fifteen moves survive deduplication against fourteen prior dossiers and 330 non-closed beads: six trust repairs lead, nine new capabilities follow. All four rotating lenses were new to the pursuit and all four produced ranked work.

Full per-agent structured output lived in `2026-10-03-jarvis-pursuit-data.json`, pruned from the tree under the two-newest-runs retention rule; read it with `git show 9cc43f3e5443dd812354ff6ee7112d9575205288:docs/redesigns/2026-10-03-jarvis-pursuit-data.json` (pipe into jq in place of the path in the access patterns below).

## Decision in view

Epic bu-q7vx1q is held by owner gate bu-y8qlcm. Closing that gate releases the sixteen children (fifteen moves plus one terminal reconciliation). It does not adopt specs, deploy anything, provision credentials or authorize external actions; the owner may instead close only chosen children. Every child carries a full Dispatch Readiness Packet in its structured fields (`bd lint`: zero warnings on 18 beads).

| Rank | Move | Kind | Cost | Bead |
|---|---|---|---|---|
| 1 | Guidance only from the owner: server-derived content authority on episodes, inherited through c | trust | L | bu-q7vx1q.1 |
| 2 | Bearer-material quarantine at the ingest boundary: keep that a code arrived, never the code | trust | M | bu-q7vx1q.2 |
| 3 | RSVP-honest calendar: owner response status and transparency drive day load and conflict radar, | trust | M | bu-q7vx1q.3 |
| 4 | Dropped-from-someone-you-know sentinel: known-contact drops by block or skip rules become an ow | trust | M | bu-q7vx1q.4 |
| 5 | Premise-bound proactive speech: every insight names the fact it asserts, is rechecked at send,  | trust | M | bu-q7vx1q.5 |
| 6 | Surface honesty batch: time-true trend grammar plus four small repairs where a rendered clause  | trust | M | bu-q7vx1q.6 |
| 7 | Use the consent already granted: Google Health workouts, then body measurements, as first-party | capability | M | bu-q7vx1q.7 |
| 8 | Person posture: say once that someone has died, is estranged or must not be contacted, and ever | capability | M | bu-q7vx1q.8 |
| 9 | Ask once: an unanswered owner question becomes an open knowledge gap that answers itself, with  | capability | M | bu-q7vx1q.9 |
| 10 | Account-security event sensor over first-party provider mail, with a 'was this you?' door | capability | M | bu-q7vx1q.10 |
| 11 | Ambient glance: scoped projection grants with consumer liveness, and one deterministic expiring | capability | M | bu-q7vx1q.11 |
| 12 | Meeting debrief: every ended meeting with other people closes with 'anything agreed?', and the  | capability | M | bu-q7vx1q.12 |
| 13 | Provider allowance windows: account-scoped exhaustion with a reset horizon, account-aware failo | capability | M | bu-q7vx1q.13 |
| 14 | Action mandates: a butler asks a sibling to act, the sibling keeps its authority, and the outco | capability | L | bu-q7vx1q.14 |
| 15 | Personal baselines: a per-schema robust band with honest coverage, deviation episodes that excl | capability | L | bu-q7vx1q.15 |

Capability share: 9/15 (60%). Trust defects were not demoted below features; the five smallest repairs were bundled into rank 6 so the capability share could meet the standing feature-first emphasis.

## North star

Five-second fleet verification with earned calm: nothing fabricated, failure never impersonates health, staleness never wears current-data authority, and every consequential clause is a door on an unbroken signal-to-session-to-evidence spine. Keyboard-first Dispatch; specialist ownership; deterministic infrastructure. The success measure is the amount of mental labor the system reliably absorbs for one owner.

## What landed since run 14 (scoped QC)

Two agents traced eighteen slices shipped after the run-14 baseline, producer through every consumer, in source. Fourteen are confirmed as designed (listed in the data file); the four partial slices below leave residues that are the small repairs in rank 6. None of this is deployment evidence.

| Slice | Verdict | Confidence |
|---|---|---|
| #4188 truthful medication supply quantity | partial | source-confirmed |
| #4261 linked owner-alert status in Standing Conditions | partial | source-confirmed |
| #4328+#4330 phone entry-route registry, 375px project, 44x44 | partial | source-confirmed |
| #4057+#4207 canonical entity activity stream from unioned so | partial | source-confirmed |

Evidence: `jq '.audits[] | select(.page=="qc-landed-seams" or .page=="qc-landed-surfaces") | .qc_verdicts'`.

## Tier board and movement

No surface moved. Three surfaces (command, activity, chat) were graded on the change log only, because their agents found almost no commits on their routes since the baseline and stopped there; the remaining seven surfaces and both sweeps were walked against the design bar with a minimum-depth rule and recorded the capability candidates they rejected. These are heuristic source judgments; no surface is claimed world-class and nothing was observed live.

| Surface | Run 14 | Run 15 | Movement / basis |
|---|---|---|---|
| surface-command | solid | solid | unchanged; change-log basis only (no design-bar walk) |
| surface-chat | functional | functional | unchanged; change-log basis only (no design-bar walk) |
| surface-activity | solid | solid | unchanged; change-log basis only (no design-bar walk) |
| surface-health | functional | functional | unchanged; unchanged vs run 14 |
| surface-education | broken | broken | unchanged; unchanged |
| surface-life-graph | weak | weak | unchanged; unchanged vs run 14 |
| surface-calendar | functional | functional | unchanged; unchanged |
| surface-ops | functional | functional | unchanged; unchanged vs run 14 (whole-surface verdict; per-route tiers  |
| surface-settings | functional | functional | unchanged; unchanged vs run 14; commits since 42c2c9817 touching these  |
| surface-spend | weak | weak | unchanged; unchanged; SpendPage |
| cross-shell | functional | functional | unchanged; Unchanged since run 14 |
| cross-visual | functional | functional | unchanged; flat vs run14: Dispatch consolidation (#4231) landed, but ch |
| cross-speed | functional | functional | unchanged; Unchanged since run 14 |
| cross-accessibility | functional | functional | unchanged; flat vs run14: state hues are not CVD-separable (light deuta |

Per-route tiers for the ops surface are in `synthesis.tier_board`.

## Systemic themes

### Authority leaks: non-owner content and stale premises wear owner-grade authority

A vendor email can become a candidate rule with the same standing as the owner's own words; a delivered nudge keeps asserting a bill is due after it is paid; promoted skip rules can silence a provider's own security alert. Ranks 1, 5 and 10 close the three leaks with deterministic gates, never an LLM judging trust.

Evidence: `src/butlers/modules/memory/tools/context.py`; `roster/switchboard/tools/insight/broker.py:739-760`; `src/butlers/ingestion_policy.py` (promotion path).

### Known people are invisible to the gates

A block rule drops a known contact while the opener says all gates clear; a declined invitation still counts as a conflict; a friend who has died still earns a birthday reminder and a gift budget. Ranks 3, 4 and 8 make the relationship graph an input to the gates that currently ignore it.

Evidence: `src/butlers/connectors/gmail.py:2201-2262`; `src/butlers/core/temporal/conflicts.py:161`; `roster/switchboard/tools/briefing.py:584-700`.

### Consent and senses already held are unused

Google Health consent covers workouts the connector never polls; provider security mail arrives and is cut to a subject line; the owner's own machines on the tailnet are invisible. Ranks 7 and 10 and the Steward addendum harden senses the fleet already has, which RFC 0018 prefers over new connectors.

Evidence: `src/butlers/connectors/google_health.py:316-389`; `openspec/specs/connector-google-health/spec.md:262-292`; `src/butlers/chronicler/adapters/exercise.py:7-12`.

### The fleet can ask, but cannot act for a sibling or follow through for the owner

Cross-butler delegation is answer-only; an "I don't know" is terminal; meetings end without a debrief; "unusual for you" has no shared statistics. Ranks 9, 12, 14 and 15 give the fleet verbs with durable outcomes, each returning through the Switchboard and each with a terminal state that cannot be invented.

Evidence: `src/butlers/core/domain_event_contracts.py:65-70`; `roster/switchboard/tools/_switchboard.py:205-210`; `roster/health/jobs/health_jobs.py:875-965`.

### Method: the UX half has converged

Run 14 found one move across ten surfaces; run 15 found seven across the same surfaces only after forcing a minimum-depth walk, and none moved a tier. The generative value now sits almost entirely in the ecosystem lenses, which produced 40 of the 53 candidates.

## Ranked moves

Full packets (surface map, behavior matrix, verification, slice plan, novelty record) live in each bead's structured `design` and `acceptance_criteria` fields and in the source audit's move entry; the paragraphs below are the outcome only.

### 1. Guidance only from the owner: server-derived content authority on episodes, inherited through consolidation and gating rules and profile facts

**Bead:** bu-q7vx1q.1 · **Kind:** trust · **Cost:** L · **Owner value:** H · **Source:** eco-knowledge#0

Every memory episode, fact and rule carries a server-stamped content authority derived from the Switchboard routing context. A rule or profile fact whose evidence is not owner-authored is born held, absent from Active Rules, Profile Facts and the discovery catalog, and listed as 'Guidance waiting for you' until the owner endorses it.

### 2. Bearer-material quarantine at the ingest boundary: keep that a code arrived, never the code

**Bead:** bu-q7vx1q.2 · **Kind:** trust · **Cost:** M · **Owner value:** H · **Source:** rot-digital-stewardship#0

One-time codes, reset links, magic links and Telegram login codes never persist in message_inbox.raw_payload or normalized_text; a typed placeholder records that an auth artifact arrived, from which provider, when, and the message still routes.

### 3. RSVP-honest calendar: owner response status and transparency drive day load and conflict radar, plus an invitation inbox with respond verbs

**Bead:** bu-q7vx1q.3 · **Kind:** trust · **Cost:** M · **Owner value:** H · **Source:** surface-life-calendar#0

Declined and transparent (free) events no longer count toward overloaded-day hours or conflict radar; unanswered invitations list with organizer and linked conflict; the owner can accept, decline or tentatively respond from the dashboard or palette with a receipt and undo.

### 4. Dropped-from-someone-you-know sentinel: known-contact drops by block or skip rules become an owner case with a replay door

**Bead:** bu-q7vx1q.4 · **Kind:** trust · **Cost:** M · **Owner value:** H · **Source:** surface-ops#0

When a block or skip rule drops a message from a known contact, the drop is marked important, the Filters opener reads 'N dropped from people you know' with a door to the narrowed filtered list and a replay verb, and 'All gates clear' is never rendered while that aggregate is unavailable or non-zero.

### 5. Premise-bound proactive speech: every insight names the fact it asserts, is rechecked at send, and is quietly amended in place when that fact stops being true

**Bead:** bu-q7vx1q.5 · **Kind:** trust · **Cost:** M · **Owner value:** H · **Source:** eco-proactivity#0

An insight candidate carries a typed premise; the broker re-validates it before selection (withdrawn, never sent, if false), persists the delivery reference, and amends the delivered message in place (Telegram edit) or folds a 'since last digest' line when the premise later resolves. Unknown probe state delivers stamped 'as of' and never claims a recheck.

### 6. Surface honesty batch: time-true trend grammar plus four small repairs where a rendered clause is not yet true

**Bead:** bu-q7vx1q.6 · **Kind:** trust · **Cost:** M · **Owner value:** H · **Source:** cross-visual-a11y#1, qc-landed-surfaces#0, qc-landed-surfaces#1, qc-landed-seams#0

(a) Every trend chart uses a shared TimeSeriesChart with time-proportional x axes ending at now, visible observations, no smoothing and gap breaks, enforced by lint. (b) A paging-class fleet or QA condition with no owner-alert episode renders 'not raised' and degrades the Standing Conditions tile. (c) The entity activity stream declares its 500-episode truncation and rows link to the chronicle day and fact detail. (d) Medication supply renders its recorded date and the default Audit view includes schedule.* mutations. (e) The QA escalations badge carries an availability state so a fetch failure is not a calm zero.

### 7. Use the consent already granted: Google Health workouts, then body measurements, as first-party evidence

**Bead:** bu-q7vx1q.7 · **Kind:** capability · **Cost:** M · **Owner value:** H · **Source:** eco-connectors#0

Recorded workouts from the already-consented Google Health account become workout_session facts and Chronicler workout episodes with provider ids; the exercise-inference suppression guard becomes live; in slice 2 device weight and body fat enter health measurements with source provenance and owner-dominance on conflict.

### 8. Person posture: say once that someone has died, is estranged or must not be contacted, and every butler respects it

**Bead:** bu-q7vx1q.8 · **Kind:** capability · **Cost:** M · **Owner value:** H · **Source:** eco-cross-butler#0

public.entities gains an owner-asserted posture (active, memorial, quiet, no_contact); briefing birthdays and gift asks, calendar birthday overlays, reconnection nudges, notify() egress and Finance loan claims all respect it, failing closed for memorial and no_contact when the read fails.

### 9. Ask once: an unanswered owner question becomes an open knowledge gap that answers itself, with a citation, when the fact is written

**Bead:** bu-q7vx1q.9 · **Kind:** capability · **Cost:** M · **Owner value:** H · **Source:** eco-knowledge#1

A sourceless decline in the answer lane records a typed knowledge gap (entity, predicate, origin thread); a later matching fact write marks it answerable in the same transaction and a deterministic job delivers exactly one notice with the memory reference into the origin thread; the owner can list what is still unknown.

### 10. Account-security event sensor over first-party provider mail, with a 'was this you?' door

**Bead:** bu-q7vx1q.10 · **Kind:** capability · **Cost:** M · **Owner value:** H · **Source:** rot-digital-stewardship#1

A deterministic pre-policy classifier turns provider security mail (new sign-in, password or MFA or recovery changed, deletion scheduled) into typed estate.security_event bus events that promoted rules cannot suppress; the owner gets one confirm prompt per event and 'no' opens a fleet case with the provider recovery door.

### 11. Ambient glance: scoped projection grants with consumer liveness, and one deterministic expiring 'one thing' payload

**Bead:** bu-q7vx1q.11 · **Kind:** capability · **Cost:** M · **Owner value:** H · **Source:** rot-ambient-presence#0, rot-ambient-presence#1

The owner mints and revokes per-surface read grants (household or owner-private audience) that unlock only their route scope; subscribe.ics moves under them; a missed expected fetch opens one attention clause. A glance endpoint returns a deterministic, audience-redacted state class, one line, needs-you count, top door, as_of and valid_until, as JSON, text or 1-bit PNG, with parity to the briefing classifier and no LLM call.

### 12. Meeting debrief: every ended meeting with other people closes with 'anything agreed?', and the answer becomes evidence-linked commitments

**Bead:** bu-q7vx1q.12 · **Kind:** capability · **Cost:** M · **Owner value:** H · **Source:** rot-work-occupation#1

A zero-LLM job selects ended calendar events with resolved non-owner attendees, records one debrief per occurrence, and batches one end-of-day prompt; the owner's reply through a pinned-target conversation creates commitments whose evidence names the meeting and counterparty, with a work/personal sphere on commitment metadata and disengagement back-off.

### 13. Provider allowance windows: account-scoped exhaustion with a reset horizon, account-aware failover, and background work deferred to the reset

**Bead:** bu-q7vx1q.13 · **Kind:** capability · **Cost:** M · **Owner value:** H · **Source:** eco-inference#0

A usage-limit rejection marks the provider account exhausted with a parsed or default reset horizon; routing excludes every catalog entry on that account until reset; owner turns fail over to another account within one attempt; schedule and deadline dispatches with no fit account defer to the reset with a skipped_allowance outcome; the Models tab and System verdict show the countdown with a door to the attempt row, and unknown is never rendered as available.

### 14. Action mandates: a butler asks a sibling to act, the sibling keeps its authority, and the outcome comes back

**Bead:** bu-q7vx1q.14 · **Kind:** capability · **Cost:** L · **Owner value:** H · **Source:** deep-do#0

A requesting butler files a typed mandate against an intent the target declared in roster/{target}/mandates.toml; Switchboard verifies and routes it; the target's session accepts or declines and acts through its own gated tools; approvals carry origin='mandated' with provenance; the terminal outcome is written once and returns to the requester; pilot intent home.away_window requested by travel.

### 15. Personal baselines: a per-schema robust band with honest coverage, deviation episodes that exclude their own days, and one scorer behind every 'unusual for you'

**Bead:** bu-q7vx1q.15 · **Kind:** capability · **Cost:** L · **Owner value:** H · **Source:** deep-say#0

A pure core statistics module computes per-metric MAD bands with coverage and measurability; per-butler daily baseline_watch jobs open and close deviation episodes that propose insight candidates with baseline evidence and reconcile owner conditions; health resting HR, HRV and sleep duration ship first, then home energy and finance spend replace their prose and 2x-average baselines.

## Design addenda (deep designs retained, not ranked)

- **Steward: perceive the owner's tailnet machines and self-hosted services, hold equipment conditions with lead time, and attribute silent senses to their upstream host** (deep-perceive#0, cost L, value H). vision.md:11-13 (you own the instance, on your own infrastructure) and vision.md:135 (success is mental labor absorbed). Today the owner has to watch Tailscale key expiry, host liveness and self-hosted service uptime himself, and he finds out only after a sense has gone quiet. Full design: `jq '.audits[] | select(.page=="deep-perceive") | .moves[0]'`.
- **Source receipts: a governed research lane where every external source is a hash-pinned, retrievable receipt** (rot-deliberation-research#0, cost M, value H). North star: 'nothing fabricated; every clause a door'. Today a recalled URL wears citation authority (research.py:80, ResearchTracker.tsx:139, citations.py:229). Vision rule 1 (sovereignty): owner-identifying queries currently leave the instance with no record. Full design: `jq '.audits[] | select(.page=="rot-deliberation-research") | .moves[0]'`.
- **Research briefs: a commissioned, resumable inquiry with a claim ledger and an honest conclusion** (rot-deliberation-research#1, cost L, value H). Vision: the success measure is mental labor absorbed. 'Look into X and tell me by Friday' is labor the owner currently carries in their head across chat turns. Full design: `jq '.audits[] | select(.page=="rot-deliberation-research") | .moves[1]'`.

## Vetted candidates not ranked

Each carries a novelty record in the data file; none is a duplicate of prior work. They are listed so nothing vanishes silently.

Owner turns on one chat are sequenced (eco-inference, M); Deliberation as a dispatch axis (eco-inference, M); A sovereign gazetteer (eco-connectors, M); A jurisdiction-true civic calendar (eco-connectors, S); Give the discovery catalog a real compaction own (eco-knowledge, S); Owner experiments (eco-cross-butler, L); Care circle (eco-cross-butler, L); Outcome-evidenced attention shaping (eco-proactivity, M); The fleet keeps its word (eco-proactivity, M); Content-blind digital estate registry (digital-stewardship, L); Deterministic domain and certificate expiry prob (digital-stewardship, S); Trials and payment-method ripple (digital-stewardship, S); Employer custody class (work-occupation, M); Vocation butler (work-occupation, L); Employment record with dated terms (work-occupation, M); Decision premises become watched tripwires; expe (deliberation-research, M); Option sheets (deliberation-research, M); Personal trials (deliberation-research, M); Honest calendar projections (ambient-presence, M); Living status line (ambient-presence, S); Commute briefing as a private, expiring podcast  (ambient-presence, L); Regimen event ledger and bounded before/after ef (health-education, M); Curriculum readiness date and an honest on-track (health-education, M); Spend by outcome (settings-spend, M); Rule backtest (settings-spend, M); Change-effect markers (settings-spend, M); Rule consequence ledger (ops, M); Durable decision intent (cross-shell-speed, M); Since your last look (cross-shell-speed, M); Ambient tab attention (cross-shell-speed, S); State that survives any eye (cross-visual-a11y, M)

## Method and limitations

- 22 agents, 10 hourly batches, at most 3 in flight; about 2.4M subagent tokens in total. Batches 2 onward carried a binding frugality rule and used roughly 40 to 60 percent fewer tokens per agent than batch 1.
- Source-only: no live stack, no secrets, no bead writes, no git mutations by any auditor. Source-connected is not deployed.
- Dedup used a gitignored ledger of 1,823 prior proposal titles plus 330 non-closed beads; agents recorded their searches in each move's `novelty` field, and the synthesizer dropped nothing for novelty reasons.
- Access pattern: `jq '.audits[] | select(.page=="<key>")' docs/redesigns/2026-10-03-jarvis-pursuit-data.json`; keys are the batch-plan labels in `orchestration.batch_plan`. `jq '.synthesis.ranked_moves[] | {rank, title, bead}'` lists the moves.
- Retention: this PR removes `2026-09-12-jarvis-pursuit-data.json` from the tree per the two-newest-runs rule; read it with `git show 89d7716c8fbdef9901d4167f320d6393afafe787:docs/redesigns/2026-09-12-jarvis-pursuit-data.json`.
