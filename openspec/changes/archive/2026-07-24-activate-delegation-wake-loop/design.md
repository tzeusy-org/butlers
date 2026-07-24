## Context

The current delegation ledger makes a target answer durable, but an answered row
has no authorized path back to the asking butler. A target must not create or
mutate a task in the asker's schema, and an asking butler must not accept
arbitrary callback text as proof that another butler answered its question.

The governing boundaries are deliberately narrow:

- Doctrine Rule 3 and RFC 0003 require interactive inter-butler communication
  to use MCP through Switchboard.
- RFC 0006 keeps each daemon's scheduled_tasks table in its own schema. The
  shared public.delegation_ledger is a record of delegation, not permission for
  sibling-schema writes.
- RFC 0001 permits runtime-created, one-shot scheduled tasks while preserving
  daemon-local scheduler ownership and crash recovery semantics.
- RFC 0002 requires an explicit core-tool inventory and server-to-server
  authorization boundary.
- RFC 0010 permits only a read-only, batch briefing aggregation exception. It
  does not authorize a real-time delegation path or any write across schemas.

This design defines the contract for follow-on implementation. It creates no
database record, migration, task, config change, schedule, callback, or
notification in this change.

## Goals / Non-Goals

**Goals:**

- Define the only v1 return path: delegate_answer to Switchboard-only callback
  to asker-owned delegate_wake to one deterministic asker-local return task.
- Preserve authoritative identity and answer provenance in the existing
  delegation ledger while making callback/task progress durable and observable.
- Make every retry, reconnect, duplicate, wrong-row, and crash path converge
  on at most one logical return task.
- Treat delegated question and answer text as untrusted reference content, not
  callback authority or executable task instructions.
- Keep both user delivery and RFC 0010's briefing aggregation outside this
  protocol.

**Non-Goals:**

- Runtime source, migrations, data changes, schedule/config activation, live
  runtime patching, or implementation of any described persistence.
- Direct sibling-schema reads, writes, task creation, calls, DSN sharing, or
  grants outside the existing shared ledger and Switchboard MCP route.
- User notification, Messenger/Telegram egress, owner identity recovery,
  quiet-hours/DND recovery, owner-local release floors, or a delayed wake
  timer. Those belong to the separate strict owner-Telegram recovery protocol
  in PR #3513 and are not a dependency of this change.
- Briefing-composer/envelope work, briefing/daily state writes, combined-brief
  joins, catalog ingestion, delegated QA, subscriptions, or standing work.
- A new scheduler-core primitive, generic callback bus, or a claim of exactly
  once runtime-session execution. This contract guarantees one logical task
  record, subject to the scheduler's existing execution semantics.

## Decisions

### D1 - The durable ledger is the authority; wake disposition is orthogonal to answer status

public.delegation_ledger remains the sole cross-butler source of truth for a
delegated question. The persisted asking_butler, target_butler, and first
accepted answering_butler values are authoritative identities. A callback
payload, MCP caller argument, catalog result, current roster state, or LLM text
MUST NOT replace them.

The existing status=answered means only that a valid target answer has been
durably accepted. It MUST remain true when the callback or local-task step
fails. A future implementation SHALL model the following logical wake data
alongside that status; the physical representation may be columns and/or a
ledger-owned attempt record, but it must preserve these semantics:

| Logical data | Required meaning |
|---|---|
| answer_digest | Immutable digest of the first stored answer for replay comparison. A later answer with different content is not a revision. |
| wake_key | Immutable delegation-wake:v1:<ledger_id>:<answer_digest> identity for all callback/replay attempts. |
| wake_state | One of not_applicable, callback_pending, callback_failed, callback_routed, task_created, or task_conflict. |
| callback attempt/result | Attempt identity, timestamp, Switchboard route result, error class/detail, and retryability. Invalid attempts must be auditable without claiming success. |
| task binding | Asking butler name, deterministic task name, task ID once known, and task-creation/reconciliation timestamp. |

not_applicable applies to rows that never reached a v1 answer. An existing
answered row that lacks immutable answer/wake provenance is a legacy row: it
remains readable but MUST NOT be guessed, backfilled, or auto-woken.

**Alternative considered:** replace answered with a larger status sequence such
as answer_pending_callback and wake_complete. Rejected because it would erase
the durable fact that an answer exists whenever delivery is temporarily
unavailable, contradicting the ledger's degraded-honesty contract.

