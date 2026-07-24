## ADDED Requirements

### Requirement: Strict Post-Commit Owner Telegram Wake Authority
The system SHALL recognize wake-recovery authority only from a durably accepted
Switchboard ingestion event for a non-empty native-text message sent in a
private/direct Telegram-bot chat by the canonical owner's primary Telegram-bot
identity. The accepted-event snapshot SHALL be immutable and contain the
ingestion-event/request ID, dedupe and external-update identifiers, committed
acceptance timestamp, canonical owner and matched identity, exact Telegram
endpoint/chat/thread tuple, direct-native-text proof, normalized-text digest,
and the Owner Attention Policy timezone/floor decision; it SHALL not retain the
message body for this protocol.

The earliest owner-local release floor SHALL be an explicit, valid,
timezone-bound Owner Attention Policy value. An absent, invalid, or not-yet
reached floor SHALL fail closed with no wake run and no deferred trigger; a
later qualifying owner DM may be evaluated again. A raw connector receipt,
Telegram user-client event, group/channel/forum/topic event, forwarded, edited,
service, callback, attachment, caption, HA, OwnTracks, location, cron, broker
catch-up, briefing, or generic context event SHALL NOT create wake authority.

#### Scenario: Accepted direct owner bot text creates one candidate
- **WHEN** Switchboard commits a direct private native-text Telegram-bot event
  from the canonical owner's primary Telegram-bot identity after the configured
  local floor
- **THEN** it persists or returns the immutable accepted-event snapshot and one
  owner/window wake-recovery candidate
- **AND** the candidate refers to the committed ingestion-event ID rather than
  an in-memory connector callback

#### Scenario: Replay returns the same candidate
- **WHEN** the same accepted ingestion event is replayed after a connector or
  coordinator retry
- **THEN** the event uniqueness rule returns the existing candidate/run and
  SHALL NOT allocate a second fence or egress action key

#### Scenario: Non-qualifying input has no release effect
- **WHEN** an event is from a group, a non-owner, a Telegram user client, a
  callback, an attachment/caption, a location source, or arrives before the
  configured local floor
- **THEN** no wake run, queue reservation, context mutation, or Messenger
  egress intent is created

### Requirement: Window-Scoped Fenced All-or-Nothing Cohort
The system SHALL coordinate a wake release through one active durable
`WakeRecoveryRun` claim per canonical owner and canonical policy-timezone
window. The run SHALL have a monotonic owner/window fence, accepted-event
reference, explicit participant-set digest, immutable exact delivery target,
composition-manifest digest, and lifecycle state. It SHALL assign one stable
release action key only after a non-empty compatible manifest is final.
An exact replay of an accepted event SHALL return its existing run, and a stale
or conflicting fence SHALL be rejected by every participant. A later accepted
DM while a run is active SHALL create no second action. Only after a
`blocked_dnd` run is explicitly aborted to a durable `aborted_dnd` outcome,
DND has cleared, and its uncommitted reservations have been released from the
old fence into retained scheduler-ineligible rows may a later accepted direct
owner DM create a higher-fence successor run. That successor SHALL adopt the
complete retained cohort and SHALL NOT expose any of its rows to the ordinary
scheduler.

Every registered v1 quiet-hours-hold origin SHALL participate in prepare, even
when it has zero eligible rows. An unavailable, refusing, mismatched, or
oversized participant SHALL retain the entire cohort and prohibit commit and
egress; it SHALL NOT be omitted. A same-fence retry for unavailable or oversize
state SHALL reuse persisted prepared responses and cutoffs and SHALL NOT add a
late row or allocate a second action. A target mismatch SHALL remain retained
with its exact target evidence and SHALL NOT be repaired by default-owner
re-resolution. An empty compatible cohort SHALL terminate as `empty` without
any Health mutation or Messenger intent.

Only a hold with immutable `owner_attention_quiet_hours` admission provenance,
the matching policy-window key, a fully resolved Telegram target tuple, original
`deliver_at`, and a deterministic origin admission sequence is eligible. The
origin-local prepare transaction SHALL freeze its cohort and cutoff sequence.
Legacy/unmarked rows and rows accepted after that cutoff SHALL remain outside
the current cohort; a late row remains `pending` for a later accepted wake.

