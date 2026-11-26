## ADDED Requirements

### Requirement: One queue gathers everything that needs the owner

ID: REQ-calendar-needs-you-queue-001
Source: bu-5ii4gj.11 track N, slice N2
Scope: v1-mandatory

The calendar page SHALL offer one Needs-you queue holding the unanswered invitations, the overlap
issues, the pending proposals and the pending meeting debriefs the page already reads. The queue
SHALL add no endpoint and no stored state: it SHALL be composed from those reads. Each item SHALL
carry its kind, a stable id, the time the thing starts (when it has one) and the time it was raised.
Items SHALL be ordered by the start time ascending, items with no start after those with one, and
ties by the oldest raised first; the order SHALL be total and the same inputs SHALL give the same order.

#### Scenario: A queue holding one of each kind

- **WHEN** one invitation, one overlap, one proposal and one debrief are waiting
- **THEN** the queue SHALL list four items, soonest-starting first

#### Scenario: Equal inputs give an equal order

- **WHEN** two items start at the same time
- **THEN** the one raised earlier SHALL come first, and a re-render SHALL not reorder them

### Requirement: An unreadable kind is unknown, never empty

ID: REQ-calendar-needs-you-queue-002
Source: bu-5ii4gj.11 track N, slice N2
Scope: v1-mandatory

The queue SHALL track, per kind, whether its read is complete. A kind whose read failed, is
unavailable, or came back partial (a dropped row or a degraded source) SHALL be named as unknown in
the queue, and the count SHALL then be shown as a lower bound ("3+ need you") and SHALL never drop to
zero because of it. Only when every kind is complete and holds nothing SHALL the queue say that
nothing needs the owner. The `changes` kind (provider moves, cancellations and location changes) is not a counted kind until its feed exists: the queue SHALL show a fixed line that moved-under-you changes are not tracked yet, and the "+" and the unknown list SHALL name only a kind whose read failed.

#### Scenario: A failing proposals read does not empty the queue

- **WHEN** the proposals read fails and one invitation is waiting
- **THEN** the queue SHALL show the invitation, name proposals as unknown and show "1+ need you"

#### Scenario: A queue with an unknown kind is not empty

- **WHEN** no item is waiting but one kind is unknown
- **THEN** the queue SHALL say which kind is unknown and SHALL NOT say that nothing needs the owner

### Requirement: The queue is driven by the shared shortcut registry

ID: REQ-calendar-needs-you-queue-003
Source: bu-5ii4gj.11 track N, slice N2
Scope: v1-mandatory

The queue SHALL register its keys through the shared list-triage hook and so the shortcut registry:
`j` and `k` SHALL move the selection, `Enter` SHALL open the selected item's evidence (the event or
panel it concerns), and the verbs of the selected item's kind SHALL be bound to plain keys. Keys
SHALL be suspended while focus is in an editable field or an open dialog, SHALL appear in the `?` help
sheet and the footer hint, and SHALL each have a command-palette form acting on the same selected
item. For an invitation `a`, `d` and `t` SHALL accept, decline and tentatively accept it through the
existing invitation response path and show its receipt; for a proposal `a` SHALL accept and `d`
SHALL dismiss it through the existing proposal mutations. A key whose action is not available for the
selected item SHALL be unbound, not a silent no-op. A response to an invitation that reaches another
person SHALL keep the existing approval behaviour: the queue never sends one itself.

#### Scenario: Navigating and acting

- **WHEN** the owner presses `j` to select an invitation and then `d`
- **THEN** the existing decline path SHALL be called once for that invitation and its receipt SHALL show in the queue

#### Scenario: A verb that does not apply is not bound

- **WHEN** an overlap item is selected
- **THEN** `a` SHALL not be bound and SHALL not appear in the help sheet for that item

### Requirement: The queue states its own count

ID: REQ-calendar-needs-you-queue-004
Source: bu-5ii4gj.11 track N, slice N2
Scope: v1-mandatory

The queue's heading SHALL state its count ("3 need you"), in the lower-bound form ("3+ need you")
when a counted kind failed to read. A complete, empty queue SHALL say nothing needs the owner and add
no clause; an empty queue with an unknown kind SHALL read "0+ need you" and name the kind ("proposals
unknown"). The verdict's own per-kind clauses already count each kind and SHALL stay as they are; the
queue adds no second verdict clause.

#### Scenario: The count is stated

- **WHEN** three items wait and a kind failed to read
- **THEN** the heading SHALL read "3+ need you"

#### Scenario: An empty queue with an unknown kind

- **WHEN** no item waits and the proposals read failed
- **THEN** the heading SHALL read "0+ need you" and name proposals as unknown
