## ADDED Requirements

### Requirement: A change plan is compiled deterministically

ID: REQ-calendar-change-plans-001
Source: bu-5ii4gj.11 slice P1; design "Calendar change plans"
Scope: v1-mandatory

A plan verb (`clear_window` or `shift_window`) with its parameters SHALL compile to an ordered list
of steps and a list of skipped events, by a pure function of the verb, the window and the owner's
events with their provenance and the owner's response to each. No model, provider call or
randomness SHALL take part: equal inputs SHALL give equal steps and an equal `plan_digest`. A step
SHALL carry the event it acts on, an action (`move`, `decline`, `tentative`), the exact tool call,
an authority class (`no_park_undo`, `owner_submit`, `parked_per_event`), a reason and the steps it
depends on (`depends_on`). A Butlers block with no attendees SHALL be `no_park_undo` and is the only
step that may run without an owner card; an owner-organised event with no other attendees SHALL be
`owner_submit` and an event that notifies another person (an owner-organised event with
attendees, a decline of an invited meeting) SHALL be `parked_per_event`. Both of those SHALL reach
the owner as a card: no step for an owner's own event runs without the owner's approval. A decline
SHALL name its `send_updates` explicitly (default none). `clear_window` SHALL move each movable
event to the earliest free slot after the window that lies inside the owner's working hours in
their own zone and within seven days of the window (`no_free_slot` is a skip, never a guess). The
compiler SHALL pick the action itself: a decline for an invited meeting and a move for a Butlers
block. A tentative response and a delete are owner overrides, not offered by this change: the compiler
never chooses them.
`shift_window` SHALL move each movable event in the window by the same signed delta and SHALL leave
invited meetings skipped, because only the organizer can move them. Skips SHALL be decided before
any slot is treated as free: only an event that really gets a step vacates its slot, and a step
that uses a slot another step vacates SHALL list that step in `depends_on`; an event's own source
slot counts as vacated only for its declared dependents. A step SHALL never be offered for an event whose ownership is unproven, for a series edit, for an
all-day or out-of-office event, or for a cancelled or declined event: each goes to `skipped` with a
stated reason. A recurring event SHALL be acted on per occurrence by the provider's own instance
id. A step's destination SHALL never overlap another busy interval, including an earlier step's
destination in the same plan. A step's request id SHALL be a UUID derived from the event's etag, the
verb, the window, the delta and the step index, so the same state replays one command and a changed
state is a new one.

#### Scenario: Clearing a day moves what the owner owns and declines what they were invited to

- **WHEN** `clear_window` is compiled over a day holding a Butlers block, an owner-organised solo event and an invited meeting
- **THEN** the block SHALL be a `no_park_undo` move, the solo event an `owner_submit` move, and the meeting a `parked_per_event` decline, none of them overlapping each other or any other busy interval

#### Scenario: A skipped event does not free its slot

- **WHEN** a shifted event would land on the slot of an event that was skipped (for example a recurring event with no instance id)
- **THEN** the first SHALL not be moved onto the second, and SHALL be skipped or moved elsewhere

#### Scenario: Shifting leaves an invited meeting alone

- **WHEN** `shift_window` is compiled over a window holding an invited meeting
- **THEN** the meeting SHALL appear in `skipped` with the reason that only its organizer can move it, and SHALL have no step

#### Scenario: An event the owner cannot prove they own is never touched

- **WHEN** an event in the window has unproven ownership
- **THEN** it SHALL be skipped with that reason and SHALL have no step

#### Scenario: Equal inputs give an equal digest

- **WHEN** the same verb, window and events are compiled twice
- **THEN** the steps and `plan_digest` SHALL be identical

### Requirement: Planning is read-only

ID: REQ-calendar-change-plans-002
Source: bu-5ii4gj.11 slice P1
Scope: v1-mandatory

`calendar_plan_change(kind, params)` SHALL return `{plan_id, plan_digest, steps, skipped}` and SHALL
write nothing: no provider call, no approvals card, no `calendar_action_log` row. The `plan_id`
SHALL derive from the `plan_digest`, so a retried call returns the same plan. A plan over a window
whose calendar source is stale, or whose projection is partial, SHALL be refused with the reason
`plan_unavailable`: a plan is never compiled against events read while stale. A plan SHALL cover at
most one day of window or sixteen steps, counting the parked items; a larger request SHALL be
refused with the reason `plan_too_large` and a suggestion to split it by day, never silently
truncated.

#### Scenario: Planning writes nothing

- **WHEN** `calendar_plan_change` is called
- **THEN** no provider write, approvals card or action-log row SHALL exist afterwards

#### Scenario: A stale source gives no plan

- **WHEN** a calendar source in the window is stale
- **THEN** the plan SHALL be refused with the reason `plan_unavailable` and have no steps

#### Scenario: An oversized plan is refused

- **WHEN** a request would need more than sixteen steps
- **THEN** it SHALL be refused with `plan_too_large` and no partial plan