#### Scenario: All registered origins must prepare
- **WHEN** one registered origin is unavailable or reports that its selected
  cohort would exceed the fixed v1 composition limit
- **THEN** Switchboard records a retained run reason and retains every
  compatible origin's uncommitted reservation under the same fence
- **AND** no selected row, policy-sleep record, or Messenger egress intent is
  partially committed

#### Scenario: Prepare freezes the local boundary
- **WHEN** an origin completes `wake_recovery.prepare.v1` at cutoff sequence N
- **AND** a matching quiet-hours hold is persisted at sequence N+1 before the
  run commits
- **THEN** the N+1 row remains `pending` and is absent from the current
  composition manifest

#### Scenario: Legacy rows are not guessed into a cohort
- **WHEN** a due deferred notification lacks the required wake-recovery
  provenance
- **THEN** the wake coordinator excludes it from the run
- **AND** its established scheduler behavior remains unchanged

### Requirement: Authenticated Prepare-Commit-Release and DND Safety Boundary
The system SHALL use authenticated, versioned MCP operations through
Switchboard for `wake_recovery.prepare.v1`, `commit.v1`, `abort.v1`, and
`release.v1`; no participant or coordinator SHALL SQL-read another origin's
deferred-notification queue. Every operation SHALL carry the run ID, fence,
owner/window keys, accepted-event reference, participant digest, and correlation
key, persist replay-safe local state, and reject a lower or conflicting fence.
Only an all-uncommitted complete current-fence cohort, after every participant
has supplied a compatible durable prepare result, SHALL permit an
`ordinary_precommit_cancel` to consume the landed
`durable-precommit-cancellation-admission` (`bu-qs702`) MCP boundary rather
than a parent-defined cancellation packet or peer-schema operation. A partial
prepared cohort SHALL remain protocol-bound.

The coordinator SHALL compose only after every prepare response is compatible,
ordering rows deterministically by origin and admission sequence. Every row
SHALL exactly match the accepted event's fully resolved Telegram endpoint,
bot/chat/thread target tuple. `recipient=None`, default-owner re-resolution,
or a differing endpoint/chat/thread SHALL cause the whole run to retain as a
target mismatch. The release envelope SHALL use that explicit target without a
new resolution step.

Health SHALL only supersede its currently active deterministic
Owner-Attention-Policy `sleeping` record for the same policy window and run
fence. It SHALL NOT clear DND, a non-policy sleep record, or any other context.
Explicit DND SHALL be an absolute veto, and a direct Telegram DM SHALL never
clear it. When Health observes DND as active while evaluating its matching
policy-sleep operation, Health SHALL retain that context and block the run
before it proceeds to egress. Wake recovery SHALL consume the landed
`canonical-dnd-generation-guard` (`bu-12iab`, RFC 0009) for every durable
decision that turns inactive DND evidence into a Health policy effect or a
Messenger egress intent. A current generation and database-time inactive DND
state are revalidated in that consumer's local transaction; Switchboard only
carries the evidence through authenticated MCP and no participant gains DND
mutation or peer-schema authority. If a DND writer wins before effective
admission, no egress intent or provider call is authorized and the parent
same-fence `blocked_dnd` / retained path applies. A DND change after a durable
send-start marker cannot retract that external call, but it blocks later or
retry admission.

Only after every participant has supplied a compatible durable prepare result
for the complete current-fence cohort may an all-uncommitted
`ordinary_precommit_cancel` use the landed
`durable-precommit-cancellation-admission` (`bu-qs702`) contract. Its
Messenger-local no-egress admission and complete same-fence participant
finalization/publication are the only route that can return the exact frozen
cohort together to ordinary `pending` through the referenced scheduler-return
transition. A partial prepared cohort remains protocol-bound. Until that
complete result exists, no member becomes `pending`, generic-scheduler-visible,
or partially sendable; DND rejection invokes the parent retained path. The
existing action-key and send-start rules remain the only delivery recovery
boundary after a committed egress action exists.

#### Scenario: Canonical DND guard blocks effective admission
- **WHEN** a canonical DND snapshot is active, changed, missing, or unprovable
  before Health or Messenger completes its relevant durable admission
