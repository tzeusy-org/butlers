## Context

The strict owner-Telegram wake-recovery design freezes a complete, exact-target
cohort through authenticated `prepare.v1` calls. Its parent packet correctly
allows only a zero-cohort cancellation before any durable prepare result. Once
an origin has moved a row to `release_prepared`, the parent intentionally has no
ordinary return to `pending`: a generic scheduler cannot safely distinguish a
cancelled release from a race with Messenger commit or provider egress.

`canonical-dnd-generation-guard` supplies the missing shared DND evidence. It
does not supply a Messenger-local cancellation record, action-key replay rule,
or linearization point against the effective egress gate. This change defines
that consumer contract without changing DND mutation ownership or implementing
the parent wake release.

| Concern | Owner | Boundary |
| --- | --- | --- |
| Canonical DND mutation and generation | Context bus in `public` | RFC 0009 guarded public operation |
| Cancellation decision source | Health or the origin Scheduler | Own policy/queue state only |
| Cross-butler relay and run authority | Switchboard | Authenticated MCP only |
| Final precommit cancellation admission | Messenger | Messenger-local durable gate and record |
| Prepared rows and eventual scheduler eligibility | Each origin Scheduler | Origin-local state only |

Health and an origin Scheduler never call Messenger directly, read
`messenger` tables, or issue provider actions. They return a local decision to
the current-fence Switchboard coordinator. Switchboard carries the immutable
packet to Messenger, but cannot substitute a local SQL check for Messenger's
own egress admission. Messenger never reads an origin deferred queue.

## Goals / Non-Goals

**Goals:**

- Give a complete prepared cohort one durable, replay-safe precommit
  cancellation decision before any egress intent or send-start marker exists.
- Bind that decision to the current run, fence, participants, cohort, release
  action, cancellation cause, and canonical DND generation without retaining
  notification content or DND payloads.
- Serialize cancellation against both the canonical DND guard and the
  Messenger-local release gate, so a racing release either wins visibly or is
  stopped before it creates an egress intent.
- Preserve all-or-nothing recovery: only an accepted, complete, same-fence
  cancellation may enter the later scheduler-return protocol; DND and uncertain
  outcomes remain retained and scheduler-ineligible.
- Define PostgreSQL/MCP behavior tests that use actual runtime roles and real
  transaction ordering rather than migration-owner or mock-only evidence.

**Non-Goals:**

- No implementation, migration, provider call, Scheduler execution loop,
  Messenger release, or modification to the draft wake-recovery parent packet.
- No new DND writer, DND generation algorithm, public audit read surface, or
  alternate context authority. RFC 0009's canonical guard remains authoritative.
- No direct Health-to-Messenger, Scheduler-to-Messenger, or peer-schema SQL
  path; no shared DSN, origin-queue grant, or provider authority outside
  Messenger.
- No generic cancellation of ordinary notifications, partial cohort release,
  target re-resolution, delivery-content persistence, or automatic resend of an
  ambiguous provider attempt.

## Decisions

### D1 — Use a single Switchboard-mediated cancellation admission packet

The future wire request is `wake_recovery.cancel_admit.v1`. It is an
authenticated Switchboard-to-Messenger operation, not a public Scheduler or
Health tool. A local Scheduler or Health policy may request cancellation only
by returning an authenticated decision to the current-fence coordinator; the
coordinator validates that source against the persisted run and relays one
packet to Messenger.

The request contains only the durable identity and safety evidence needed for
admission:

| Field | Meaning |
| --- | --- |
| `schema_version` | Exact value `wake_recovery.cancel_admit.v1`. |
| `run_id`, `fence` | Immutable current wake run and monotonic owner/window fence. |
| `participant_digest`, `cohort_digest` | Digests of the complete frozen participant set and exact prepared cohort, respectively; neither contains notification content. |
| `release_action_key` | The immutable action identity reserved by the parent wake protocol. |
| `cancellation_action_key` | A stable opaque identity generated once by the coordinator from the version, run/fence, both digests, release action, cancellation cause, and decision reference. It is reused for every retry. |
| `cancellation_request_id` | The stable MCP/correlation ID for the coordinator call; it is logged separately from the action key. |
| `decision_source`, `decision_ref`, `reason_code` | The authorized Scheduler/Health decision category and opaque persisted decision reference; never raw message text. |
| `expected_dnd_generation` | The inactive canonical DND snapshot generation captured for the decision. |
| `dnd_observed_at`, `dnd_revalidate_at` | The database-clock snapshot evidence needed to reject stale or expired evidence. |

The coordinator MUST reject a request that does not exactly match its durable
run/fence/participant/cohort/action record before it invokes Messenger. It MUST
not generate a new action key on retry or translate a local policy error into a
different cancellation reason.

At prepare time, Switchboard SHALL persist an immutable ordered origin-subset
manifest. Each entry is `(origin_butler, origin_frozen_subset_digest,
origin_frozen_subset_count)` and commits only that origin's already-frozen local
prepared subset (including an explicit zero/policy-only commitment where the
participant owns no notification rows). `participant_digest` commits to the
ordered participant identities; `cohort_digest` commits to the exact canonical
origin-subset manifest, so it remains a global all-cohort commitment without
exposing a peer's rows. The manifest is immutable for the run/fence. An origin
later receives its own entry and `cohort_digest`; it MUST prove the former from
its own durable rows and receipts, and MUST NOT attempt to prove the latter by
reading or reconstructing any peer schema.

**Alternative considered:** Let Health or an origin Scheduler call Messenger
directly. Rejected because it creates a second cross-butler channel, lets a
local actor bypass the current-fence coordinator, and would require a direct
peer capability or database grant.

### D2 — Persist one Messenger-local cancellation admission record

Messenger owns a future private record, conceptually
`wake_recovery_cancellation_admissions`. The exact table and Python names are
implementation details, but its durable minimum shape is:

| Field | Meaning |
| --- | --- |
| `cancellation_action_key` | Primary replay/action identity. |
| `schema_version`, `request_fingerprint` | Versioned semantic replay identity. |
| `run_id`, `fence`, `participant_digest`, `cohort_digest`, `release_action_key` | Immutable binding to the prepared release. |
| `cancellation_request_id`, `decision_source`, `decision_ref`, `reason_code` | Correlation and authorization evidence. |
| `expected_dnd_generation`, `observed_dnd_generation` | Captured and transaction-observed DND evidence. |
| `decision_state`, `decision_code`, `decided_at` | Durable accepted, rejected, or ambiguous result and database timestamp. |
| `release_gate_state` | The precommit Messenger state observed under the gate lock. |

The record SHALL contain neither composed notification content, Telegram target
data beyond the parent action binding, raw DND value/metadata, canonical DND
mutation fingerprints, nor provider request/response content. A distinct
provider-attempt record, if one already exists, remains owned by Messenger's
release protocol and is not copied into this admission record.

`cancellation_action_key` is unique. Only one cancellation admission may bind a
given `(run_id, fence, release_action_key)`; a different action key for that
same tuple is a `conflicting_cancellation_action`, not a second attempt.
`request_fingerprint` is a versioned digest over every semantic request field
above. An exact replay returns the original durable receipt. Reusing the action
key or request ID with changed run, fence, digest, action, decision reference,
reason, or DND evidence fails closed as `idempotency_conflict` without changing
the release gate or record.

**Alternative considered:** Reuse only `wake_recovery.release.v1`'s action
record. Rejected because release idempotency proves an egress action, whereas
cancellation must prove that no egress intent existed when it won and must
retain its own source/DND/replay evidence.

### D3 — Linearize against the Messenger release gate and canonical DND guard

