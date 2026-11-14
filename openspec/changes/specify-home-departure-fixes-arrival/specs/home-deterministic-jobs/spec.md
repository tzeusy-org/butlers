## ADDED Requirements

### Requirement: Departure fixes are exact, per item and always owner-approved

The Home butler SHALL offer a fix for a departure-card item only when the owner has declared one for that item in `home.departure_fixes`: one exact `(domain, service)`, explicit entity target and allowed data keys, for an entity that is in `home.departure_watch`. The declaration SHALL be written only through an owner-authenticated route and SHALL be refused when the call is not an explicit-entity call that RFC 0028 classifies. When the departure card is queued the job SHALL park one prepared action per fixable item through the approvals module with `origin='prepared'` and the exact stored call, SHALL store the RFC 0028 risk class on it, and SHALL NOT dispatch any Home Assistant request itself. Every fix SHALL require an owner approval regardless of its risk class. No action SHALL cover more than one item, and no "secure all" action SHALL exist. The job SHALL run with no model in the loop and SHALL stay dark behind its own flag, off by default.

ID: REQ-home-deterministic-jobs-008
Source: RFC 0028; bu-svqpzo.12; RFC 0038 D18; bu-jv1moa.29
Scope: v1-mandatory

#### Scenario: A declared item gets one parked fix

- **WHEN** the departure card for a confirmed leave edge lists a watched item that has a declared fix
- **THEN** exactly one prepared action SHALL be parked with dedup key `home:departure-fix:<edge id>:<entity id>` carrying the stored call unchanged
- **AND** a later run for the same edge SHALL NOT park a second one

#### Scenario: A model cannot author or alter a fix

- **WHEN** a model-driven session or a tool argument supplies a service call for a departure item
- **THEN** no fix SHALL be parked from it and the declared call SHALL be the only call ever stored

#### Scenario: Risk class does not waive approval

- **WHEN** a declared fix is classed `reversible` under RFC 0028
- **THEN** it SHALL still park and SHALL run only after an owner approval
- **AND** a `protected` fix SHALL be marked as such in its card line and SHALL NOT be covered by a default standing rule

#### Scenario: Undeclared or non-explicit calls are refused

- **WHEN** a fix declaration names an area, device, label or all-entity target, a data key outside the allowlist, an entity not in the departure watch, or comes from an unauthenticated caller
- **THEN** the route SHALL store nothing and the refusal SHALL be content-blind and audited by outcome only

### Requirement: Cancellation never claims an undone effect

Before every run and before dispatching every replay, the fix job SHALL re-read the away premise from the canonical `owner.place` slot and timeline. A fix's premise is bound to the leave edge it was parked for (the edge id in its dedup key): the premise holds only while that edge is still the latest confirmed, unretracted `owner.place` edge, so a later absence never revives an earlier absence's fix. It SHALL reject each still-`pending` fix with a system actor and a content-blind reason `owner_returned`, `premise_retracted`, `already_secure` or `watch_removed`. For an `approved` fix whose premise has ended it SHALL refuse to dispatch any Home Assistant request. It SHALL NOT edit, delete or reinterpret any actuation receipt, and SHALL NOT roll back an executed fix. Text produced about a fix SHALL be derived from its resolver status, and SHALL NOT say a fix was undone, reverted or restored.

ID: REQ-home-deterministic-jobs-009
Source: RFC 0028 (receipt and `unverified` semantics); module-approvals status transitions; bu-jv1moa.29
Scope: v1-mandatory

#### Scenario: Return cancels pending fixes

- **WHEN** a confirmed, non-late enter edge follows the leave edge while a fix is still `pending`
- **THEN** the fix SHALL be rejected with reason `owner_returned` and no Home Assistant request SHALL be made
- **AND** its status SHALL read `cancelled`, not `rejected` by the owner

#### Scenario: An executed fix keeps its receipt

- **WHEN** the owner approved a fix that settled `succeeded`, `failed` or `unverified` before the owner returned
- **THEN** arrival SHALL leave the receipt and approval unchanged and SHALL NOT claim the effect was undone
- **AND** an `unverified` fix SHALL read as unclear and SHALL NOT read as success

#### Scenario: An old approval cannot actuate after the return

- **WHEN** an approved fix replays after a confirmed enter edge or a retraction of the leave
- **THEN** the replay SHALL dispatch nothing and SHALL record that it did not run

#### Scenario: A later absence does not revive an earlier fix

- **WHEN** a fix approved for one leave edge replays after the owner returned and then left again on a new leave edge
- **THEN** the replay SHALL dispatch nothing, because its originating leave edge is no longer the latest confirmed `owner.place` edge

#### Scenario: Unknown presence holds

- **WHEN** the away premise reads unknown
- **THEN** pending fixes SHALL neither be rejected nor dispatched and the next run SHALL re-read it

### Requirement: Arrival card of what changed while away

