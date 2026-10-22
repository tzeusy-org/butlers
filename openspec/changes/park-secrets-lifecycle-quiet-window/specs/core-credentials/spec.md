## MODIFIED Requirements

### Requirement: Lifecycle Notification Remediation and Delivery Honesty
The lifecycle notification SHALL identify the credential state and include a dashboard URL ending in `/secrets?focus=<URL-encoded canonical credential key>`. For a User credential whose catalogued provider is OAuth, it SHALL additionally include a remediation line directing the owner to re-authorize from that credential card; non-OAuth credentials SHALL not receive that line. The notification SHALL NOT embed a dashboard-API URL: `DASHBOARD_URL` is the frontend base and the API is reachable only under a separate, deployment-specific mount that is not derivable from it, so a composed `/api/oauth/<provider>/start` link is dead on every path-mounted deployment. The `/secrets` deep link already lands on the card whose re-authorize control starts the same dance.

Lifecycle delivery SHALL use medium-priority owner Telegram delivery and the established notify-boundary order: (1) Switchboard `delivery_preferences` quiet-hours evaluation, (2) owner quiet-hours and context-bus suppression, then (3) recipient resolution and delivery. The first gate defers by placing a `notify.v1` envelope on Switchboard's deferred-notifications queue. Owner-attention policy quiet hours (gate 2) also park rather than drop: every credential the policy holds in one scan SHALL be composed into ONE aggregate `notify.v1` envelope scoped to the quiet window (dedup marker: the window start), released by the ordinary deferred flush at the window end. Each scan SHALL replace the window's pending envelope with the current held set, atomically, so repeated and concurrent scans leave exactly one pending envelope for the window. A parked credential state SHALL NOT be re-alerted while the envelope that carried that credential is pending or delivered and the parked marker is still the newest lifecycle marker for the credential (see Transition-Debounced Lifecycle Delivery). Context-bus suppression, and a policy window that cannot be resolved, suppress and rely on a later lifecycle scan. Every delivery decision SHALL be recorded to the attention ledger as `delivered`, `deferred`, `suppressed`, or `failed` with a machine-readable reason. A ledger-write failure MUST NOT change the delivery decision or lifecycle-marker behavior it describes; `failed` and `deferred` are never interchangeable.

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
- **AND** an owner quiet-hours policy hold whose window resolves is parked, not suppressed (see "Policy quiet hours park one aggregate envelope per window"), so the owner quiet-hours case of this scenario applies only when the window cannot be resolved

#### Scenario: A transport outage records failure and keeps only the latest retry
- **WHEN** direct lifecycle delivery returns a transport failure for a credential that already has a pending retry envelope
- **THEN** the prior pending envelope matching that credential's `/secrets?focus=` fragment is cancelled before the new envelope is queued
- **AND** the attention ledger records `outcome="failed"`, a `delivery_error:<detail>` reason, and the retry envelope reference when enqueueing succeeded
- **AND** no lifecycle-state marker is appended

### Requirement: Audit Action Vocabulary for Credential Lifecycle
The `public.audit_log.action` column is unconstrained `TEXT`; this requirement defines the credential-lifecycle action vocabulary that writers SHALL use:

| Action | Used by |
|---|---|
| `verified` | Probe success |
| `failed` | Probe failure |
| `rotated` | Value replaced (User rotate, System set on existing key, CLI rotate) |
| `connected` | OAuth dance completed successfully |
| `disconnected` | Credential explicitly disconnected (User disconnect, CLI revoke) |
| `warned` | Scope mismatch or expiring-soon detected during probe |
| `overrode` | System override created (per-butler `butler_secrets` row added) |
| `revoked` | System override removed (per-butler `butler_secrets` row removed via `DELETE /api/secrets/system/<key>?target=<butler>`) |
| `attempted` | OAuth dance initiated (begin endpoint called) but not yet completed |
| `set` | New System secret created (first-time `POST /api/secrets/system/<key>`) |
| `lifecycle_state_notified` | A direct proactive lifecycle notification was delivered for an attention state |
| `lifecycle_state_parked` | A held lifecycle alert was parked in a quiet-window deferred envelope (`note=<state>\|<envelope id>`); it never advances the delivery debounce marker |

Because `public.audit_log.action` has no enum or check constraint, this specification is the authoritative action vocabulary; an action addition requires no enum or constraint migration.

#### Scenario: All mutation endpoints write audit rows with new actions
- **WHEN** any `/api/secrets/*` mutation endpoint completes successfully
- **THEN** an `audit_log` row is appended with `actor = "owner"` (single-owner system), `action = <appropriate vocabulary value above>`, `target = <canonical credential key>`, and `note = <stored prose; never LLM-generated>`

### Requirement: Transition-Debounced Lifecycle Delivery
The lifecycle scan SHALL use the canonical credential key as its delivery identity and compare the current attention state with the most recent `public.audit_log` row whose `action` is `lifecycle_state_notified` for that key. If the most recently delivered state equals the current state, the scan SHALL skip the delivery path. A different current attention state SHALL be eligible for another notification; a return to a previously notified state is still skipped when that state remains the latest delivered marker. This delivered-state skip applies only while the newest of the credential's `lifecycle_state_notified` and `lifecycle_state_parked` rows is the notified row; a newer parked row supersedes it, so the state is eligible again.

Only a confirmed direct delivery MAY advance the marker: it appends `action="lifecycle_state_notified"`, `target=<canonical credential key>`, and `note=<delivered state>`. Deferred, suppressed, failed, and marker/audit-write-error paths SHALL leave the marker unadvanced so a later scan can retry instead of silently losing the condition. If the marker lookup fails, the scan SHALL treat the credential as not yet notified rather than suppressing a potentially actionable alert.

A credential state parked in a quiet-window envelope (`action="lifecycle_state_parked"`, `note=<state>|<envelope id>`) is a second, narrower skip rule: the scan SHALL skip the direct delivery only when that parked row is the NEWEST of the credential's `lifecycle_state_notified` and `lifecycle_state_parked` rows, its state equals the current state, and the envelope it names is pending or delivered. A later confirmed direct delivery therefore supersedes the parked row, and a credential absent from a later aggregate names a cancelled envelope and falls through to a direct delivery. A parked row never advances the delivery marker.

This read-then-write debounce SHALL be operated with one dashboard-API replica. It does not provide a cross-replica claim or uniqueness guarantee; horizontal dashboard-API scaling requires a new concurrency contract before this lifecycle scan can be replicated.

#### Scenario: One delivered attention state does not repeat every scan
- **WHEN** the latest lifecycle marker for `u:google` has `note="expiring"` and the current state is `expiring`
- **THEN** the scan sends no notification and records no new attention-ledger outcome for that credential

#### Scenario: A later attention state is delivered
- **WHEN** the latest lifecycle marker for a canonical credential key is `expiring` and the current state is `expired`
- **THEN** the scan attempts an `expired` notification
- **AND** after confirmed direct delivery with a successful audit write it appends a new marker with `note="expired"`