Messenger's prepared-release gate is a private, durable record distinct from
an egress intent. The future `commit.v1`, `release.v1`, and
`cancel_admit.v1` operations MUST lock that same gate row with `FOR UPDATE`.
This gives a single order between cancellation and egress construction.

For a new cancellation action, Messenger performs one local database
transaction:

1. authenticate the Switchboard caller and validate the versioned request;
2. lock the matching prepared-release gate and check the immutable
   run/fence/participant/cohort/action binding;
3. return an exact stored cancellation receipt or reject a replay conflict
   before changing the gate;
4. invoke the canonical RFC 0009 DND admission helper on the same connection:
   take the public guard `FOR SHARE`, require the expected generation, a fresh
   database-time inactive DND state, and provable snapshot evidence;
5. prove that the private release gate has neither an egress intent nor a
   `send_started_at` marker, provider receipt, provider-attempt ambiguity, or
   incompatible terminal state;
6. persist the accepted or rejected cancellation record and the corresponding
   terminal release-gate transition, then commit before returning a receipt.

The public share lock remains held only through Messenger's local durable
decision. It is never held across an MCP call, origin finalization, provider
I/O, or retry delay. A canonical DND writer that commits first advances the
generation and makes cancellation reject; a cancellation that gets the share
lock first commits its local decision before that writer can proceed.

If `commit.v1` wins the private release-gate lock first, it creates the egress
intent and cancellation MUST reject it as `egress_intent_exists`. If
`cancel_admit.v1` wins, it records `accepted_precommit` and every later
commit/release under that run/fence MUST reject before creating an intent. A
send-start marker, confirmed provider receipt, or inconsistent attempt state
is never rewritten into cancellation success.

**Alternative considered:** Check DND or the release state before acquiring
locks. Rejected because either state could change between a preflight and the
durable decision, producing an unprovable cancellation race.

### D4 — Make the acceptance predicate deliberately narrow

Messenger returns one of these durable decision classes:

| Class | Required result | Scheduler consequence |
| --- | --- | --- |
| `accepted_precommit` | Exact binding, inactive matching DND generation, and no egress intent/send-start are all proved. | Eligible only for the all-cohort finalize/publish protocol in D5. |
| `rejected_blocked_dnd` | Generation changed, active DND is observed, or snapshot evidence is stale/unprovable. | Switchboard must durably fan the same-fence result to every participant through the parent `abort.v1(reason=blocked_dnd)` path; every uncommitted origin reaches its parent-defined `release_retained_dnd` state and no row becomes `pending`. |
| `rejected_egress_present` | Egress intent, send-start, provider receipt, or incompatible release state already exists. | Existing run/action recovery continues; no scheduler return. |
| `rejected_fence_or_digest` | Run/fence/action/participant/cohort identity is stale or mismatched. | Existing current-fence state remains authoritative. |
| `ambiguous` | Messenger cannot prove a coherent local gate/attempt state or cannot recover a prior outcome after retry/restart. | No scheduler return and no automatic provider resend; explicit reconciliation is required. |

`rejected_blocked_dnd` is the explicit retained `blocked_dnd` behavior for a
DND-generation mismatch. It is not a generic error that a caller may retry
with a new generation, because a changed generation means the original
cancellation decision no longer describes the same safety state. A later
qualifying wake path owns any successor run under its higher fence.

No transport timeout is interpreted as acceptance. The coordinator retries the
same `cancellation_action_key`, request ID, and fingerprint until it receives a
durable receipt. If Messenger returns `ambiguous`, the coordinator preserves
the cohort and action evidence; it MUST NOT send, return rows to `pending`, or
mint a replacement action key.

### D5 — Bind publication to a locally provable frozen subset

Messenger admission decides whether a release can be cancelled before egress;
it does not own origin rows. Therefore `accepted_precommit` starts a durable
all-cohort completion protocol rather than an immediate local Scheduler update:

