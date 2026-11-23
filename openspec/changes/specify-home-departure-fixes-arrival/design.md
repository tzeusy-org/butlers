## Context

Baseline: main `a38cf00fac`, including the timed insight lane and the read-only departure card
(#5789, #5790).

What exists and is reused unchanged:
- RFC 0011 Amendment 8, the timed lane: delivered between `deliver_after` and `expires_at` or not at
  all; `dnd` and `sleeping` always hold; quiet hours yield only to a confirmed presence edge under 15
  minutes old; a premise probe is re-checked before sending; one candidate per `dedup_key`.
- RFC 0028, Home physical actuation: a `(domain, service)` risk map (`safe`, `reversible`,
  `consequential`, `protected`; unknown is `protected`); consequential and protected calls park
  through the approvals module; the exact stored call replays under an authorization bound to the
  executor task, tool and argument digest; the receipt (`home.ha_command_log`) settles to exactly
  `succeeded`, `failed` or `unverified` from a live read-back; `unverified` is never success and its
  approval cannot be retried.
- bu-svqpzo.12: a reversible call runs without approval only for explicit entity targets with
  allowlisted data keys; anything else parks.
- The approvals module: `origin='prepared'` actions admitted durably and silently; pending to
  `approved|rejected|expired`; approved to `executed|abandoned` (abandon is dashboard-only). The
  Travel connection door withdraws a pending prepared action by `transition_pending_action` to
  `rejected` with a system actor and reason; this change reuses that path.
- The departure card producer, `home.departure_watch` and `home.departure_cards`, the presence
  `owner.place` slot and timeline (leave, enter, retraction, late), and
  `public.resolve_home_departure`.
- RFC 0038 D18: deterministic jobs run without a model. bu-dhloi1.3 (merged): a model cannot choose
  high priority; only authenticated deterministic safety producers, owner-authored break-through
  rules and owner-flagged obligations bypass quiet hours or sleep; unknown binding is routine.

Non-goals: any immediate lane (gate G1); any change to DND, sleep, budgets or the urgent
sub-cycle; any automatic actuation; a second presence inference; any new approval status.

## Outcome map

| Outcome | Owner of the data | Contract | Verification seam (existing file, extend) |
|---|---|---|---|
| Per-item parked fix | Home: `departure_fixes` declaration; approvals `pending_actions` row | REQ-008 | `tests/capability_broker/test_actuation_risk.py` (risk and exact-target), `tests/modules/approvals/test_prepared_actions.py` and `test_prepared_execution.py` (admission and replay), `tests/jobs/test_home_departure_card.py` |
| Cancellation, honest outcome | Home job; approvals transition | REQ-009 | `tests/modules/approvals/test_cancel_reason_code.py`, `tests/jobs/test_home_departure_card.py` |
| Arrival card | Home: `arrival_cards` evidence; timed lane | REQ-010 | `tests/jobs/test_home_departure_card.py` (timed-lane shape), real-Postgres claim test as the departure card has |
| Status resolver | Home read function; definer probe for the Switchboard | REQ-011 | new cases in the two prepared-action files above, plus the migration test for the definer function |
| Immediate-lane boundary | RFC 0011, bu-dhloi1.3 | REQ-012 | a contract test that no job in these slices sets an urgent priority |

## Parked fixes (REQ-008)

A fix is the owner's declaration, never a model's choice: one row per watched item
(`home.departure_fixes`), holding the exact `(domain, service)`, explicit entity target and allowed
data keys. The same entity may not have two fixes. It is written only through an owner-authenticated
route and refused if the call is not an explicit-entity call that RFC 0028 classifies, or if the
entity is not already in `home.departure_watch`. Only the declared call can ever be stored; the job
composes nothing.

When the departure card queues, the job parks one prepared action per fixable item it lists, through
the approvals module with `origin='prepared'`, dedup key `home:departure-fix:<edge id>:<entity>`, and
the exact stored kwargs. Risk comes from `effective_actuation_risk` at park time and is stored on the
action. A fix always waits for an owner approval: a `reversible` class that RFC 0028 would run
unprompted still parks here, because the card moment is not an owner request (decided D5). A
`protected` fix (locks, garage doors, anything unlisted) parks and its card line says so; the
approvals module's existing rule applies, so no standing rule ever covers it by default.

The card gains only a status word per fixable item (from the resolver, REQ-011) and a pointer to the
dashboard approval, not a button. A Telegram tap is not part of this change (gate G6).

No "secure all" action exists: each item is its own action with its own approval and receipt.

## Cancellation and honesty (REQ-009)

A run of the fix job, and every replay of an approved fix, re-reads the away premise
(`owner.place` slot and timeline, the same reader as the card). The premise is bound to the fix's
originating leave edge (the edge id in its dedup key): it holds only while that edge is still the
latest confirmed, unretracted `owner.place` edge. Reading "away" again after a return and a new
leave does not revive it, which matters because an unrun `approved` row can only move to `executed`
or `abandoned` (approvals module) and abandon is dashboard-only, so this binding is the only guard
on a stale approval. It then:
- rejects each still-`pending` fix with a system actor and a content-blind reason: `owner_returned`
  after a confirmed non-late enter, `premise_retracted` after a retracted leave, `already_secure`
  when the item reads secure, `watch_removed` when the declaration or watch row was removed;
- on replay of an `approved` fix whose premise has ended, refuses to dispatch (no Home Assistant
  request) and settles the action without a receipt claim of success;
- never edits a receipt. A fix that already settled `succeeded`, `failed` or `unverified` keeps that
  receipt. Arrival does not roll it back. Where RFC 0028 recorded a rollback hint, the arrival card
  may show it as text only.

Wording is derived from state: a cancelled fix reads "not run", an `unverified` one reads "unclear;
check the item", a `failed` one "did not run", and `succeeded` reads only the receipt's observed
post-condition. The text "undone", "reverted" or "restored" is never produced.

Approved-but-unrun fixes cannot be rejected (the state machine allows only dashboard abandon); the
replay-time premise check is what keeps arrival from racing an old approval.

## Arrival card (REQ-010)

A confirmed, non-late `owner.place` enter edge after a leave edge for which the departure card (or
any fix) existed queues one read-only timed candidate, dedup key `home:arrival:<edge id>`. Window:
`deliver_after` confirmed + 2 minutes, `expires_at` confirmed + 30 minutes, mirroring the departure
card so one lifecycle is reviewed once (decided D4). An absence shorter than the departure card's 30
minute window, or one with no card, fix or condition, queues no arrival card (decided D4). The timed lane's existing
presence-edge rule applies as adopted: quiet hours yield to a confirmed edge under 15 minutes old;
`dnd` and `sleeping` hold.

Content, all from stores Home already owns, newest first, capped, owner labels only:
- conditions that opened or resolved during the absence from the `home:*` sources (leak, smoke, CO,
  gas, and the away-safety sources once adopted), with their resolution reason;
- the departure card's items still not secure at arrival;
- each fix with its resolver status;
- stretches where the Home Assistant lease was unhealthy ("could not see from X to Y").

Nothing is inferred from Home Assistant history (it has no freshness record); an absence that had a departure card
says "Nothing changed that Home tracks" and states that Home only watches what the owner declared.
A premise probe `("home", "arrival_card_current")` answers false for a retracted or superseded
enter edge, so a card for an edge that was undone is withdrawn unsent.

## Status resolver (REQ-011)

`prepared_action_status(action_id)` reads Home's own `pending_actions` row and, when one exists, the
latest `home.ha_command_log` receipt for the approval, and returns one of a closed set:
`awaiting_approval`, `approved_not_run`, `succeeded`, `unverified`, `failed`, `rejected`, `cancelled`
(system rejection from REQ-009), `expired`, `abandoned`, `unknown`. It returns no arguments, no
message text and no target. Receipt wins over the approval row for the executed case; a row it cannot
classify, or a missing one, is `unknown`, which no card renders as success.

The Switchboard consults it only through a `SECURITY DEFINER` function with a pinned `search_path`,
executable by `butler_switchboard_rw` and not PUBLIC, mirroring `public.resolve_home_departure`
(core_335); it returns only the closed set. A card whose fix status changed since it was queued is
withdrawn unsent, as the departure card is for changed lines.

## Immediate lane boundary (REQ-012)

What adopted text supports, restated and nothing more:
- The urgent sub-cycle exists today (`priority >= URGENT_PRIORITY_THRESHOLD`, hourly) and skips quiet
  hours and the daily cap.
- A model invocation cannot choose that priority; only authenticated deterministic safety producers,
  owner-authored break-through rules and owner-flagged obligations bypass quiet hours or sleep
  (bu-dhloi1.3).
- The timed lane is not authorization for urgency (bu-jv1moa.8).

The departure fix, arrival card and status resolver therefore all use the timed lane, all at routine
priority. A flight delay today sits at priority 70, below the urgent threshold, and the travel-day rule
defers its digest; this change does not move it.

Owner gates (not adopted, not written as requirements):
- **G1.** Is there an immediate lane at all? If so its exact RFC 0011 text: latency bound, hold and
  expiry semantics, budget and daily cap interaction, double-send guard (it must reuse the shared
  claim), channels, and whether it replaces or sits beside the hourly urgent sub-cycle. bu-pyg3o1
  excluded it; it needs its own exact-text adoption.
- **G2.** Flight-delay consumer: which delay (minutes, connection at risk, boarding within N hours)
  earns interruption, and as an owner-authored rule or an approved derived right under bu-dhloi1.3.
  A model-selected high priority is excluded by doctrine.
- **G3.** Safety consumers while away: is the hourly urgent sub-cycle acceptable for leak, smoke, CO
  and gas, or does an owner want faster? Also the existing P1 of `specify-home-away-safety`
  (quiet-hours bypass for the new away-safety producers).
- **G6.** A Telegram approve button for a parked fix (a signed tap sink, bu-kqme7f.4) versus
  dashboard-only approval. Until answered, approval is dashboard-only.

Decided engineering defaults (coordinator ruling 2026-11-14; not owner gates, reversible by a later
change):
- **D4.** The arrival window mirrors the departure card (confirmed + 2 to + 30 minutes). A short
  absence (under 30 minutes) or one with nothing to report queues no card.
- **D5.** No tapless fix: a `reversible` fix still parks and runs only after an owner approval.
- **D7.** Fix declarations are written by the dashboard owner only, never by conversation or tool.

## Successor packets

- **S-A. Prepared-action status resolver (REQ-011).** No deps. Home read function, definer probe
  function and tests. Dark.
- **S-B. Departure fixes (REQ-008, REQ-009).** Deps: S-A, the departure card (merged). Home
  migration for `departure_fixes`, owner routes, parking in the departure job, cancellation and
  replay-time premise check, dashboard panel.
- **S-C. Arrival card (REQ-010).** Deps: S-A, S-B for fix lines (works without). Migration for
  `arrival_cards`, producer, premise probe.
- **S-D. Immediate-lane RFC text and consumers.** Blocked on G1 to G3; no implementation bead until
  the owner adopts exact text. Held.
- **S-E. Telegram fix button.** Blocked on G6 and bu-kqme7f.4.

Each Home slice stays dark behind its own flag and activates in one owner-approved change with the
timed lane and the presence reducer.