- **THEN** the consumer fails closed, authorizes no new egress intent or
  provider call, and Health retains policy sleep when its own decision is
  denied
- **AND** the current-fence parent path records `blocked_dnd` / retained
  evidence for the complete uncommitted cohort rather than scheduler work

#### Scenario: Post-prepare cancellation requires complete referenced admission
- **WHEN** an all-uncommitted current-fence `ordinary_precommit_cancel` is
  requested after every participant has supplied a compatible durable prepare
  result for the complete current-fence cohort
- **THEN** it uses only the landed `bu-qs702` admission/finalization/publication
  contract and leaves every member fenced until all matching evidence is durable
- **AND** no subset, late row, generic scheduler scan, or partial send is
  permitted; partial preparation, DND, egress-present, and ambiguous outcomes
  remain protocol-bound

#### Scenario: Exact target mismatch retains all rows
- **WHEN** a prepared row has a different Telegram chat, thread, or bot
  endpoint from the accepted direct owner event
- **THEN** the coordinator records durable `retained_mismatch` and
  `release_retained_mismatch` evidence for the full uncommitted cohort
- **AND** it does not return the rows to scheduler-visible `pending`, substitute
  a default recipient, or send a partial cohort

#### Scenario: Restart resumes only the current fence
- **WHEN** Switchboard, an origin, Health, or Messenger restarts after a
  durable prepare or commit response
- **THEN** replaying the same operation and fence returns the persisted state
- **AND** a stale worker cannot prepare, abort, commit, or release that run

### Requirement: Reason-Specific Abort and Recovery Boundaries
`abort.v1` SHALL atomically persist its reason, run/fence, participant digest,
and every selected row's before/after state before it releases any local
reservation. It SHALL distinguish a zero-cohort pre-prepare cancellation, a
DND block, a retained participant failure, and any committed egress outcome. A
repeated abort at the same fence SHALL return the recorded outcome; a stale or
conflicting fence SHALL not change a row or open a new action.

Cancellation before owner/window claim SHALL create no abort run. A fenced
`ordinary_preprepare_cancel` MAY terminate only a `claimed` or `preparing` run
at its current fence before any participant has durably returned a prepare
result, frozen a cutoff, or moved a row to `release_prepared`, and before DND
or a retained reason has won. It SHALL record terminal `aborted_preprepare`
with an empty cohort audit, change no row state or scheduler eligibility, and
make a late same-fence prepare request return the terminal abort. Once any
participant preparation has durably linearized, the pre-prepare path SHALL be
rejected. Only after every participant has supplied a compatible durable
prepare result for the complete current-fence cohort may an all-uncommitted
`ordinary_precommit_cancel` use the landed `bu-qs702`
admission/finalization/publication workflow; a partial prepared cohort SHALL
remain protocol-bound. Its accepted decision may return only the complete
frozen cohort together after the referenced DND-guarded effective admission. It
SHALL not turn an
individual, late, stale-fence, egress-present, or ambiguous row into `pending`
or a partial send.

| Outcome | Durable run and row state | Required recovery boundary |
|---|---|---|
| No durable prepare result | `ordinary_preprepare_cancel` records current-fence `aborted_preprepare` with an empty cohort audit and no row transition. | No scheduler eligibility changes. Same-fence late prepare/replay returns the terminal abort; a later qualifying accepted event may seek a successor after the claim is released. |
| Post-prepare ordinary cancellation | Only an all-uncommitted complete current-fence cohort, after every participant has supplied a compatible durable prepare result, consumes the landed `bu-qs702` admission, finalization, and publication workflow. | Only a matching accepted DND-guarded admission plus complete same-fence publication may return the exact frozen cohort together to ordinary `pending`; partial, late, stale, egress-present, and ambiguous outcomes remain scheduler-ineligible. |
| DND before commit | Explicit abort records `aborted_dnd`; the full uncommitted cohort becomes `release_retained_dnd`, released from the old fence but retaining its run/fence evidence. | The rows are scheduler-ineligible. A new higher-fence run requires DND clear plus a later qualifying accepted direct owner DM, and it atomically adopts the whole retained cohort. |
| Unavailable, oversize, or target mismatch | `retained_unavailable` / `retained_oversize` may retry only at the same fence using their persisted cutoff and manifest; `retained_mismatch` keeps `release_retained_mismatch` exact-target evidence. An explicit stop records a reason-tagged `aborted_retained` and `release_retained_*` rows. | The rows are scheduler-ineligible. Recovery never adds a late row, omits a participant, creates a second action, or default-resolves a mismatched target. |
| Committed, delivered, or ambiguous egress | Committed rows remain `release_committed` and bound to the stable action key; delivery is immutable `egress_delivered` audit; uncertainty is `egress_ambiguous` with action evidence. | None may become `pending` through abort or a later wake. Same-fence replay returns the committed action/receipt, and ambiguity requires explicit reconciliation with no automatic resend. |