### D2 - First-answer acceptance creates one immutable callback identity

delegate_answer MUST accept an answer only when the ledger row is routed and the
calling butler equals the row's authoritative target_butler. Its successful
transaction persists the answer, answered_at, answering_butler, answer_digest,
and wake_key together, then sets wake_state=callback_pending.

The first accepted answer wins. A second delegate_answer call for the same row,
including a duplicate, reconnect, or a changed answer, MUST NOT overwrite the
original answer, digest, identities, or wake key. A changed answer MUST return
an explicit integrity error and MUST schedule nothing. A duplicate of the same
answer can return the existing durable result but MUST NOT create a second
callback identity.

A callback retry is a replay of the immutable wake_key, not a second answer
submission. If a Switchboard route attempt fails, the answer stays durable,
wake_state=callback_failed records its result, and the caller receives an
honest partial-success response. Transient route failures are retryable; an
authorization, identity, or task-identity conflict is not made retryable by
blind repetition. Any retry uses the same ledger id, answer digest, and wake
key and may not create standing retry work or a scheduler-core mechanism.

**Alternative considered:** make the target wait for the asker's task creation
before committing the answer. Rejected because the two schemas cannot share an
atomic transaction; an unavailable asker must not make a valid answer vanish.

### D3 - Switchboard is the sole callback broker and independently re-authorizes it

After durable first-answer acceptance, the target invokes only Switchboard's
existing route() path. The callback request has the fixed shape below; it
contains no answer, question, owner, recipient, timing, or task prompt data.

    target delegate_answer
      -> Switchboard route(
           source_butler = authoritative answering_butler,
           target_butler = authoritative asking_butler,
           tool_name = delegate_wake,
           args = {ledger_id, wake_key}
         )
      -> asking butler delegate_wake(ledger_id, wake_key)

Before routing, Switchboard MUST read the durable ledger row and verify all of
the following:

1. the row exists and is answered;
2. the requested source equals its answering_butler and its authoritative
   target_butler;
3. the requested target equals its asking_butler;
4. the supplied wake key equals the row's immutable wake key; and
5. normal Switchboard route eligibility accepts that exact target.

Switchboard MUST reject a caller that supplies a mismatched ledger id, source,
target, answer digest, or wake key. It MUST NOT invoke delegate_wake on a
non-authoritative target, issue a direct MCP call between siblings, or schedule
on behalf of either domain butler.

delegate_wake is a server-to-server endpoint. It MUST accept calls only from
the trusted Switchboard route path and repeat the ledger lookup and local
identity checks before any task operation. A direct caller, target butler,
wrong asker, non-answered row, missing answer provenance, or mismatched wake
key is rejected with no local task and no successful wake state transition.

**Alternative considered:** allow the target to call delegate_wake directly
because it already knows the asker. Rejected because it bypasses the only
inter-butler broker, removes central route authorization, and invites a direct
sibling-schema scheduling path.

### D4 - Callback content is fenced as untrusted reference data

The callback contains only ledger_id and wake_key. The asker MUST obtain the
answer, question, and identity values by re-reading the authoritative ledger
row after authenticating the Switchboard route. It MUST treat question and
answer text as untrusted reference content:

- no payload field may choose a butler, task name, schedule, tool, recipient,
  policy decision, or SQL target;
- neither text may be concatenated into a scheduler command or treated as an
  instruction to invoke a tool; and
- the created task's bounded prompt may reference the ledger id and tell the
  asking session to retrieve and evaluate the answer, but must not turn the
  answer itself into executable control text.

Unknown callback fields, including an answer copy, target name, task id,
owner_local_floor, owner identity, DND state, recipient, or delivery metadata,
are outside delegate_wake.v1 and MUST be rejected rather than silently becoming
authority. This is an intentional untrusted-data fence, not an extension point.

### D5 - Only the asker creates the deterministic local one-shot task

Once delegate_wake has re-authorized the row, only the local asking butler may
reconcile a runtime-created one-shot task. Its fixed logical identity is:

    task name: delegate-return-<ledger_id>
    task owner/schema: authoritative asking_butler
    task metadata: {ledger_id, wake_key, answer_digest, source=delegation_return}

