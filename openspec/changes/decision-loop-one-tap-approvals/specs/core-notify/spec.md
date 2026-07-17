# Core Notify — Delta

## ADDED Requirements

### Requirement: Approval-Request Delivery Intent

The `notify.v1` envelope SHALL support `intent = "approval_request"` for
owner-targeted approval notifications originated by daemon infrastructure (the
approvals gate park path). The envelope carries an `actions` payload listing
decision affordances (verb, signed callback token, dashboard deep link).
`approval_request` envelopes MUST target the owner only — envelopes targeting
any non-owner recipient are rejected at validation. Channel rendering is the
delivery plane's responsibility: telegram renders inline buttons; channels
without interactive affordances deliver the same summary text with a dashboard
deep link. Approval-request delivery MUST NOT consume the proactive-insight
budget.

#### Scenario: Approval request renders buttons on telegram

- **WHEN** an `approval_request` envelope with an `actions` payload is
  delivered to the owner's telegram channel
- **THEN** the Messenger sends the message with Approve/Reject inline buttons
  bound to the payload's callback tokens and an Open dashboard link

#### Scenario: Non-interactive channel falls back to a deep link

- **WHEN** an `approval_request` envelope is delivered over a channel without
  interactive button support (e.g. email)
- **THEN** the message contains the dossier summary and the dashboard deep
  link, and no interactive affordance is required for the owner to decide

#### Scenario: Non-owner target is rejected

- **WHEN** an `approval_request` envelope targets a recipient that does not
  resolve to the owner
- **THEN** envelope validation fails with a structured error and nothing is
  delivered

#### Scenario: Insight budget is unaffected

- **WHEN** approval-request notifications are delivered on a day the insight
  budget is exhausted
- **THEN** they deliver normally and do not decrement the insight budget

### Requirement: Notify Decision-Dossier Boundary

The core `notify()` tool SHALL accept `_why`, `_evidence`, `_blast_radius`, and
`_reversibility` as a strict decision dossier when it executes through the
approvals recipient guard. After recipient owner resolution, a non-owner or
unresolvable target MUST return the structured retryable dossier error for a
missing or malformed `why` before standing-rule matching or any
`pending_actions` persistence. Owner-directed notifications remain exempt from
the required `why` field, while supplied dossier values are still validated
strictly. A valid typed dossier MUST be persisted on every `notify()` park
path, including a missing entity-channel identifier.

#### Scenario: Non-owner notify rejects a missing or malformed why without parking

- **WHEN** `notify()` resolves an explicit recipient as non-owner or
  unresolvable and `_why` is absent, blank, or otherwise malformed
- **THEN** it returns the retryable structured dossier error before querying
  standing rules or creating a pending action

#### Scenario: Owner notify remains exempt

- **WHEN** `notify()` resolves its recipient as the owner and no dossier is
  supplied
- **THEN** it continues through the owner-approved path without requiring
  `_why`

#### Scenario: Valid notify dossier persists on recipient and identifier parks

- **WHEN** a non-owner `notify()` invocation with a valid typed dossier is
  parked by recipient approval gating or because its target entity lacks a
  channel identifier
- **THEN** the resulting `pending_actions` row stores `why`, `evidence`,
  `blast_radius`, and `reversibility`