1. Switchboard derives and persists one immutable, per-origin
   `wake_recovery.cancel_finalize.v1` action/request identity from the accepted
   Messenger receipt, the exact run/fence/digests/actions, and that recipient's
   immutable origin-subset manifest entry. It sends that versioned packet only
   as the authenticated current-fence Switchboard coordinator.
2. The packet delivers the opaque accepted Messenger evidence and the
   recipient's manifest entry. Before reading or changing cancellation state,
   the receiving origin authenticates the caller as that coordinator and checks
   the packet's version, target, run/fence, global commitments, local manifest,
   release/cancellation actions, and accepted evidence against its own durable
   prepared state or policy state. Messenger, Health, a Scheduler, a provider,
   and unauthenticated callers cannot invoke this origin operation.
3. In one origin-local transaction, the receiver proves its complete
   `origin_frozen_subset_digest` and count, moves all and only that frozen local
   subset to `cancellation_ready`, and persists a durable versioned
   finalization receipt. Health records its matching policy decision as
   cancellation-ready but does not clear unrelated context. These states remain
   scheduler-ineligible. A global `cohort_digest` is a correlation commitment,
   never a substitute for this local proof.
4. Switchboard durably collects a compatible finalization receipt from every
   snapshot participant and derives a `cancellation_finalization_digest` over
   the ordered receipts, each of which carries that participant's immutable
   origin-subset digest and receipt digest.
5. Only then does it issue a replay-safe `wake_recovery.cancel_publish.v1`
   packet to each origin. An origin may move all and only its matching
   `cancellation_ready` rows to the prerequisite-defined scheduler return state
   only for this packet; individual-row, subset, or changed-fence publication
   is rejected.

`wake_recovery.cancel_finalize.v1` is the authenticated Switchboard-to-origin
operation that establishes the local finalization receipt; it is not an
informal forwarding of a Messenger result. Its exact semantic request fields
are:

| Field | Meaning |
| --- | --- |
| `schema_version` | Exact value `wake_recovery.cancel_finalize.v1`. |
| `run_id`, `fence`, `origin_butler` | The immutable current run/fence and intended receiving origin. |
| `participant_digest`, `cohort_digest` | The global immutable participant and cohort commitments. |
| `origin_frozen_subset_digest`, `origin_frozen_subset_count` | The receiving origin's immutable manifest entry and local proof binding. |
| `release_action_key`, `cancellation_action_key` | The parent release identity and accepted cancellation identity. |
| `accepted_admission_receipt`, `admission_receipt_digest` | Opaque durable Messenger evidence whose decision is exactly `accepted_precommit`, plus its immutable digest; it contains no notification, DND, or provider payload. |
| `cancel_finalize_action_key`, `cancel_finalize_request_id` | Stable per-origin action and correlation identities, derived once from every immutable binding above and reused on retry. |
| `request_fingerprint` | Versioned digest over every semantic request field above. |

The origin persists a `wake_recovery.cancel_finalize.receipt.v1` receipt keyed
by `cancel_finalize_action_key` in the same transaction as the local
`cancellation_ready` transition. The receipt contains the request fingerprint,
accepted-admission receipt digest, run/fence/global commitments, local manifest
digest/count, and resulting local state. An exact replay with the same action,
request ID, and fingerprint returns that stored receipt after either side
restarts. Reusing either identity with any changed semantic field returns
`idempotency_conflict` without a state mutation; a wrong target, stale or
different fence, mismatched manifest, or missing/non-accepted evidence is
rejected before the transition. A timeout without a durable receipt leaves the
origin unfinalized: Switchboard retains and resends the same finalization action
and request ID, cannot derive a compatible aggregate digest, and cannot issue
publication. If the receipt committed before a lost response, the same replay
returns it rather than deriving a result from current row state.

`wake_recovery.cancel_publish.v1` is an authenticated Switchboard-to-origin
operation, not a Messenger, Health, Scheduler, or provider surface. Its exact
semantic request fields are:

| Field | Meaning |
| --- | --- |
| `schema_version` | Exact value `wake_recovery.cancel_publish.v1`. |
| `run_id`, `fence`, `origin_butler` | The immutable current run/fence and the intended receiving origin. |
| `participant_digest`, `cohort_digest` | The global immutable participant and cohort commitments. |
| `origin_frozen_subset_digest`, `origin_frozen_subset_count` | The receiving origin's immutable manifest entry; this is the local proof binding. |
| `release_action_key`, `cancellation_action_key` | The parent release identity and the accepted cancellation identity. |
| `admission_receipt_digest`, `origin_finalization_receipt_digest`, `cancellation_finalization_digest` | The accepted Messenger receipt, this origin's durable finalization receipt, and the complete ordered origin-finalization commitment. |
| `cancel_publish_action_key`, `cancel_publish_request_id` | Stable per-origin action and correlation identities, derived once from the immutable run/fence, digest, action, and receipt bindings above and reused on retry. |
| `request_fingerprint` | Versioned digest over every semantic request field above. |

Only the authenticated current-fence Switchboard coordinator may invoke this
operation. Before changing local eligibility, the receiver SHALL check the
version, caller, run/fence, origin identity, global commitments, immutable
local subset digest/count, accepted admission receipt, its own durable
finalization receipt digest, and complete finalization digest against its own
durable `cancellation_ready` state. It persists one origin-local publish receipt
keyed by `cancel_publish_action_key`, including the request fingerprint and
resulting local state, before replying.
An exact retry returns that stored receipt after either side restarts. Reusing
its action key or request ID with any changed semantic field, targeting another
origin, a missing receipt, a stale fence, or a locally mismatched subset fails
closed without changing eligibility. A timeout with no durable receipt is not a
publication; Switchboard replays the same packet and never derives success from
a global digest alone.

The future implementation may represent the final return state as `pending`
only if its normal scheduler and final Messenger delivery admission preserve
the cancellation receipt's run/fence/DND evidence. It MUST NOT route that row
through the parent wake release or a legacy unguarded flush. A crash or missing
participant receipt leaves every unfinalized portion scheduler-ineligible and
retries the exact cancellation protocol; it never converts a prepared subset
to ordinary work.

This is all-or-nothing at the durable protocol boundary without pretending that
separate butler schemas share a distributed SQL transaction. The durable
finalization digest proves that every participant reached the same cancellation
decision before any origin receives a publish authorization, while each
origin-local finalization receipt and subset digest give the receiver a proof it
can validate without peer-schema access. A retransmitted publish packet is
idempotent; it cannot change membership, action, target, local subset, or DND
evidence.

**Alternative considered:** Let each origin set its prepared rows directly to
`pending` after receiving a local cancellation request. Rejected because a
missing participant, racing commit, or stale digest could release an arbitrary
subset and make the ordinary Scheduler send what was supposed to be one fenced
cohort.

### D6 — Fan DND rejection through the parent retained-state contract

`rejected_blocked_dnd` is a Messenger-local guarded-admission decision, not an
origin-local DND query or a permission for Switchboard to mutate an origin
queue. On that durable receipt, Switchboard SHALL persist a same-fence DND-abort
fanout record containing the cancellation action, Messenger receipt digest,
run/fence, participant/cohort commitments, and immutable origin-subset manifest.
It SHALL then invoke the parent `strict-owner-telegram-wake-recovery`
`abort.v1` operation with the parent-defined `blocked_dnd` reason for every
current-fence participant. This change consumes that parent reason-specific
contract by reference; it does not define a new abort variant or alter parent
PR #3513's state machine.

