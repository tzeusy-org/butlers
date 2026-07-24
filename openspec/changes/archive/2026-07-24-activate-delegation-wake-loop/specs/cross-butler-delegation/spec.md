## ADDED Requirements

### Requirement: Delegated Answer Wake Contract

A valid delegated answer SHALL remain a durable fact in
public.delegation_ledger while a separate wake disposition records the return
path to the original asker. The existing answered status SHALL mean that the
assigned target's first answer was accepted; it SHALL NOT be replaced with a
callback or task failure status.

On first valid answer acceptance, the ledger SHALL persist, atomically with the
answer identity, an immutable SHA-256 digest of the stored answer, a wake key
of delegation-wake:v1:<ledger_id>:<answer_digest>, and
wake_state=callback_pending. It SHALL retain logical callback-attempt/result
evidence, including timestamp, result/error, and retryability, plus the
asker-local task identity and task ID once known.

The allowed wake states are not_applicable, callback_pending, callback_failed,
callback_routed, task_created, and task_conflict. Existing answered rows that
lack the immutable v1 answer/wake provenance are legacy rows and SHALL remain
discoverable without a fabricated wake.

#### Scenario: First valid target answer enters the callback state

- **WHEN** delegate_answer is called for a routed ledger row by that row's
  authoritative target_butler with a non-empty answer
- **THEN** the row SHALL retain status=answered with its authoritative
  answering_butler, answer, answer digest, and wake key recorded
- **AND** its wake_state SHALL become callback_pending before any callback is
  attempted

#### Scenario: Callback failure is honest partial success

- **WHEN** a valid answered row's Switchboard callback cannot be routed because
  of a transient route or connection failure
- **THEN** the answer and answered status SHALL remain durable
- **AND** the wake_state SHALL become callback_failed with callback evidence
  and a retryable result
- **AND** no return task or user notification SHALL be reported as created

#### Scenario: Duplicate or changed answer cannot create a new wake identity

- **WHEN** delegate_answer is called again for an already answered ledger row,
  including with a different answer body
- **THEN** it SHALL not overwrite the original answer, identities, digest, or
  wake key
- **AND** a changed answer SHALL return an explicit integrity error and create
  neither a callback nor a task

### Requirement: Switchboard-only Delegated Answer Callback

A delegated-answer callback SHALL travel only through Switchboard's existing
route primitive. The target SHALL request a callback only with source_butler
equal to the ledger's authoritative answering_butler, target_butler equal to
the authoritative asking_butler, tool_name=delegate_wake, and args limited to
ledger_id and wake_key.

Before it routes the callback, Switchboard SHALL re-read the ledger and verify
that the row exists, is answered, has matching authoritative target/answering
identities, and has the supplied immutable wake key. Switchboard SHALL use
normal route eligibility for that exact asking butler. It SHALL not make a
direct sibling MCP call, schedule in a sibling schema, or accept callback
authority from an arbitrary target callback payload.

#### Scenario: Valid target callback reaches only the original asker

- **WHEN** the authoritative answering butler requests a callback for an
  answered row with its stored wake key
- **THEN** Switchboard SHALL route only to that row's authoritative
  asking_butler and invoke delegate_wake through the trusted route path
- **AND** neither Switchboard nor the target SHALL create an asker-schema task

#### Scenario: Wrong actor, row, or wake key is rejected before task creation

- **WHEN** a callback names a missing row, a non-answered row, a wrong source,
  a wrong target, or a mismatched wake key
- **THEN** Switchboard and delegate_wake SHALL reject it with no local task
- **AND** the invalid attempt SHALL not advance a successful wake state or
  change the authoritative answer

### Requirement: Asker-owned Deterministic Return Task

delegate_wake SHALL be a server-to-server endpoint callable only through the
trusted Switchboard route path. It SHALL independently re-read the ledger,
verify that its local butler name equals asking_butler, verify the answered
target/answering identities and wake key, and create or reconcile work only in
its own schema.

