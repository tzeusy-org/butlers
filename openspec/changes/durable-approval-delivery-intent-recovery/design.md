## Context

RFC 0021 requires a one-tap owner notification whenever an action parks. The
current common helper, `park_pending_action()`, inserts a pending action and
then makes an independent best-effort push attempt. Its `approval_push_emissions`
row has useful legacy display outcomes, but it is not an atomic outbox: a
runtime-less caller, crash, quiet-hours deferral, or failed/ambiguous transport
can leave an action pending without a recoverable notification intent.

The affected path crosses every approval producer, its owning schema, daemon
background lifecycle, Switchboard/Messenger notify routing, dashboard reads,
and retention. The design must preserve four independent contracts:

- RFC 0017 owner-only recipient resolution and secret handling;
- RFC 0021's exact quiet-hours release, control-plane budget exemption, and
  first-three / digest / collapsed burst behavior;
- RFC 0022's rule that PostgreSQL `NOTIFY` is only a wake hint, never durable
  work; and
- Doctrine's parked-action boundary: notification recovery can never approve,
  execute, expire, defer, or edit the domain action.

The canonical dashboard-approvals defer endpoint is a fifth binding contract:
an authenticated defer extends expiry and resets notification re-presentation
to `now + hours`. The current source only updates `pending_actions.expires_at`,
but the canonical requirement still binds this packet. A later presentation is
therefore an explicit dashboard-authorized generation, not a worker retry or a
new logical action.

The active `strict-owner-telegram-wake-recovery` planning change concerns the
generic deferred-notification queue. This change deliberately does not join
that queue, its wake cohort, or its cross-schema protocol. Legacy generic
approval rows keep their existing behavior; new approval intents are neither
backfilled nor replayed into either system.

## Goals / Non-Goals

**Goals:**

- Commit each new pending action and one unique delivery intent atomically.
- Make due notification work recoverable across duplicate parks, concurrent
  workers, process crashes/restarts, and ordinary transient failures.
- Preserve RFC 0021 quiet-hours, no-re-gate, budget, and burst-collapse
  semantics exactly at durable admission, including the canonical dashboard
  defer re-presentation exception through a fenced later generation.
- Make the provider boundary safe when it can prove duplicate idempotency and
  honest when a post-start outcome is ambiguous, and bind non-secret recovery
  identifiers to a transport-authenticated issuer/owning schema/mode.
- Fence all decision/expiry races and expose only safe delivery truth to API,
  dashboard, logs, metrics, and retention.
- Produce an implementation packet that can be independently reviewed and
  exercised against real PostgreSQL before any rollout.

**Non-Goals:**

- No source implementation, Alembic execution, deployment, daemon restart,
  live provider replay, owner notification, approval decision/execution,
  entity/fact mutation, or historical-intent backfill in this planning change.
- No generic notification-queue redesign, quiet-hours wake release, new owner
  channel policy, daily insight-budget change, or change to a parked action's
  expiration/decision semantics.
- No generic notification retry/escalation/history behavior for recovery
  traffic: those controls are explicitly excluded rather than reused or
  broadened.
- No exactly-once provider claim. A provider without a stable idempotency or
  reconciliation primitive must remain visibly ambiguous after an uncertain
  post-start outcome.

## Decisions

### D1 — The local approval intent is the transactional outbox boundary

Each approval-owning schema gains `approval_delivery_intents`, one row per
`pending_actions.id`, and append-only `approval_delivery_attempts`. The shared
parking helper accepts a typed `ParkRequest`, validates origin and safe dossier
data, and uses one acquired connection plus one transaction to:

1. insert or return the semantic-key-deduplicated pending action;
2. serialize the current schema's RFC 0021 burst calculation;
3. resolve the quiet-hours *admission* into a persisted `not_before`; and
4. insert the action's unique intent (and existing action-queued audit event
   where its current caller contract requires it).

The intent root carries the immutable logical action key, origin, admission
classification/cohort membership, and safe metadata. Its append-only presentation children carry
timing, state, claim fields, and a monotonic generation; a direct action
presentation has deterministic key `<action_key>:p:<generation>`, while a
cohort digest derives its key from its independent cohort key. Neither
layer carries a callback token, callback secret, recipient, raw message, raw
`tool_args`, or provider payload. The worker reads the local pending action and
renders the current deterministic dossier only while it is still eligible to
notify.

Each presentation has exactly one local subject: either `intent_id` or
`cohort_id`, enforced by a mutually-exclusive foreign-key check and
subject/generation uniqueness. The subject kind and presentation mode must
agree (`action`/`single|collapsed` including a deferred direct successor;
`cohort`/`burst_digest`) so a fourth action's root cannot masquerade as a
digest owner.

