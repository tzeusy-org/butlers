## MODIFIED Requirements

### Requirement: Lifecycle Notification Remediation and Delivery Honesty
The lifecycle notification SHALL identify the credential state and include a dashboard URL ending in `/secrets?focus=<URL-encoded canonical credential key>`. For a User credential whose catalogued provider is OAuth, it SHALL additionally include a remediation line directing the owner to re-authorize from that credential card; non-OAuth credentials SHALL not receive that line. The notification SHALL NOT embed a dashboard-API URL: `DASHBOARD_URL` is the frontend base and the API is reachable only under a separate, deployment-specific mount that is not derivable from it, so a composed `/api/oauth/<provider>/start` link is dead on every path-mounted deployment. The `/secrets` deep link already lands on the card whose re-authorize control starts the same dance.

Lifecycle delivery SHALL use medium-priority owner Telegram delivery and the established notify-boundary order: (1) Switchboard `delivery_preferences` quiet-hours evaluation, (2) owner quiet-hours and context-bus suppression, then (3) recipient resolution and delivery. The first gate defers by placing a `notify.v1` envelope on Switchboard's deferred-notifications queue. Owner-attention policy quiet hours (gate 2) also park rather than drop: every credential the policy holds in one scan SHALL be composed into ONE aggregate `notify.v1` envelope scoped to the quiet window (dedup marker: the window start), released by the ordinary deferred flush at the window end. Each scan SHALL replace the window's pending envelope with the current held set, atomically, so repeated and concurrent scans leave exactly one pending envelope for the window. A parked state SHALL NOT be re-alerted once its envelope is pending or delivered. Context-bus suppression, and a policy window that cannot be resolved, suppress and rely on a later lifecycle scan. Every delivery decision SHALL be recorded to the attention ledger as `delivered`, `deferred`, `suppressed`, or `failed` with a machine-readable reason. A ledger-write failure MUST NOT change the delivery decision or lifecycle-marker behavior it describes; `failed` and `deferred` are never interchangeable.

For a failed transport delivery, or an unexpected error after a complete message and recipient are known, the scan SHALL best-effort enqueue one retry envelope on Switchboard's deferred-notifications queue with a 30-minute backoff (or the already-resolved quiet-hours delivery time). Before enqueueing it SHALL cancel pending envelopes for the same credential's state-independent `/secrets?focus=` fragment, so the latest state supersedes old retries. A later confirmed direct delivery SHALL cancel any remaining pending retry envelope for that fragment. Missing-recipient and pre-resolution failures SHALL be recorded as `failed` without a malformed retry envelope.

#### Scenario: The owner gets an actionable OAuth remediation message
- **WHEN** `u:google` transitions to `expiring` and direct delivery succeeds
- **THEN** the message includes `/secrets?focus=u%3Agoogle`
- **AND** it includes a re-authorize remediation line and no `/api/` URL
- **AND** the attention ledger records `outcome="delivered"` with a `state_transition:expiring` reason

#### Scenario: Quiet hours defer without pretending the transition is delivered
- **WHEN** Switchboard delivery preferences defer the medium-priority lifecycle notification
- **THEN** a Switchboard `notify.v1` envelope is queued for its computed batch delivery time
- **AND** the attention ledger records `outcome="deferred"` with reason `delivery_preferences_quiet_hours`
- **AND** no lifecycle-state marker is appended before a confirmed direct delivery

#### Scenario: Quiet-hours queue failure is failed and retryable
- **WHEN** Switchboard delivery preferences require a medium-priority lifecycle notification to defer, the owner recipient resolves, and the deferred-notifications queue cannot persist its `notify.v1` envelope
- **THEN** the attention ledger records `outcome="failed"`, reason `delivery_preferences_queue_failure_retryable`, and a null `notification_ref`
- **AND** the scan does not deliver directly inside quiet hours, increment its deferred count, or append a lifecycle-state marker
- **AND** a later lifecycle scan remains eligible to retry the transition

#### Scenario: Policy quiet hours park one aggregate envelope per window
- **WHEN** owner-attention policy quiet hours hold two changed credentials across several scans in one window
- **THEN** exactly one pending deferred envelope exists for that window and it names both credentials
- **AND** each scan records `outcome="deferred"` (never `suppressed`) for each held credential with that envelope as `notification_ref`
- **AND** no lifecycle-state marker is appended before a confirmed direct delivery

#### Scenario: A parked state is not re-alerted after the window
- **WHEN** the window's envelope has been delivered by the deferred flush and a later scan sees the same credential state
- **THEN** the scan sends no direct notification for that state

#### Scenario: An unresolvable policy window keeps the legacy drop
- **WHEN** owner quiet hours hold a credential but the policy window cannot be resolved
- **THEN** the attention ledger records `outcome="suppressed"` and no envelope is parked

#### Scenario: A suppression is visible and retriable
- **WHEN** owner quiet hours or an active `dnd` or `sleeping` context signal suppresses the lifecycle notification
- **THEN** the attention ledger records `outcome="suppressed"` with the applicable machine-readable reason
- **AND** no lifecycle-state marker is appended, so the next scan remains eligible to retry

#### Scenario: A transport outage records failure and keeps only the latest retry
- **WHEN** direct lifecycle delivery returns a transport failure for a credential that already has a pending retry envelope
- **THEN** the prior pending envelope matching that credential's `/secrets?focus=` fragment is cancelled before the new envelope is queued
- **AND** the attention ledger records `outcome="failed"`, a `delivery_error:<detail>` reason, and the retry envelope reference when enqueueing succeeded
- **AND** no lifecycle-state marker is appended