The one logical return task SHALL be named delegate-return-<ledger_id>. Its
metadata SHALL bind ledger_id, wake_key, answer_digest, and
source=delegation_return. The ledger SHALL record the authoritative asking
butler, deterministic task name, and task ID once the task exists. The task is
a bounded runtime-created one-shot continuation, not a TOML schedule, a
recurring task, a target-butler task, or a deterministic briefing job.

#### Scenario: Authorized callback creates one asker-local task

- **WHEN** the original asking butler receives an authorized delegate_wake for
  an answered row and no matching local task exists
- **THEN** it SHALL create exactly one local one-shot task named
  delegate-return-<ledger_id>
- **AND** it SHALL record that task's ID and wake_state=task_created without
  writing any sibling schema

#### Scenario: Duplicate delivery and reconnect return the existing task

- **WHEN** the same authorized callback or route reconnect is delivered after
  a matching return task is bound
- **THEN** delegate_wake SHALL return the existing task binding
- **AND** it SHALL not insert another logical task or change the wake key

#### Scenario: Crash after task insert reconciles deterministically

- **WHEN** the asker crashes after inserting the matching local task but before
  recording its ledger task binding
- **THEN** a replay of the same wake key SHALL find that task by deterministic
  name and matching metadata and bind its existing ID
- **AND** it SHALL not insert a second task

#### Scenario: A conflicting deterministic name fails closed

- **WHEN** the asker finds delegate-return-<ledger_id> with a different ledger
  id, wake key, or answer digest
- **THEN** it SHALL preserve the conflict evidence and set or retain
  wake_state=task_conflict
- **AND** it SHALL not replace the task or create a second task automatically

### Requirement: Delegated Content And Wake Policy Are Fenced

delegate_wake SHALL receive only ledger_id and wake_key as callback authority.
It SHALL read the question, answer, and identities from the ledger after
authorization and SHALL treat question and answer content as untrusted
reference data. No callback field or answer text may select a task name,
schedule, SQL target, tool call, recipient, policy decision, or target butler.

The return task prompt may reference the ledger ID and direct the asking
session to retrieve/evaluate the answer, but it SHALL NOT embed delegated text
as executable instructions. Unknown callback fields, including answer copies,
task IDs, target names, owner identity, owner_local_floor, DND state,
recipient, or delivery metadata, SHALL be rejected.

#### Scenario: Untrusted payload cannot steer scheduling

- **WHEN** an otherwise valid callback carries answer text, a task ID, target
  name, recipient, or another unknown field
- **THEN** delegate_wake SHALL reject the payload before scheduling
- **AND** it SHALL not treat any supplied value as authority in place of the
  ledger row

#### Scenario: DND or an owner-local floor cannot become a wake gate

- **WHEN** a valid delegated callback arrives while DND/sleeping is active or
  an owner-local floor is absent, invalid, stale, or not yet reached
- **THEN** only the delegation ledger and callback checks SHALL govern the
  internal return task
- **AND** the protocol SHALL not read or mutate context/delivery policy, create
  a delayed timer, or send a user notification

### Requirement: Callback Replay Is Not Re-gated Or Backfilled

A callback retry SHALL replay the original immutable ledger id, answer digest,
and wake key. It SHALL not re-run catalog resolution, answer selection,
identity resolution, quiet-hours/DND checks, owner-floor evaluation, target
selection, or a fresh scheduling admission decision.

A valid first answer that arrives after the original asking session ended SHALL
still be eligible for its one internal return task. A late duplicate after
task_created is only a replay. A legacy answered row without the v1 immutable
wake provenance SHALL not be auto-woken or backfilled.

#### Scenario: Late answer is propagated without a fresh delegation decision

- **WHEN** the assigned target first answers a routed row after the original
  asker session has ended
- **THEN** the normal answered-to-callback-to-return-task path SHALL run using
  the persisted asker and target identities
- **AND** it SHALL not re-query the catalog or select a new target

#### Scenario: Legacy answer remains discoverable but inert

- **WHEN** an answered ledger row lacks the required v1 answer digest or wake
  key
- **THEN** it SHALL remain readable through the existing ledger discovery
  surface
- **AND** no callback, task, retry, or owner-facing recovery action SHALL be
  inferred from it