The authenticated dashboard defer transaction is the only writer that appends
generation `g + 1`: under the shared action-then-intent-presentation lock it
extends expiry, supersedes any pre-start `g`, and stores `not_before = now +
hours`. If `g` already crossed handoff start, its late result is historical but
`g + 1` remains scheduled. This preserves the dashboard contract without a
second action or a worker-triggered push.

`approval_push_emissions` is read-compatible legacy evidence during rollout;
new code must not write both it and an intent for the same parked action.

**Alternative considered:** an after-commit callback plus a retry job. Rejected
because the callback can be lost before the retry job knows the action exists.

### D2 — Admission snapshots RFC 0021 rather than reusing generic deferral

The parking transaction uses the same Owner Attention Policy calculation as
RFC 0021. It records one `not_before` equal to `requested_at` or the exact end
of the quiet interval. The worker uses that stored value; it never recomputes a
changed policy at delivery time. This preserves the existing no-re-gate rule.

Under the existing per-schema advisory transaction lock, the transaction
counts newly admitted approval intents in the ten-minute window and chooses:

| Park ordinal in window | Admission classification / behavior |
| --- | --- |
| 1–3 | `single`; worker eventually renders one action dossier. |
| 4 | `cohort_anchor`; creates the window's durable cohort, joins it as the first member, and creates its cohort-owned `burst_digest` presentation. |
| 5+ | `collapsed`; terminal non-sendable local presentation evidence that joins that cohort and has no direct transport attempt. |

Every action has an intent root and logical action key. The digest presentation
is keyed by the cohort, not by its fourth action, and renders the current
eligible cohort membership. A terminal member marks only its own membership
ineligible; it cannot cancel a shared digest while another member remains
eligible. If the cohort is empty before handoff, its unsent digest is
`cancelled` with `cohort_empty` but the cohort remains durable for the window,
and a later member atomically creates a successor only when no current unsent
generation exists. A delivered or handoff-started cohort generation is never
duplicated.
The admission decision uses the approval control-plane policy only;
it does not consume or borrow the insight broker's daily budget. New intent
rows never go into `deferred_notifications`, and the generic scheduler never
claims them.

**Alternative considered:** calculate burst/quiet behavior in the worker.
Rejected because concurrent admission or later policy changes would make the
same parked action nondeterministic and could generate duplicate digests.

### D3 — Fenced claims linearize recovery before provider handoff

The daemon starts a dedicated approval-delivery loop only when its local
Approvals module/schema is active. Core owns the loop lifecycle; the approvals
module owns its schema/repository and deterministic render rules. This keeps a
module from creating an independent scheduler while preserving schema
ownership.

Claiming uses a local query with `FOR UPDATE SKIP LOCKED`, increasing
`claim_fence`, assigning a fresh opaque `claim_token`, and setting a finite
lease on a specific presentation generation. All renewals and state writes
require that generation plus the same token/fence. The worker uses a fixed lock
order (`pending_actions`, then intent, then presentation/cohort membership)
whenever it must coordinate with a decision/defer writer.

The implementation has two recovery categories:

1. A stale `claimed` presentation lacks a provider-start marker and can be
   reclaimed safely by a higher fence.
2. A stale `handoff_started` presentation may have reached a provider. It must
   be reconciled through Messenger using its immutable presentation key. It may
   become `delivered`, `retry_wait` only with proof of safe retry, or
   `ambiguous`; it cannot be directly claimed to send again.

Backoff is exponential with bounded jitter/cap and database-time scheduling.
Attempts continue while a pending action remains eligible; a long-lived
failure becomes a derived stuck alert rather than an untracked terminal drop.

**Alternative considered:** use the generic scheduler's due-row scan. Rejected
because that scan has no action fence or provider-handoff semantics and changes
would couple this control plane to unrelated notification behavior.

### D4 — Send-start is the cancellation linearization point

The worker resolves the current verified owner target and callback secret only
in memory. If either is absent, it writes a safe pre-handoff retry reason and
never calls the provider. Otherwise it enters a local transaction, locks the
action, intent, and current presentation, verifies `status='pending'` and an
unexpired action, and persists an attempt plus `handoff_started` under its
fence. That commit is the last cancellation-safe point for that generation.

Each terminal domain path out of pending uses the same connection transaction
to change the action, cancel that action's matching nonterminal presentations,
and mark its cohort membership ineligible. It does not cancel a shared digest while
another member is eligible. If the decision/expiry wins before send-start, no
provider call occurs. If send-start wins, a later domain transition cancels
future recovery but cannot revoke a network call that may already be in flight.
Any late delivery result is append-only attempt evidence and cannot restore the
action or presentation to a sendable state.