#### Scenario: Post-prepare cancellation stays all-cohort through publication
- **WHEN** an all-uncommitted current-fence `ordinary_precommit_cancel` is
  requested after every participant has supplied a compatible durable prepare
  result for the complete current-fence cohort
- **THEN** every member stays prepared or cancellation-ready and
  scheduler-ineligible until the landed `bu-qs702` contract has a compatible
  accepted admission and complete finalization/publication evidence
- **AND** only that exact cohort may enter ordinary `pending` through the
  referenced scheduler-return transition; a DND rejection uses `aborted_dnd` /
  `release_retained_dnd`

#### Scenario: Pre-durable-prepare cancellation has no cohort side effect
- **WHEN** Switchboard cancels a `claimed` or `preparing` run at its current
  fence before any participant has durably prepared a row or frozen a cutoff
- **THEN** the run becomes `aborted_preprepare` with an empty cohort audit and
  no row-state or scheduler-eligibility change
- **AND** a late same-fence prepare request returns the terminal abort instead
  of reserving a row

#### Scenario: DND abort waits for fresh accepted owner intent
- **WHEN** a DND-blocked run is explicitly aborted
- **THEN** it records `aborted_dnd` and retains every cohort row as
  `release_retained_dnd`, not scheduler-visible `pending`
- **AND** only a later qualifying accepted direct owner DM after DND clears may
  form a higher-fence successor that adopts the complete cohort

#### Scenario: Retained and post-commit cohorts cannot fall back to scheduling
- **WHEN** a run is retained for unavailable, oversize, or mismatch, or has
  reached `release_committed`, `egress_delivered`, or `egress_ambiguous`
- **THEN** an abort or scheduler pass preserves its reason-tagged protocol
  state and fence/action audit
- **AND** it does not make any individual row `pending`, send a partial cohort,
  or re-resolve a target

### Requirement: Stable Egress Idempotency and Ambiguous-Send Recovery
The system SHALL derive one immutable `release_action_key` from the run ID,
fence, accepted event, exact target tuple, and canonical composition-manifest
digest. The same key SHALL traverse the run, origin commit receipts, composed
`notify.v1` metadata, Messenger delivery intent, provider-attempt record,
provider receipt, and audit references; retry paths SHALL NOT mint a new key.

Messenger SHALL durably persist the egress action before an external Telegram
call. A restart before the durable send-start marker MAY resume the same action.
A confirmed provider result SHALL persist the provider message ID and make
repeated release calls return that terminal result without another provider
call. A crash or timeout after the send-start marker without a durable receipt
SHALL become `egress_ambiguous`; it SHALL preserve evidence and require
explicit reconciliation, not automatically resend.

#### Scenario: Repeated release has one confirmed provider send
- **WHEN** Messenger receives repeated `wake_recovery.release.v1` calls with
  the same valid release action key after it has persisted a provider receipt
- **THEN** it returns the existing terminal receipt
- **AND** it makes no second Telegram provider call

#### Scenario: Post-start timeout fails closed
- **WHEN** Messenger loses process or transport certainty after its durable
  send-start marker but before it persists a Telegram receipt
- **THEN** it records `egress_ambiguous` with the same action key and evidence
- **AND** ordinary retries, the scheduler, and a new wake event cannot blindly
  invoke another provider send for that action