Every current-fence participant returns the parent replay-safe abort receipt;
each origin participant applies the parent's durable `aborted_dnd` /
`release_retained_dnd` transition to its own matching frozen subset. Switchboard
records each receipt and, on a timeout, conflict, or origin restart, replays the
same parent `abort.v1` only to the outstanding participant. It MUST NOT send
`cancel_publish.v1`, expose a row to the Scheduler, rewrite the Messenger
decision, re-read DND on behalf of an origin, or use direct SQL to emulate the
retained transition. Until every participant has returned the compatible parent
receipt, the fanout remains incomplete and no successor, publication, egress,
or partial release is authorized. The parent contract continues to govern the
later DND-clear plus fresh-owner-intent successor rule.

### D7 — Restart and failure recovery use only durable receipts

Every receiver persists its request fingerprint and result before responding.
After a process restart, a replay returns the stored matching receipt; it never
reconstructs an outcome from in-memory state, a client timestamp, or an
assumption that a provider did not receive a call. A missing record after a
known committed release state, contradictory state, failed role proof, missing
DND guard, missing database time, or unavailable required participant is
fail-closed.

This applies independently to Messenger admission,
`cancel_finalize.v1`/its origin-local receipt, `cancel_publish.v1`, and the
parent DND-abort fanout receipts. A Switchboard or origin restart may replay the
same action/request identity; it MUST NOT mint a replacement finalization or
publication, infer a local transition from a transport timeout, or turn a
partial DND fanout into a scheduler return.

An ambiguous provider outcome is outside the accepted precommit path because
the no-egress-intent/no-send-start predicate is false or unprovable. It remains
`egress_ambiguous` under the parent release record, prohibits automatic resend,
and cannot be transformed into a cancellation success or ordinary scheduler
work by a later retry.

## Contract-Test Matrix

The implementation MUST add behavior-executing PostgreSQL integration and MCP
contract tests. Source inspection, a migration-owner connection, or mocks that
skip row locks do not prove these properties.

| Case | Setup / action | Required proof |
| --- | --- | --- |
| Exact precommit cancellation | Complete prepared cohort, matching current fence/digests, inactive DND generation `N`, no egress intent | Messenger records one `accepted_precommit` receipt before response; commit/release cannot create an intent; no provider tool is called. |
| DND wins first | Health snapshot captures `N`; canonical writer commits `N+1` before Messenger guard admission | Messenger durably returns `rejected_blocked_dnd`; Switchboard replays parent `abort.v1(reason=blocked_dnd)` to every participant until each has its parent receipt and every origin has the parent-defined `release_retained_dnd` result; no scheduler return or egress occurs. |
| Cancellation wins DND ordering | Messenger holds the guard share lock for matching inactive `N` while a canonical writer races | Messenger commits its local decision before the writer advances to `N+1`; no lock is held across MCP/provider I/O, and later egress still requires its own guard admission. |
| Release wins private gate | `commit.v1` locks the prepared-release gate and writes an egress intent before cancellation | Cancellation records `rejected_egress_present`; it cannot remove the intent, send, or publish rows. |
| Cancellation wins private gate | Cancellation locks the matching prepared gate first | It records accepted cancellation and every later same-fence commit/release rejects before creating an intent or send-start marker. |
| Exact replay | Repeat a complete request with the same action key and fingerprint after any terminal result | The original receipt is returned; generation evidence, gate state, and record are not rewritten. |
| Conflicting replay | Reuse action key/request ID with changed fence, digest, action, reason, decision reference, or DND generation | `idempotency_conflict` is returned with no state mutation. |
| Stale or cross-run request | Submit a lower/current-mismatched fence or another run's digest/action | Messenger rejects before a local decision or egress change; the current run remains authoritative. |
| Local-subset mismatch | Send an origin a matching global `cohort_digest` but changed `origin_frozen_subset_digest`/count, origin identity, or local prepared subset | The origin rejects `cancel_finalize.v1` and `cancel_publish.v1` without peer reads or a local transition; Switchboard retains the same action for reconciliation. |
| Finalization exact/conflicting replay | Re-send the same `cancel_finalize.v1` action/request/fingerprint, then reuse either identity with changed evidence, fence, manifest, or action | Exact replay returns the one durable finalization receipt without another transition; conflicting replay returns `idempotency_conflict` with no local state mutation. |
| Finalization timeout and restart | Messenger accepted cancellation but a finalization response is lost, or Switchboard/origin restarts before it is observed | Without a durable receipt, rows remain prepared or cancellation-ready and scheduler-ineligible; Switchboard retries the same finalization action/request only. A committed receipt is recovered by exact replay, and no publication is issued until every receipt is compatible. |
| Publish replay and restart | Re-send the same `cancel_publish.v1` action/request/fingerprint after an origin restart | The origin returns its original durable publish receipt only when its matching finalization receipt and aggregate digest are present; changed global or local-subset evidence cannot make rows scheduler-visible. |
| DND fanout restart | Lose an origin `abort.v1(reason=blocked_dnd)` response or restart Switchboard/origin after a durable receipt | Switchboard replays the same parent abort only to the missing origin; no `cancel_publish.v1`, scheduler fallback, or new DND decision is inferred. |
| Timeout and restart | Lose the MCP response or restart Messenger after committing a record | Retrying the same action key reads the durable receipt; absence or incoherence is `ambiguous`, never assumed accepted. |
| Send-start / provider ambiguity | Attempt cancellation when send-start, provider receipt, or ambiguous attempt evidence exists | Cancellation cannot be accepted, cannot return rows to ordinary work, and cannot trigger automatic resend. |
| Role and topology proof | Execute as `butler_health_rw`, `butler_switchboard_rw`, and `butler_messenger_rw` with real `SET ROLE` sessions | Health reads only public DND evidence; Switchboard cannot SQL-read Messenger/origin rows; Messenger owns its record; only authenticated Switchboard MCP calls reach cancellation admission. |

