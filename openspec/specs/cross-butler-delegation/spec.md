# Cross-Butler Delegation

## Purpose

Defines the cross-butler delegation ledger: how one butler asks a question
that another butler's domain covers, how the target is resolved, how routing
happens, and how the answer is recorded and discovered. Sequenced after the
`memory_catalog` default-on flip (bu-qvnce.15) per the 2026-07-04 JARVIS
pursuit dossier (`docs/redesigns/2026-07-04-jarvis-pursuit.md`). bu-gxmfx
implements v1: post question -> resolve -> route -> record answer -> discover.
## Requirements
### Requirement: Ledger Of Record
Every delegated question SHALL be recorded as exactly one row in
`public.delegation_ledger`, identified by a durable `id`. A question is never
silently dropped: every terminal outcome (`unroutable`, `failed`, `answered`)
and the in-flight state (`pending`, `routed`) SHALL be persisted and readable
back via `ledger_id`.

#### Scenario: Every ask produces a ledger row
- **WHEN** a butler calls `delegate_ask` with a non-empty question
- **THEN** exactly one `public.delegation_ledger` row is created with that
  question and the asking butler's name, and its `id` is returned to the
  caller as `ledger_id`

#### Scenario: A write failure is never silently absorbed
- **WHEN** the ledger insert or a status transition fails at the database
  layer
- **THEN** the failure SHALL propagate to the caller as an explicit error
  response rather than being swallowed or reported as a false success

### Requirement: Domain Resolution Via Shared Catalog
"Whose domain covers this question" SHALL be resolved by querying
`public.memory_catalog` (the same shared discovery index that backs
`GET /api/memory/catalog/search` / Fleet Knowledge search) via a hybrid
(semantic + full-text) search over the question text. No parallel or
butler-local relevance index SHALL be introduced for this resolution.

#### Scenario: Top catalog hit names the target
- **WHEN** `delegate_ask` runs a hybrid catalog search for the question and
  the top hit's `source_butler` (or `source_schema` when `source_butler` is
  unset) names a butler other than the asker
- **THEN** that butler is recorded as the ledger row's `target_butler` and
  dispatch is attempted

#### Scenario: No catalog match is unroutable
- **WHEN** the catalog search returns no hits for the question
- **THEN** the ledger row is recorded with `status = 'unroutable'` and
  `reason = 'no_catalog_match'`, `target_butler` is left null, and no dispatch
  is attempted

#### Scenario: Self-delegation is refused
- **WHEN** the resolved target butler is the same butler that asked the
  question
- **THEN** the ledger row is recorded with `status = 'unroutable'` and
  `reason = 'self_target'`, and no dispatch is attempted

### Requirement: Routing Goes Through The Switchboard
Dispatch of a resolved delegated question to its target butler SHALL go
through the Switchboard's existing `route()` primitive (the same routing
function used by `post_mail`, `correct_route`, and `route_to_butler`) --
never a bespoke point-to-point dispatch path.

#### Scenario: Non-Switchboard asker dispatches via the Switchboard MCP client
- **WHEN** a non-Switchboard butler's `delegate_ask` has resolved a target
  butler
