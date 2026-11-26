## ADDED Requirements

### Requirement: A change plan is compiled deterministically

ID: REQ-calendar-change-plans-001
Source: bu-5ii4gj.11 slice P1; design "Calendar change plans"
Scope: v1-mandatory

A plan verb (`clear_window` or `shift_window`) with its parameters SHALL compile to an ordered list
of steps and a list of skipped events, by a pure function of the verb, the window and the owner's
events with their provenance and the owner's response to each. No model, provider call or
randomness SHALL take part: equal inputs SHALL give equal steps and an equal `plan_digest`. A step
SHALL carry the event it acts on, an action (`move`, `decline`), the exact tool call, an authority
class (`no_park_undo`, `owner_submit`, `parked_per_event`) and a reason. A Butlers block with no
attendees SHALL be `no_park_undo`; an owner-organised event with no other attendees SHALL be
`owner_submit`; anything that notifies another person (an owner-organised event with attendees, a
decline of an invited meeting) SHALL be `parked_per_event`. `clear_window` SHALL move each movable
event to the earliest slot after the window that is free on every owner-busy calendar and SHALL
decline an invited meeting; `shift_window` SHALL move each movable event in the window by the same
signed delta and SHALL leave invited meetings skipped, because only the organizer can move them. A
step SHALL never be offered for an event whose ownership is unproven, for a series edit, for an
all-day or out-of-office event, or for a cancelled or declined event: each goes to `skipped` with a
stated reason. A recurring event SHALL be acted on per occurrence by the provider's own instance
id. A step's destination SHALL never overlap another busy interval, including an earlier step's
destination in the same plan.

#### Scenario: Clearing a day moves what the owner owns and declines what they were invited to

- **WHEN** `clear_window` is compiled over a day holding a Butlers block, an owner-organised solo event and an invited meeting
- **THEN** the block SHALL be a `no_park_undo` move, the solo event an `owner_submit` move, and the meeting a `parked_per_event` decline, none of them overlapping each other or any other busy interval

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
whose calendar source is stale SHALL carry no steps and the reason `stale_source`: a plan is never
compiled against events read while stale. A plan SHALL cover at most one day of window or sixteen
steps; a larger request SHALL be refused with the reason `plan_too_large`, never silently
truncated.

#### Scenario: Planning writes nothing

- **WHEN** `calendar_plan_change` is called
- **THEN** no provider write, approvals card or action-log row SHALL exist afterwards

#### Scenario: A stale source gives no plan

- **WHEN** a calendar source in the window is stale
- **THEN** the plan SHALL have no steps and the reason `stale_source`

#### Scenario: An oversized plan is refused

- **WHEN** a request would need more than sixteen steps
- **THEN** it SHALL be refused with `plan_too_large` and no partial plan

### Requirement: A plan is applied as one decision with per-step receipts

ID: REQ-calendar-change-plans-003
Source: bu-5ii4gj.11 slice P2
Scope: v1-mandatory

`calendar_apply_plan(plan_id, plan_digest)` SHALL re-compile the plan and refuse with no write when
the digest differs. It SHALL park every `parked_per_event` step as one decision set, wait on it,
then run the `no_park_undo` and `owner_submit` steps in order. Every step SHALL be bound to the
event's etag the plan was compiled against and sent once: a changed etag SHALL decline that step
(`changed_since_plan`) and write nothing for it. Each step SHALL leave a `plan_step` row in
`calendar_action_log` with the plan id, step index, pre-state, `etag_after` and an outcome of
`applied`, `declined`, `uncertain` or `skipped`. A step outcome SHALL NOT depend on a later step:
one declined step does not stop the others.

#### Scenario: A stale digest applies nothing

- **WHEN** `calendar_apply_plan` is called with a digest that no longer matches the compiled plan
- **THEN** it SHALL refuse and no step SHALL be written

#### Scenario: A changed event declines only its own step

- **WHEN** one event's etag changed after the plan was compiled
- **THEN** that step SHALL be recorded `declined` with `changed_since_plan` and the others SHALL still run

### Requirement: A plan has one composite undo

ID: REQ-calendar-change-plans-004
Source: bu-5ii4gj.11 slice P2
Scope: v1-mandatory

`calendar_undo_plan(plan_id)` SHALL be offered as one approvals card. Approving it SHALL reverse
each `applied` step bound to the `etag_after` that step recorded: a step whose event changed since
SHALL be left alone and recorded as not undone. A step that was `declined`, `uncertain` or
`skipped` SHALL NOT be reversed. A reversal that reaches another person SHALL be part of the same
card; no third-party-visible change SHALL happen without the owner's approval of that card.

#### Scenario: Undo reverses only what is still as the plan left it

- **WHEN** a plan applied two moves and the owner later edited one of them
- **THEN** approving the undo SHALL restore the untouched one and leave the edited one, recording it not undone