The Home butler SHALL queue at most one read-only arrival card per confirmed, non-late `owner.place` enter edge that follows a leave edge by at least 30 minutes and for which a departure card, a parked fix or a Home condition existed, through the Switchboard timed lane (RFC 0011 Amendment 8), with dedup key `home:arrival:<edge id>` and a premise probe `("home", "arrival_card_current")` carrying the edge id and its `confirmed_at`. The card SHALL list, by the owner's labels only, the Home conditions that opened or resolved during the absence with their reasons, the departure-card items still not secure, each fix with its resolver status, and the stretches in which the Home Assistant source was not healthy. It SHALL NOT be inferred from Home Assistant history, SHALL NOT contain `friendly_name` or any coordinate, and SHALL contain no button, prepared action or Home Assistant call. An absence with a departure card whose items all stayed secure SHALL say that nothing Home tracks changed and that Home tracks only what the owner declared. The job SHALL run with no model in the loop and SHALL stay dark behind its own flag, off by default.

ID: REQ-home-deterministic-jobs-010
Source: RFC 0011 Amendment 8; bu-jv1moa.29
Scope: v1-mandatory

#### Scenario: An enter edge queues one card

- **WHEN** a confirmed, non-late enter edge follows a leave edge
- **THEN** one timed candidate SHALL be queued with `deliver_after` confirmed + 2 minutes and `expires_at` confirmed + 30 minutes
- **AND** a later run SHALL NOT queue a second for the same edge

#### Scenario: A short or empty absence queues no card

- **WHEN** the enter edge follows the leave edge by less than 30 minutes, or no departure card, fix or condition existed for the absence
- **THEN** no arrival candidate SHALL be queued

#### Scenario: A retracted or superseded enter withdraws the card

- **WHEN** the enter edge is retracted, late, or followed by a confirmed leave before delivery
- **THEN** the premise probe SHALL answer false and the candidate SHALL be withdrawn unsent

#### Scenario: A fix is described by its actual status

- **WHEN** the absence included a parked fix
- **THEN** the card SHALL show that fix's resolver status and SHALL NOT show success unless the receipt is `succeeded`

#### Scenario: A source outage is visible

- **WHEN** the Home Assistant source lease was missing, in error or expired during the absence
- **THEN** the card SHALL say Home could not see for that stretch and SHALL NOT call the home secure

#### Scenario: Holds and window follow the timed lane

- **WHEN** the owner is `dnd` or `sleeping`
- **THEN** the candidate SHALL hold and SHALL expire at the end of its window rather than arriving later

### Requirement: Prepared-action status resolver

Home SHALL provide `prepared_action_status(action_id)`, a read-only resolver returning exactly one of `awaiting_approval`, `approved_not_run`, `succeeded`, `unverified`, `failed`, `rejected`, `cancelled`, `expired`, `abandoned` or `unknown`, derived from Home's own approval row and the latest `home.ha_command_log` receipt for that approval. The receipt SHALL decide the executed case. A system rejection recorded under REQ-home-deterministic-jobs-009 SHALL be `cancelled`. A missing, unreadable or unclassifiable row SHALL be `unknown`. The resolver SHALL return no arguments, target, message text or free-form reason. The Switchboard SHALL reach it only through a `SECURITY DEFINER` function with a pinned `search_path` that returns only that closed set, executable by `butler_switchboard_rw` and not by PUBLIC.

ID: REQ-home-deterministic-jobs-011
Source: RFC 0028; module-approvals status contract; core_335 `resolve_home_departure`; bu-jv1moa.29
Scope: v1-mandatory

#### Scenario: Receipt decides the executed case

- **WHEN** an approved action has a receipt settled `succeeded`, `failed` or `unverified`
- **THEN** the resolver SHALL return that receipt outcome and not `approved_not_run`

#### Scenario: Unknown is never success

- **WHEN** the action row is missing or its state cannot be classified
- **THEN** the resolver SHALL return `unknown` and no consumer SHALL render it as done

#### Scenario: A changed status withdraws a queued card

- **WHEN** a card was queued showing a fix status and the status changed before sending
- **THEN** the timed lane's premise check SHALL answer false and the card SHALL be withdrawn unsent

#### Scenario: The probe is content-blind

- **WHEN** the Switchboard calls the definer function
- **THEN** it SHALL receive only a status from the closed set and SHALL NOT be able to read the action's arguments or receipt fields

### Requirement: No immediate lane in these outcomes

The departure fix, arrival card and status resolver SHALL use the timed lane at routine priority and SHALL NOT set an urgent priority, request a quiet-hours bypass or add a lane. A model invocation SHALL NOT choose high priority for any of them. Only authenticated deterministic safety producers, owner-authored break-through rules and owner-flagged obligations MAY bypass quiet hours or sleep (bu-dhloi1.3), and the timed lane's approval SHALL NOT be treated as authorization for urgency. An immediate lane, a flight-delay consumer and any new safety consumer are not specified here and require their own exact-text owner adoption.

ID: REQ-home-deterministic-jobs-012
Source: bu-pyg3o1 (immediate-lane text excluded); bu-dhloi1.3; bu-jv1moa.8
Scope: v1-mandatory

#### Scenario: These producers stay routine

- **WHEN** the fix job or the arrival job proposes an insight candidate
- **THEN** its priority SHALL be below the urgent threshold and its lane SHALL be `timed`

#### Scenario: Model-chosen urgency is not a path

- **WHEN** a model session asks that a fix or arrival message be urgent
- **THEN** the request SHALL have no effect on its priority or lane