The task is a bounded internal continuation. It is not a TOML schedule,
deterministic-job registry entry, recurring task, task in the target schema,
or owner-facing notification. It is created only after the answer/callback
checks above; it performs no catalog re-resolution, target re-selection, or
delivery-policy check.

Task insertion and the shared-ledger task binding cannot share a transaction.
The asker therefore uses deterministic reconciliation:

1. look up a local task by deterministic name and metadata before insert;
2. if no task exists, insert the one local task and then persist its ID and
   wake_state=task_created on the shared ledger;
3. if the process crashes after local insert but before the ledger update, a
   replay finds the matching local task, attaches its ID, and completes the
   same state transition; and
4. if a task with the deterministic name has a different ledger id, wake key,
   or answer digest, set/retain wake_state=task_conflict, preserve evidence,
   and never create a substitute task automatically.

If the ledger already records task_created, every duplicate/reconnect returns
the existing binding. A later absence of an old one-shot task is not proof that
a new one is needed: it may have fired or been retained/deleted under normal
scheduler lifecycle. The protocol MUST NOT create a second logical task from
that absence alone.

**Alternative considered:** use a random task name and rely on a unique ledger
update to deduplicate. Rejected because a crash between the local insert and
the shared update would leave an unfindable task and permit a duplicate retry.

### D6 - State and transition matrix separates answer truth from callback progress

| Ledger status | Wake state | Entry condition | Allowed transition | Invariant |
|---|---|---|---|---|
| pending, routed, unroutable, or failed | not_applicable | No valid first answer exists | only a valid routed target answer may enter callback_pending | No callback or return task exists. |
| answered | callback_pending | First valid target answer and immutable wake key committed | callback_routed or callback_failed | Answer and identities never change. |
| answered | callback_failed | Switchboard route did not obtain an acceptable callback result | same-key replay to callback_routed, or remain failed | Partial success is visible; no sibling task or fabricated completion exists. |
| answered | callback_routed | Switchboard accepted the exact callback and asker is processing/reconciling it | task_created, task_conflict, or retryable reconciliation of the same key | Only the authoritative asker may inspect/create its local task. |
| answered | task_created | Matching deterministic local task exists and its binding is durable | terminal for logical task creation | All duplicates return the same task ID; no second logical task is inserted. |
| answered | task_conflict | Deterministic name resolves to incompatible local metadata | explicit repair/reconciliation using the same immutable evidence only | No automatic replacement task is created. |
| legacy answered | legacy/no wake provenance | Row predates the protocol or lacks required immutable fields | none automatically | Discoverability remains; no retroactive wake is inferred. |

Invalid callback attempts are separately auditable but do not erase a valid
answer or advance this matrix. In particular, an invalid or wrong-row attempt
cannot turn an unanswered row into answered, and a changed answer cannot turn
task_created back into a pending state.

### D7 - DND, owner-local floors, late rows, and re-gating are explicitly out of the wake loop

The delegated return task is internal work; it has no user egress. Therefore:

- An active DND/sleeping context is neither read nor written by
  delegate_answer, Switchboard callback handling, or delegate_wake. It does
  not suppress, delay, unblock, or mutate the return task.
- There is no owner-local release floor in delegate_wake.v1. A missing,
  invalid, stale, or not-yet-reached owner-local floor cannot authorize,
  reject, delay, or create a task. A supplied floor field is rejected as an
  unknown payload field under D4.
- No quiet-hours policy, deferred-notification row, recipient resolution,
  Messenger call, or notify() invocation is made. The protocol must not become
  a hidden wake-recovery or user-notification path.
- Once a valid answer/wake key is accepted, retries re-use that exact evidence;
  they MUST NOT re-run catalog resolution, answer selection, identity
  resolution, policy/DND checks, or a fresh scheduling admission gate.

An answer arriving after the asker's original session ended is still eligible
for its one internal return task if it satisfies D2-D5. A late duplicate after
task_created is only a replay. A legacy answered row without v1 provenance is
not a late row to be auto-recovered; it stays discoverable only. These rules
prevent both dropped work and inappropriate import of PR #3513 semantics.

### D8 - The RFC 0010 briefing exception remains closed

This protocol neither reads nor writes general.v_briefing_contributions, any
specialist briefing/daily state key, a combined briefing, or a briefing
envelope. A delegated return task cannot be substituted for a specialist
contribution job and does not gain RFC 0010's narrow cross-schema read
exception.

