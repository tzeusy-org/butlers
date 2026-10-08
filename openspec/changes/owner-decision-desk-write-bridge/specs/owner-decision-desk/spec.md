## ADDED Requirements

### Requirement: Decision Intents Are Durable Owner Choices

The system SHALL record each owner decision choice as a durable decision intent in the Switchboard
schema before any tracker effect. An intent SHALL carry its bead id, the chosen option text, its
source (`dashboard` or `telegram`), its actor, an optional originating prompt id, a status, an
attempt count, categorical failure and last-error reasons, and creation, claim, and terminal
timestamps. Its status SHALL be exactly one of `pending`, `applying`, `applied`, or `failed`.

Recording SHALL validate the choice against the current decision digest. The bead SHALL be an open
decision with available structured details, and the option SHALL exactly match one of its current
options. Recording SHALL fail with a named reason when the digest is unavailable, when the bead is
not an open decision, when its structured details are unavailable, or when the option is not
offered. Recording SHALL NOT invent, normalize, or default an option.

At most one intent per bead SHALL be live (`pending`, `applying`, or `applied`), enforced by the
database. Recording the same option for a bead that already has a live intent SHALL return that
intent unchanged and identify it as not newly created. Recording a different option SHALL be
refused as a conflict that names the existing intent's state. A `failed` intent SHALL NOT block a
new choice.

ID: REQ-owner-decision-desk-001
Source: bu-ckkpz.3; owner ruling 2026-10-08
Scope: v1-mandatory

#### Scenario: A valid choice records one pending intent

- **WHEN** the owner chooses an offered option for an open decision with structured details
- **THEN** one intent is stored with status `pending`, the exact option text, its source, and its
  actor

#### Scenario: A repeated identical choice is idempotent

- **WHEN** the same option is recorded again for a bead whose intent is still live
- **THEN** the existing intent is returned unchanged and reported as not newly created
- **AND** no second live intent exists

#### Scenario: A conflicting choice is refused

- **WHEN** a different option is recorded while the bead has a live intent
- **THEN** recording is refused as a conflict naming the existing intent's state
- **AND** the existing intent is unchanged

#### Scenario: An unoffered or stale choice is refused by name

- **WHEN** the option is not among the bead's current options, the bead is not an open decision,
  or the digest is unavailable
- **THEN** no intent is stored and the refusal names that reason

### Requirement: The Applier Applies Each Intent At Most Once

The development tracker bridge workload SHALL apply decision intents with `bd` so that each
intent's tracker effect happens at most once and every intent reaches an honest terminal state.

The applier SHALL first reconcile every intent left `applying` by an earlier run, using the
tracker's current state as evidence: a closed bead whose close reason carries the intent's marker
`decision-intent <intent id>` is `applied`; a bead closed without that marker is `failed` with
reason `bead_closed_elsewhere`; a still-open bead returns to `pending`. It SHALL NOT re-run a tracker
operation for an intent before this reconciliation.

The applier SHALL then claim pending intents oldest first, in a bounded batch, by atomically moving
each from `pending` to `applying` and committing that claim before any tracker call. For each
claimed intent it SHALL re-read the bead and SHALL fail the intent with a categorical reason when the
bead does not exist (`bead_not_found`), is not open (`bead_not_open`), no longer carries the
`decision` label (`not_a_decision`), or no longer offers the option (`option_not_offered`). A bead
that `bd` answers for but cannot describe SHALL fail that intent alone (`bead_unreadable`) and
SHALL NOT stop the batch. Otherwise
it SHALL close the bead with a reason containing the chosen option, the intent marker, and the
source, under a fixed audit actor, and mark the intent `applied`.

A failed close SHALL be re-read before classification: our marker on a closed bead means `applied`.
A transient tracker failure SHALL return the intent to `pending` with a categorical `last_error`;
`bd_close_failed` SHALL become terminal `failed` after three attempts, and an unreachable tracker
(`tracker_unavailable`) SHALL NOT consume the attempt budget and SHALL stop the batch. Failure
reasons SHALL be categorical and SHALL NOT contain raw `bd` output, credentials, or host names.
After applying, the workload SHALL refresh the export so the Decisions lane reflects the closure;
an apply failure SHALL NOT prevent that export, and the run SHALL report failure when either step
failed.

ID: REQ-owner-decision-desk-002
Source: bu-ckkpz.3; REQ-runtime-attention-outbox-002 (at-most-once precedent)
Scope: v1-mandatory

#### Scenario: A pending intent closes its bead once

- **WHEN** a pending intent names an open decision that still offers the option
- **THEN** the bead is closed with a reason carrying the option and `decision-intent <intent id>`
- **AND** the intent becomes `applied`

#### Scenario: A crash after claim does not double-apply

- **WHEN** a run claimed an intent and stopped before recording the outcome
- **THEN** the next run marks it `applied` if the bead carries its marker and returns it to
  `pending` if the bead is still open
- **AND** the bead is closed by that intent at most once

#### Scenario: A drifted bead fails the intent honestly

- **WHEN** the bead was closed elsewhere, lost its decision label, or no longer offers the option
- **THEN** the intent becomes `failed` with the matching categorical reason
- **AND** the bead is not modified

#### Scenario: A tracker outage leaves intents pending

- **WHEN** the tracker is unreachable during a run
- **THEN** claimed intents return to `pending` with `last_error=tracker_unavailable`
- **AND** the export step still runs and the run reports failure

### Requirement: Decision Prompts Route Through the Attention Policy

A deterministic Switchboard job SHALL offer eligible decisions to the owner as Telegram decision
prompts. It SHALL take no action unless routing is explicitly enabled for the deployment. An
eligible decision is an open decision with available structured details, no existing prompt, and no
live intent; candidates SHALL be ordered with escalated decisions first, then oldest first.