Dashboard defer is the only nonterminal scheduling writer. It uses the same
lock order: defer-first supersedes the current pre-start generation and blocks
its provider call; send-start-first permits only the old attempt's historical
result while defer records exactly one next generation. A later terminal
transition cancels the next generation as well. The worker has no route to
perform any part of this operation.

For a deferred cohort member (the fourth anchor or a later collapsed member),
that transaction marks only the membership ineligible for an unstarted cohort
digest and appends its direct action successor. It does not
cancel a shared digest while other members remain eligible; a handoff-started
digest is historical evidence rather than a revocable send.

The worker has no code path that writes `pending_actions`; an observed
past-expiry action lets it cancel only its own presentation. The normal expiry
operation remains the sole domain transition writer.

**Alternative considered:** hold the transaction while calling Messenger.
Rejected because a network wait would hold action locks and still cannot
transactionally undo a provider side effect.

### D5 — Messenger owns trusted recovery handoff and ambiguous egress truth

The current `notify.v1` response is an in-memory success/failure shape and
Messenger routes `approval_request` inline. Future recovery delivery uses a
separate, narrowly typed `NotifyDeliveryV1` mode. Its logical action-or-cohort
subject key and generation-specific presentation key are correlation values,
not credentials; a digest never reuses the fourth action's key.
Switchboard derives the issuer and owning schema from the authenticated daemon
transport principal, validates that they are registered for the recovered
action or cohort subject through a non-caller-serializable source-schema attestation,
and forwards that trusted context without exposing a caller-settable equivalent
in the serialized envelope. If the current route cannot prove that principal
and attestation, it must reject recovery before any generic log, ledger write,
or provider call rather than treating `source_butler`, an origin field, or a
key prefix as authority. This adds no peer pool or cross-schema read grant.

At the actual Messenger provider boundary, a new, narrowly wired
approval-handoff ledger is keyed by the trusted tuple `(issuer, owning_schema,
presentation_key, mode)`. The source schema attests the action/cohort
subject-to-presentation relation through the authenticated recovery transport;
Messenger accepts that context only from Switchboard and gains no peer pool or
cross-schema read grant to infer it. It returns a normalized safe classification:

| Boundary result | Definition | Local action |
| --- | --- | --- |
| `confirmed` | Provider acceptance/duplicate acknowledgement or a reconciled receipt proves the same trusted tuple completed. | Mark presentation `delivered`. |
| `safe_retry` | No provider call began, or the adapter/reconciliation contract proves reuse of the same presentation key cannot duplicate. | Back off and retry that presentation only. |
| `ambiguous` | A provider-start record exists but no confirmation/reconciliation proof exists. | Mark presentation `ambiguous`; reconcile only, never blindly resend. |

The new ledger must be used on the real `route.execute`/provider path. It is
not a restoration of Messenger's retired, unwired generic delivery-request
tables, and recovery material must not enter those generic rows. Generic
notification list/history/read/stats/acknowledge/retry/escalate and
stored-envelope reconstruction must exclude or reject recovery entries, so no
generic operator/API route can reveal a recovery envelope or initiate a replay.
The approval API may expose only the safe delivery projection. Provider-specific
adapters may add true idempotency or lookup support later, but they may not
label an unknown post-start outcome safe merely to increase retries.

The recovery-mode path must branch before generic `log_notification()` and
`_write_outbound_message_inbox()` persistence. It creates no outbound
`switchboard.message_inbox` row, including a redacted placeholder, because that
table is the source for generic conversation and LLM history. The protected
handoff ledger therefore records only its bounded safe metadata; rendered text,
recipient-derived thread identity, and callback material remain egress-local.
Any future redacted non-history record must have a separate contract proving it
cannot be joined into a generic reader.

**Alternative considered:** authenticate recovery from the action-key prefix or
a caller-supplied origin. Rejected because both are forgeable correlation data
at a cross-schema boundary. A generic retry path was also rejected because it
can bypass the presentation fence and retain callback-bearing envelope data.

### D6 — Safety vocabulary and truthfulness are schema/API contracts

Migration checks constrain intent/presentation state, mode, handoff class, and reason code
to closed values. The API projects a small `ApprovalDeliveryStatus` instead of
the legacy `push_outcome` as authoritative truth:

- logical action state plus the current presentation generation/state/mode,
  safe `last_reason_code`, attempt count, and `next_attempt_at`;
- `stuck` and `ambiguous` booleans derived server-side; and
- a legacy evidence indicator only while old emission rows remain.

The dashboard distinguishes “waiting for quiet hours,” “retrying,” “delivery
uncertain,” “collapsed into digest,” and “cancelled by decision” from a
confirmed provider handoff. It must never infer “owner was never notified”
from a null/failed old push row. No recipient, tool arguments, token, raw
provider error, full message, or secret enters a list API, frontend type,
metric label, or user-visible diagnostic.