### Requirement: A plan is applied through approvals with per-step receipts

ID: REQ-calendar-change-plans-003
Source: bu-5ii4gj.11 slice P2
Scope: v1-mandatory

`calendar_apply_plan(plan_id, plan_digest)` SHALL re-compile the plan and refuse with no write when
the digest differs. It SHALL return `pending` at once: every step that touches an owner's event
(`owner_submit` and `parked_per_event`) SHALL be parked as its own approvals card, bound to the etag
the plan was compiled against, and only `no_park_undo` steps (Butlers blocks) MAY run directly. An
approved card runs through the approvals executor as its own stored call, sent once. A changed etag
SHALL decline that step (`changed_since_check`) and write nothing for it; a step whose dependency
was not applied SHALL decline `dependency_not_applied`, and a move whose dependency is still
undecided SHALL be deferred, not declined: the executor leaves the approved card retryable and
the move runs once its dependencies settle, so the owner is never asked to plan the change again.
Only moves (`calendar_update_event`) defer; a respond step is never anyone's dependency. A deferral
is audited as the executor's `action_execution_failed` with the closed text `dependency_pending:
...` (logged at error level today; the core executor is unchanged) and writes no
`calendar_action_log` receipt. A dependency that is approved but whose outcome is uncertain keeps
the dependent waiting, not declined, and a card that has waited 48 hours on `pending_actions.
requested_at` is declined `dependency_not_applied`. No standing rule auto-applies `calendar_respond`. A declined
or uncertain step SHALL skip its dependents. A decline step SHALL be parked through the calendar
module's own response coordinator, never auto-approved on the agent path. A rejected or expired card SHALL run no step for it and the plan SHALL record the
outcome. Each step SHALL leave a `plan_step` row in `calendar_action_log` with the plan id, step
index, pre-state, `etag_after` and an outcome of `applied`, `declined`, `uncertain` or `skipped`.
Re-applying the same `plan_id` SHALL replay the recorded `plan_step` receipts and finish only the
rest, never re-send a step; resume works only while the plan still compiles to the same digest; once a
card executes the events differ and a re-apply is refused with `plan_changed`. No dashboard route SHALL apply a plan except by parking; the plan tools
are MCP-only. Until the goal-runtime cutover (RFC 0038) the cards are per step; after it the parked
steps become one decision set with per-item untick (`explicit_only`), and an unticked item leaves its
event untouched and recomputes its dependents.

#### Scenario: A stale digest applies nothing

- **WHEN** `calendar_apply_plan` is called with a digest that no longer matches the compiled plan
- **THEN** it SHALL refuse and no step SHALL be written

#### Scenario: No owner event moves without a card

- **WHEN** a plan holding an `owner_submit` step is applied
- **THEN** that step SHALL be parked as an approvals card and the provider SHALL not be written until the owner approves it

#### Scenario: A changed event declines only its own step

- **WHEN** one event's etag changed after the plan was compiled
- **THEN** that step SHALL be recorded `declined` with `changed_since_check` and the independent others SHALL still run

#### Scenario: A declined step skips its dependents

- **WHEN** a step that vacated a slot is declined and a later step was to use that slot
- **THEN** the later step SHALL be `declined` with `dependency_not_applied`

#### Scenario: Re-applying resumes

- **WHEN** `calendar_apply_plan` is called again for a plan with recorded receipts
- **THEN** it SHALL replay the recorded outcomes and send only the steps with no receipt

### Requirement: A plan is undone one reversal at a time

ID: REQ-calendar-change-plans-004
Source: bu-5ii4gj.11 slice P2
Scope: v1-mandatory

`calendar_undo_plan(plan_id)` SHALL offer one approvals card per reversal, each reversal bound to
the `etag_after` that step recorded, and SHALL return `pending`. Approving a card SHALL reverse that
step only if its event is still as the plan left it: a step whose event changed since SHALL be left
alone and recorded as not undone (`changed_since_move` when the event changed after the step's own
move). Reversals SHALL be offered highest step first. A reversal SHALL list in `depends_on` the reversals of
the applied steps that moved into the slot its event left, and SHALL be refused with
`blocked_by_step_N` when one of those reversals could not be offered. A reversal SHALL run with the
card's `fail` conflict policy, so a destination already busy declines it as `destination_busy`
rather than overlapping another event, and SHALL be refused as `in_the_past` once the start it would
restore has passed. A step that was `declined`, `uncertain` or `skipped` SHALL
NOT be reversed. A reversal that reaches another person cannot be offered: a decline reversal SHALL be refused with
`response_reversal_not_supported`, and the owner re-answers the invitation from the conflict radar.
No third-party-visible change SHALL happen without the owner's approval. After the
goal-runtime cutover the reversals become one decision set with per-item approval.

#### Scenario: Undo reverses only what is still as the plan left it

- **WHEN** a plan applied two moves and the owner later edited one of them
- **THEN** approving the undo cards SHALL restore the untouched one and leave the edited one, recording it not undone