Before any delivery the job SHALL apply, in order, the owner quiet-hours and context-bus
suppression used by the existing decision-review jobs, then a budget of at most three prompts per
rolling 24 hours counting delivered, uncertain, and in-flight prompts. A suppressed or over-budget
candidate SHALL be recorded in the attention ledger as `deferred` with reason `quiet_hours`,
`context_bus:<signal>`, or `budget_exhausted`, at most once per bead per 12 hours, and SHALL remain
eligible for a later run.

Each delivery SHALL first reserve the bead's single prompt row, snapshotting the offered options and
default. Only a prompt whose prior outcome was proven `not_attempted` MAY be reserved again. The job
SHALL map the delivery's transport outcome to the prompt: `confirmed` to `delivered`;
`not_attempted` to `not_attempted`; `rejected` to `rejected`; and `uncertain`, a missing outcome, or
an exception to `uncertain`. A reservation still without an outcome after ten minutes SHALL become
`uncertain`. An `uncertain`, `rejected`, or `delivered` prompt SHALL never be re-sent. Every
delivery branch SHALL record an attention-ledger row (`delivered` or `failed` with a categorical
reason). The job SHALL use fixed templates and SHALL NOT invoke a model.

ID: REQ-owner-decision-desk-003
Source: RFC 0011 Amendment 1 (attention ledger); RFC 0021 §1 (push budget shape);
REQ-core-notify-027; Non-Negotiable Rule 4
Scope: v1-mandatory

#### Scenario: Disabled routing sends nothing

- **WHEN** routing is not enabled for the deployment
- **THEN** the job sends no prompt and records no ledger row

#### Scenario: Quiet hours defer without dropping

- **WHEN** an eligible decision exists during owner quiet hours
- **THEN** no prompt is sent and the ledger records `deferred` with reason `quiet_hours`
- **AND** the decision is offered on a later run outside quiet hours

#### Scenario: The daily budget caps prompts

- **WHEN** three prompts were delivered in the last 24 hours and another decision is eligible
- **THEN** no prompt is sent and the ledger records `deferred` with reason `budget_exhausted`

#### Scenario: An uncertain send is never repeated

- **WHEN** a prompt's delivery outcome is uncertain
- **THEN** the prompt is recorded `uncertain` and the ledger records `failed`
- **AND** no later run sends a prompt for that bead

### Requirement: Telegram One-Tap Decision Capture

A decision prompt SHALL travel as a `notify.v1` envelope with intent `decision_request` through
Switchboard and Messenger, carrying an explicit owner recipient, a fixed-template message that
lists the decision title and its options, one `choose` action per option (at most sixteen, each
with a button label of at most 64 characters and a signed callback token), and exactly one
`open_dashboard` action. Messenger SHALL deliver it only to a recipient that resolves to a verified
owner channel. On Telegram it SHALL render one button row per option plus a dashboard link row;
channels without inline buttons SHALL receive the message and link. Deferred delivery SHALL NOT
coalesce a decision request into a digest.

Each callback token SHALL be `dsk1:<prompt id>:<option index>:<signature>`, at most 64 UTF-8 bytes,
signed with the same Tier-1 callback secret and signer as RFC 0021 approval tokens over a payload
that includes the `dsk1` prefix, the prompt id, the option index, and the prompt's creation time.

The Telegram bot connector SHALL handle a `dsk1` callback before ingestion and before its generic
callback acknowledgement. It SHALL verify that the tapping sender is the primary verified owner
channel, load the prompt through a connector-scoped route, verify the signature against the
prompt's creation time, and record the choice through a second connector-scoped route with actor
`owner@telegram`. It SHALL acknowledge the callback once the outcome is known, so the
acknowledgement states what happened. On a recorded choice it SHALL edit the prompt message to the
recorded, pending-application state and remove its keyboard. A malformed or invalid token, a
non-owner sender, or a missing prompt SHALL receive only a generic acknowledgement and SHALL change
nothing. A decision that already has a live intent, or is no longer open, SHALL receive an
"already handled" acknowledgement; changed options SHALL receive an "open the dashboard"
acknowledgement; both SHALL record no new intent and remove the keyboard. A failure that may be
transient SHALL say so and keep the keyboard.

ID: REQ-owner-decision-desk-004
Source: RFC 0021 §2 (inline keyboards, signed callbacks, owner-channel verification);
RFC 0017 (owner-routing safety); Non-Negotiable Rule 7
Scope: v1-mandatory

#### Scenario: One tap records the chosen option

- **WHEN** the owner taps an option button on a delivered decision prompt
- **THEN** a `telegram` intent for that option is recorded with actor `owner@telegram`
- **AND** the message is edited to the recorded state and its keyboard removed

#### Scenario: A forged or foreign tap changes nothing

- **WHEN** a callback carries an invalid signature or comes from a non-owner sender
- **THEN** the callback receives only a generic acknowledgement
- **AND** no intent is recorded

#### Scenario: A second tap is already handled

- **WHEN** the owner taps any option on a prompt whose bead already has a live intent
- **THEN** the callback is acknowledged as already handled and no new intent is recorded

#### Scenario: Decision requests require an owner recipient and options

- **WHEN** a `decision_request` envelope lacks a recipient, a `choose` action, or the single
  `open_dashboard` action, or carries an approval verb
- **THEN** the envelope is rejected before delivery

## Source References

- Non-Negotiable Rule 4 (the daemon is deterministic infrastructure)
- Non-Negotiable Rule 7 (transport is connector responsibility)
- RFC 0011 Amendment 1 (attention ledger)
- RFC 0017 (owner-routing safety)
- RFC 0021 (decision loop: one-tap callbacks)
- RFC 0025 (tracker-host beads projection exporter, amended for the development bridge)
