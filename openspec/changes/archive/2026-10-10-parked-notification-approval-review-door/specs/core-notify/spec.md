## ADDED Requirements

### Requirement: Committed notification parks retain a bounded review reference
A Messenger-routed notification blocked by a successful recipient-gate park SHALL retain a bounded, server-derived same-action review reference through its typed refusal and the existing Switchboard failed-notification persistence. The reference SHALL contain only the canonical action UUID and actual registered owning Messenger source, SHALL be separate from caller metadata and exception text, and SHALL be admitted only after the owning park commit and same-request routed response validation. Failed, rolled-back, absent, malformed, wrong-source or uncertain park evidence MUST NOT fabricate a reference. The notification SHALL remain blocked with all existing role, actor, privacy, authorization, delivery and recovery boundaries intact.

ID: REQ-core-notify-032
Source: Closed owner release bu-3b6goa; run13 dossier ranked move5 S4; parked-notification-approval-review-door/design.md
Scope: v1-mandatory

#### Scenario: A real committed park retains the same action
- **WHEN** Messenger's pool-scoped recipient gate commits a pending action and blocks the routed notification
- **THEN** its validated same-request refusal carries that admitted action UUID and registered Messenger source
- **AND** the ordinary Switchboard failed row stores the same reference through its dedicated writer-owned correlation field
- **AND** no channel adapter executes and the existing blocked delivery/error and permission boundaries remain intact

#### Scenario: Park failure or rollback cannot fabricate a door
- **WHEN** the recipient park fails, rolls back, returns no durable action or loses its response before a validated refusal is received
- **THEN** no notification review reference is admitted
- **AND** the failure or uncertainty remains truthful without fabricating a pending action, successful delivery or review door

#### Scenario: Caller metadata and error text are not correlation authority
- **WHEN** a notify input, ordinary metadata, legacy stored metadata or error text contains an apparent action ID, source or review URL
- **THEN** that input SHALL NOT populate the dedicated review reference
- **AND** malformed, foreign-source, mismatched-request or mismatched-channel response references are refused

#### Scenario: Logging failure cannot undo or repeat the committed park
- **WHEN** the park committed but the subsequent owning failed-notification insert fails
- **THEN** the pending action remains under its existing lifecycle and the logging failure supplies no fabricated persisted reference
- **AND** this correlation performs no rollback of the committed action, provider delivery or automatic re-park/retry

#### Scenario: Replay preserves the actual admission rather than a borrowed action
- **WHEN** the existing park lifecycle returns a duplicate admission or concurrent attempts produce distinct admissions
- **THEN** each admitted reference names only the actual action returned for that request
- **AND** this contract adds no deduplication or inference from another request's action, error or metadata

#### Scenario: Ordinary notify and recovery boundaries survive
- **WHEN** the notification is owner-allowed, rule-allowed, an ordinary failed delivery, a local pending_action_id park or an approval-control/recovery envelope
- **THEN** its existing delivery, tool response, ledger, privacy and recovery-isolation contracts remain intact
- **AND** this routed review correlation adds no action permission, recursive park, synthesized local ledger row or recovery capability