The later bounded Relationship-to-Finance seed may use the normal
delegate_ask/Switchboard route once this protocol is implemented and activated.
It must remain an independent producer: it may not modify same-day composition,
use direct Finance access, or cause owner egress merely because a return task
exists.

### D9 - Follow-on boundaries are serial and non-overlapping

| Child | Owns | Must use from this change | Must not add |
|---|---|---|---|
| bu-27dxl.5.2 | Ledger representation, grants, callback adapter, delegate_wake, deterministic task reconciliation, focused integration tests | D1-D7 and the cross-butler delegation/core-daemon delta sections | Core-group activation, guidance, briefing seed, direct schema access, user delivery, or scheduler-core changes. |
| bu-27dxl.5.3 | Runtime-config validation/activation, Finance and Relationship effective configuration, shared guidance and MCP inventory proof | D3-D5 and the core-daemon inventory section | Ledger/callback semantics, briefing seed, dashboard/UI, or user delivery. |
| bu-27dxl.5.4 | One deterministic Relationship-to-Finance briefing producer and focused tests | D7-D8 and the briefing-contribution exclusion section | Composer/envelope changes, direct Finance access, quiet-window recovery, or user notification. |

The sequence is representation and propagation (.5.2), activation and
discoverability (.5.3), then bounded producer enforcement (.5.4). It is not an
authorization to merge the separate PR #3513 protocol or to implement
unbounded delegated workflows.

## Verification Matrix

| Contract area | Required future proof | Negative proof |
|---|---|---|
| First-answer authority | Migrated-DB/unit test accepts only the persisted target on a routed row and snapshots the wake key | Wrong actor/status, duplicate, and changed answer leave the row/task unchanged. |
| Switchboard callback | Route test verifies source/target/key against the ledger before delegate_wake | Direct sibling call, wrong source/target/key, and missing row invoke no local task. |
| Untrusted-data fence | Tool test schedules only a fixed prompt/metadata from a ledger reread | Payload answer, task id, owner/floor, recipient, or unknown fields cannot steer scheduling. |
| Replay and crash recovery | Integration test simulates insert-before-ledger-update crash and reconciles the same task | Duplicate/reconnect never creates a second task; incompatible deterministic name becomes conflict. |
| Failure honesty | Route error test leaves answered durable and records retryable/non-retryable callback state | A callback failure never reports task-created success or emits owner egress. |
| Internal-only/DND boundary | Tests run during active DND and absent/invalid floor fixtures | No context mutation, delivery-policy read, delayed timer, Messenger call, or user notification occurs. |
| Briefing boundary | Briefing job/spec tests prove no new contribution/composer behavior | No cross-schema briefing read/write or envelope change is introduced. |

## Risks / Trade-offs

- **Answer durable but callback unavailable** -> Preserve answered plus
  callback_failed and a stable replay key rather than inventing a false complete
  result or losing the answer.
- **Two stores cannot commit task and ledger binding atomically** -> Use the
  deterministic name and metadata reconciliation rule; reject mismatched
  records instead of generating a replacement task.
- **A target answer can contain prompt-injection-like content** -> Keep it out
  of callback authority and schedule control; make it retrievable reference
  data only.
- **An old answered row looks tempting to repair** -> Fail closed for rows
  missing v1 provenance. Explicit future migration/reconciliation work would
  need its own specification.
- **Confusion with owner wake recovery** -> Exclude all owner/DND/floor/egress
  inputs and cite PR #3513 as an adjacent non-overlap, not a dependency.

## Migration Plan

This OpenSpec-only change has no deployment or migration step.

The implementation sequence is deliberately serial:

1. bu-27dxl.5.2 adds the durable representation and MCP-only propagation with
   migrated-DB and route/task replay tests.
2. bu-27dxl.5.3 makes the validated core group usable in selected butler
   configurations and proves the intended tool inventory/guidance.
3. bu-27dxl.5.4 adds one bounded deterministic producer without changing the
   briefing composer or user delivery.

Rollback of this planning PR is removal of its OpenSpec change directory. A
future implementation must be independently reversible: disable the producer,
remove activation, then retire the callback/task path only after durable rows
are safely preserved for discovery.

## Open Questions

None block the protocol contract. The physical schema shape, exact migration
revision, route-recovery hook placement, and test fixture mechanics belong to
bu-27dxl.5.2 so long as they preserve every logical field and transition
defined above.
