## ADDED Requirements

### Requirement: Wake-Recovery Precommit Cancellation Admission
Messenger SHALL be the sole final admission authority for a future
precommit-cancellation request after a wake-recovery cohort has prepared and
before any egress intent exists. It SHALL accept only an authenticated
Switchboard `wake_recovery.cancel_admit.v1` request whose run, fence,
participant/cohort digests, release action, cancellation action key, and DND
evidence match its private prepared-release gate.

Messenger SHALL serialize cancellation, commit, and release through that same
private gate. It SHALL use the canonical DND guard only through the RFC 0009
consumer helper and SHALL persist a durable idempotent accepted, rejected, or
ambiguous cancellation receipt before it responds. Messenger SHALL not let a
Health, Scheduler, origin, or provider call bypass this boundary.

For `rejected_blocked_dnd`, Messenger SHALL return only its durable guarded
admission receipt to authenticated Switchboard. Switchboard, not Messenger,
then drives the parent `abort.v1(reason=blocked_dnd)` fanout to origin-local
`release_retained_dnd` state. Messenger SHALL not call an origin, mutate an
origin queue, or expose a DND read surface for that fanout.

Cancellation SHALL remain deterministic service-plane work under RFC 0038
D1/D2/D7 and SHALL NOT admit a reasoning goal, invoke an LLM or start a session.
Generic route-proxy mediation SHALL be replaced by a named control-plane
service-listener endpoint fixing the cancellation schema, Messenger recipient
operation and authenticated Switchboard caller allowlist. Messenger SHALL
authenticate the current-fence Switchboard service identity independently of
payload source claims and retain every existing gate, cohort, replay and DND
check. The endpoint and recipient operation SHALL NOT be LLM-presentable or
reachable from the session listener; no direct peer caller or grant is added.

ID: REQ-butler-messenger-004
Source: proposal.md; design.md D0–D7; RFC 0038 D1/D2/D7
Scope: v1-mandatory

#### Scenario: Messenger rejects a direct non-Switchboard caller
- **WHEN** Health, an origin Scheduler, or an unauthenticated caller invokes
  the cancellation admission surface directly
- **THEN** Messenger rejects it before reading or changing its release gate
- **AND** it creates no cancellation record, egress intent, or provider attempt

#### Scenario: Messenger preserves no-send-start admission proof
- **WHEN** a matching cancellation is admitted before an egress intent and
  send-start marker exist
- **THEN** Messenger stores an `accepted_precommit` receipt before responding
- **AND** any later same-fence commit or release rejects before a provider call

#### Scenario: Messenger refuses to cancel ambiguous egress
- **WHEN** its private gate records send-start, a provider receipt, or an
  ambiguous provider attempt for the release action
- **THEN** Messenger returns a durable non-accepted outcome
- **AND** it neither returns scheduler work nor automatically resends the action