**Alternative considered:** store raw exception text for debugging. Rejected:
it can contain target identity or provider content and makes a closed recovery
state impossible to reason about across consumers.

### D7 — Retention preserves unresolved safety evidence

Attempts are append-only for an intent/presentation lifetime. A safe terminal summary is
also appended to the approval event spine, whose existing historical
provenance rules outlive mutable action rows. Routine retention never deletes a
nonterminal or `ambiguous` root, presentation, or cohort while its action or
eligible cohort member remains pending. Terminal root/presentation/cohort/attempt
rows are removed only in the same retention phase as their terminal action(s);
migration downgrade refuses to drop any nonempty new table.

This intentionally favors an observable stale intent over deletion/replay. It
also allows an application binary rollback to leave additive data intact until
a compatible binary returns, rather than converting it into a live provider
send or a domain mutation.

## Risks / Trade-offs

- **Provider ambiguity can leave an owner notification unresolved.** → Show
  `ambiguous`/stuck truth, keep dashboard decisions available, and prohibit
  speculative duplicates until an adapter gains a proof-bearing reconcile path.
- **A source worker may crash after a remote call.** → Messenger's key ledger
  is the reconciliation authority; the source's send-start marker never alone
  permits another send.
- **Concurrent decision and worker sends are intrinsically non-transactional.**
  → Make send-start an explicit durable linearization point, cancel on the
  winner, and test both orderings.
- **Dashboard defer can race provider handoff or create an accidental duplicate.**
  → Keep one logical action key, fence each presentation generation, and allow
  exactly one authenticated deferred successor under the action lock.
- **A fourth action can terminate before a digest is sent.** → Make the cohort,
  rather than that action, own the digest and test a fifth-or-later member after
  the original representative is terminal.
- **A generic notification control could bypass recovery safety or leak a
  callback-bearing envelope.** → Keep recovery handoff data out of generic
  notification persistence and reject/exclude it in every generic read/control
  path.
- **A non-secret key could be replayed across schemas.** → Bind recovery at the
  actual authenticated transport boundary to registered issuer/schema/mode and
  fail closed on any mismatch before egress.
- **A per-schema worker can increase background load.** → Claim in bounded
  batches, use due indexes and capped retry scheduling, and expose backlog/oldest-due
  metrics before enabling more schemas.
- **Legacy push data can confuse operators during rollout.** → Mark it
  explicitly legacy in API/UI and never synthesize/backfill it into new intents.
- **A migration downgrade can lose recovery evidence.** → Use guarded
  non-destructive downgrade checks, not a destructive “rollback.”

## Migration Plan

1. Add an additive Approvals migration for intent roots, presentation
   generations, cohorts/membership, append-only attempts, constraints,
   due/lease indexes, and safe approval-event vocabulary; add a separately
   owned, wired Messenger handoff migration for trusted
   issuer/schema/presentation/mode reconciliation. Do not execute either in
   this change.
2. Release compatible read paths and observability with the new writer/worker
   disabled. Existing emission rows stay readable as legacy evidence.
3. Add the atomic parking helper and terminal transition helper, update all
   listed producers, and enable new writes for newly parked actions only. Do
   not dual-send a legacy emission and a new intent.
4. Enable the worker after real-PostgreSQL fault/concurrency coverage and an
   owner-authorized staging/canary drill. Expand schema-by-schema while
   watching due age, lease expiry, ambiguous count, and no duplicate handoffs.
5. Keep schema and data on binary rollback. Disable new admission first; do
   not downgrade/drop while any root/presentation/cohort/attempt/event evidence exists. Active
   intents remain visible and recover only when a compatible worker returns.

## Open Questions

- Which current provider adapters can accept/reconcile a stable idempotency
  key today, and which must return `ambiguous` after every post-start timeout?
  The implementation packet requires an adapter capability inventory before
  enablement; it may not assume Telegram has one.
- What bounded retry and stuck-SLO values best fit the existing daemon cadence?
  Choose them as explicit reviewed constants/configuration with database-time
  tests; do not silently inherit the generic scheduler's polling interval.
- Which existing daemon-to-Switchboard/Messenger path can carry an authenticated
  source principal and a non-caller-serializable source-schema
  subject/presentation attestation? The implementation must inventory that
  boundary first and fail closed until it can enforce the registered
  issuer/owning-schema binding without a peer pool or cross-schema grant.
- Should the safe terminal event be one generic delivery-summary event or a
  small closed family of event types? The migration must preserve existing
  approval-event retention/provenance checks either way.