## Risks / Trade-offs

- **[Cancellation needs multiple durable receipts]** → The protocol favors a
  retained, recoverable cohort over a fast but partial cancellation. Same-fence
  replay supplies liveness without inventing membership.
- **[A DND change can occur after a cancellation decision]** → The publish
  receipt carries the captured evidence, and any future effective egress must
  perform its own Messenger DND admission. Cancellation is never treated as a
  permanent permission to send.
- **[A private release gate adds state]** → It is deliberately Messenger-local
  and avoids a public cross-schema coordinator or provider-side behavior.
- **[Ambiguous outcomes can retain notifications]** → Retention is safer than
  duplicate owner delivery; explicit reconciliation owns any later resolution.

## Migration Plan

This planning change performs no migration. The implementation sequence is:

1. Land and verify the canonical DND guard implementation and its role/locking
   tests before enabling this consumer.
2. Add Messenger-local prepared-release-gate and cancellation-admission
   records, origin-local frozen-subset/finalization/publish receipts, and
   Switchboard run/finalization/DND-fanout records through schema-owned
   migrations with least-privilege ACLs.
3. Add authenticated versioned MCP validation and same-fence replay handling
   for admission, origin publication, and the parent DND-abort fanout; do not
   expose direct peer SQL or provider tools.
4. Add the PostgreSQL/MCP role, concurrency, crash, and ambiguity tests above.
5. Keep the capability disabled until the parent wake-recovery implementation,
   its final egress DND admission, and a staged reconciliation drill are ready.

Rollback preserves prepared, cancellation-ready, accepted, rejected, and
ambiguous records. A downgrade MUST NOT reinterpret any of them as ordinary
`pending`, erase a cancellation receipt, or resend a provider action; a
compatible binary must resume or explicitly reconcile the durable state.

## Open Questions

None block the contract. Exact table, Python helper, and RPC registration names
remain implementation details so long as they preserve the versioned packet,
same-row lock ordering, durable replay identities, and fail-closed behavior.