- **THEN** dispatch calls the Switchboard's `route` MCP tool (via the
  butler's `switchboard_client`) with `target_butler`, `tool_name =
  "delegate_receive"`, and the ledger id, question, and asking butler as args

#### Scenario: Switchboard asking itself dispatches in-process
- **WHEN** the Switchboard butler itself calls `delegate_ask`
- **THEN** dispatch calls the underlying `route()` function directly
  in-process (no MCP round trip to itself), with the same routing/eligibility
  checks as any other route dispatch

#### Scenario: A dispatch failure is recorded honestly
- **WHEN** the Switchboard `route()` call errors (target unreachable, stale,
  quarantined, or the tool call itself fails)
- **THEN** the ledger row transitions to `status = 'failed'` with the error
  recorded in `reason`, and the response to the asking butler names the
  failure and marks it `retryable` when the failure looks transient
  (timeout, connection error, Switchboard not connected)

### Requirement: Answering Is Asynchronous And Guarded
Receiving a delegated question SHALL NOT block the dispatching Switchboard
`route()` call on an LLM run. The target butler SHALL be woken to answer via
a scheduled one-shot task, and posting the answer SHALL only succeed for the
row's actual assigned target.

#### Scenario: Receipt schedules an answering task without blocking
- **WHEN** `delegate_receive` is called for a `'pending'` ledger row whose
  `target_butler` matches the receiving butler
- **THEN** it schedules a one-shot task instructing the butler's next spawned
  session to answer and call `delegate_answer`, and returns without waiting
  for that session to run

#### Scenario: Duplicate receipt is not re-scheduled
- **WHEN** `delegate_receive` is called again for a ledger row that is no
  longer `'pending'` (already `'routed'` or `'answered'`)
- **THEN** no new task is scheduled and the response reports the row's
  existing status rather than silently no-op'ing

#### Scenario: Only the assigned target may answer
- **WHEN** `delegate_answer` is called with a `ledger_id` whose row's
  `target_butler` does not match the calling butler, or whose `status` is
  not `'routed'`
- **THEN** no answer is recorded and an explicit error is returned -- never a
  fabricated success

#### Scenario: A valid answer completes the ledger row
- **WHEN** `delegate_answer` is called by the correct `target_butler` for a
  `'routed'` row
- **THEN** the row transitions to `status = 'answered'` with `answer`,
  `answered_at`, and `answering_butler` recorded

### Requirement: Discoverability
Every delegation-ledger row SHALL be discoverable outside the asking and
answering butlers' own sessions, without a per-butler fan-out (the ledger is
one shared `public` table reachable from any pool).

#### Scenario: List recent delegations
- **WHEN** a caller requests `GET /api/delegation/ledger` with optional
  `status`, `asking_butler`, or `target_butler` filters
- **THEN** matching rows are returned most-recent-first with pagination
  metadata, without fanning out to multiple butler pools

#### Scenario: Fetch one delegation by id
- **WHEN** a caller requests `GET /api/delegation/ledger/{id}` for an
  existing row
- **THEN** the full row (including `answer` once present) is returned

#### Scenario: Unknown id is a 404, not an empty success
- **WHEN** a caller requests `GET /api/delegation/ledger/{id}` for an id with
  no matching row
- **THEN** the response is `404 Not Found`, never a `200` with null/empty
  data standing in for "not found"

#### Scenario: Wake-protocol fields are discoverable, not just the answer (bu-ep4ks.3)
- **WHEN** `GET /api/delegation/ledger` or `GET /api/delegation/ledger/{id}`
  returns a row
- **THEN** the response includes `wake_state`, `wake_key`, `wake_task_id`,
  `wake_task_name`, `wake_updated_at`, and `answer_digest` alongside the
  existing fields, so `callback_failed` and `task_conflict` -- the two
  failure states the wake protocol introduces -- are distinguishable from an
  ordinary answered row over the API, not only via direct database access
- **AND** a row with no v1 wake provenance defaults `wake_state` to
  `"not_applicable"` rather than omitting the field

#### Scenario: Filtering to stuck wake states without a fleet-wide scan
- **WHEN** a caller requests `GET /api/delegation/ledger?wake_stuck=true`
- **THEN** only rows whose `wake_state` is `callback_failed` or
  `task_conflict` are returned, combinable with the existing `status`,
  `asking_butler`, and `target_butler` filters

#### Scenario: Delegation rows are visible on butler detail and the attention surface
- **WHEN** the dashboard renders a butler's detail page or the Overview
  attention list
- **THEN** the butler detail page shows delegated-out and delegated-in rows
  for that butler, with a visually distinct badge for `callback_failed` and
  `task_conflict` rows
- **AND** any fleet-wide row stuck in `callback_failed` or `task_conflict`
  surfaces on the Overview attention list, deep-linking to the asking
  butler's detail page
- **AND** a failed fetch of stuck delegations renders a named degraded
  notice, never a silent "nothing stuck" all-clear

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

delegate_wake SHALL enforce its admission boundary through ledger
re-verification rather than through the caller channel, even though in normal
operation it is reached only through the trusted Switchboard route path. The
framework has no LLM-hidden-but-registered tool tier (known framework
limitation — see core-daemon's "Delegation Core Tool Inventory And Admission
Boundary"), so delegate_wake is necessarily registered as an ordinary
LLM-visible MCP tool and no admission-layer signal distinguishes a
Switchboard-routed call from a direct same-butler invocation. It SHALL
independently re-read the ledger, verify that its local butler name equals
asking_butler, verify the answered target/answering identities and immutable
wake key, and create or reconcile work only in its own schema. A direct or
forged call that fails any of those checks SHALL be rejected with no local
task created and no sibling-schema write.

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

