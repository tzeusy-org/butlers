## ADDED Requirements

### Requirement: Wake-Recovery Egress Action Idempotency
Messenger SHALL admit a wake-recovery release only through a valid,
authenticated `wake_recovery.release.v1` request whose stable action key,
accepted-event reference, run/fence, exact Telegram endpoint/chat/thread target,
and composition-manifest digest match its earlier prepare/commit state. It SHALL
persist the egress action before any Telegram provider call and SHALL keep the
action key distinct from normal content-derived `notify.v1` idempotency when
the protocol's fixed action identity is supplied.

Messenger SHALL persist a send-start marker before the external call and a
provider message ID on confirmed receipt. Repeating a completed action SHALL
return the stored terminal result without another provider call. A crash or
timeout after the send-start marker but before a durable receipt SHALL persist
`egress_ambiguous`, preserve the action evidence, and prohibit automatic resend
until explicit reconciliation establishes the outcome.

Before Messenger persists a wake-recovery egress intent, it SHALL consume the
landed `canonical-dnd-generation-guard` (`bu-12iab`) transaction-bound
admission contract. A changed, active, missing, or unprovable DND generation
authorizes no intent or provider call; an already durable send-start marker
cannot be retracted by a later DND change. Only an all-uncommitted complete
current-fence cohort, after every participant has supplied a compatible durable
prepare result, may use `ordinary_precommit_cancel` through the landed
`durable-precommit-cancellation-admission` (`bu-qs702`) Messenger-local gate;
a partial prepared cohort remains protocol-bound. This requirement does not
duplicate either contract's request fields, locking, receipts, or cancellation
state machine.

#### Scenario: Stable action key returns the first receipt
- **WHEN** Messenger receives a repeated valid release request after recording
  a Telegram provider receipt for the same action key
- **THEN** it returns the original terminal delivery result
- **AND** it does not create another provider attempt or message

#### Scenario: Unknown post-start outcome is not retried
- **WHEN** Messenger restarts after persisting send-start but cannot determine
  whether Telegram accepted the call
- **THEN** it records and returns `egress_ambiguous` for the original action
  key
- **AND** no scheduler, retry worker, or repeated coordinator call sends again
