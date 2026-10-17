# Ordinary ingestion target acceptance

The adopted [target-delivery contract](../../openspec/changes/recover-ingestion-target-deliveries/specs/ingestion-target-delivery-recovery/spec.md)
separates source classification, target acceptance, and target processing. D1
provides the immutable representation and target acceptance primitive. Committing
plans in the ingest pipeline, fenced intent workers, retry admission and recovery
projections remain subsequent implementation work. This change does not activate
those paths or backfill historical deliveries.

## Route envelope

Ordinary Switchboard ingestion uses an optional top-level `delivery` on
`route.execute`. It contains `ingestion_event_id`, `target_butler`, `segment_id`,
`payload_digest` and `operation` (`accept`, the default, or `lookup`). The event
UUID7 equals `request_context.request_id`; the segment equals both context and
subrequest segment fields, and both subrequest ids must match. An explicit
`target.butler` equals the receiving daemon and `delivery.target_butler`.
Dashboard, Messenger and Switchboard target lanes refuse this binding. Existing
routes without this field retain their existing contracts.

Only the existing configured `trusted_route_callers` boundary admits the
Switchboard source endpoint. This is an identity assertion within the adopted
trusted-host model, not cryptographic caller authentication. No new credential,
provider permission, cross-schema receipt read, or approval-delivery table is
introduced.

`DeliveryBinding.for_envelope()` creates a binding from normalized `route.v1`
work. The SHA256 digest covers the complete normalized input, target, source
metadata and request/subrequest lineage. Only `delivery`, top-level tracing,
context tracing and context `received_at` are excluded. Encoding uses sorted
JSON keys, compact separators, UTF-8 without ASCII escaping, and rejects
non-finite numbers. Normalized defaults are included. Source metadata and
message content cannot change under the same key, even after processing ends.
The private server-admitted source record remains separate from this public
route envelope and cannot be supplied as attribution by the caller.

## Atomic receipt and lookup

Acceptance inserts into target-local `route_acceptance_ledger` and `route_inbox`
in one READ COMMITTED transaction. The unique key is
`(ingestion_event_id, target_butler, segment_id)`. A matching concurrent insert
waits for the first transaction, then reads its receipt; only the new winner
schedules processing. The receipt UUID is the first route inbox id. Duplicate
calls return it with `dedup: true`, even when the processing row is gone. A
same-key digest conflict returns no receipt. Existing inbox processing claims
still fence downstream execution.

`operation: lookup` validates the same event, target and segment lineage and
compares the supplied original digest to the retained ledger. Callers can use
`input.prompt: "receipt lookup"` instead of sending message content. Lookup
performs only SELECT, does not consume private source admission, insert an inbox,
claim processing, or spawn a session. It returns `accepted` with `receipt_id`,
`not_found` with no receipt, a binding conflict, or unavailable. Absence and
unavailability do not prove that transport had no effect and never authorize
an automatic resend. Success and refusal responses for this lane omit sender
identity, message payload and raw exception details.

## Storage and retention

`core_268` installs the target-local content-blind ledger, runtime RLS and
append-only guards. Each runtime role may SELECT/INSERT only its own bound
target. Named-schema installations also fix the target to the installed schema;
legacy standalone public-schema installations permit each runtime's own target
binding. UPDATE, DELETE and TRUNCATE are refused, including mutation attempts
made after bootstrap re-widens table grants. A separate inbox-insertion trigger
requires an ingestion-bearing inbox row to use its matching retained receipt;
older raw insertion code cannot mint a second processing row for that envelope.
No source-event or route-inbox FK can cascade away the receipt.

All three tables enable RLS without FORCE, following the existing receipt-table
backup boundary (`core_246`). The trusted migration/table owner retains its
PostgreSQL owner bypass for the configured NOSUPERUSER backup login. Every
`SET ROLE` runtime remains fenced by the unchanged policies and immutable
triggers, including after bootstrap re-grants. No runtime grants, backup role,
credentials or control-plane exclusions are added. The complete ledger, plans
and intents must survive the published artifact and restore; payload pruning
does not remove receipts, digests or non-reacceptance guards.

`sw_045` installs Switchboard-local `ingestion_target_plans` and
`ingestion_target_intents`. The first classified plan, event/target/segment,
subrequest, canonical digest and budgets are immutable. Payload columns can be
nulled for existing retention; they cannot be changed or restored. Source rows
are not foreign-key parents, so source pruning cannot erase the first decision.
The closed state vocabulary is `pending`, `attempting`, `accepted`, `retry_wait`,
`ambiguous`, `terminal_failed`; transport outcomes and reasons have fixed CHECK
vocabularies. Claims pair an opaque fence with a server timestamp, attempt/age
budgets are bounded, and an accepted intent requires an immutable receipt.
Only the Switchboard runtime can read or write these representations. The
future worker must compare its claim fence on every transition; these tables
do not dispatch work themselves.

## Capacity and rollback

The permanent ledger is deliberately non-prunable in v1. Startup/recovery
sweeps emit content-blind capacity observations (`bytes`, `estimated_rows`)
from PostgreSQL catalogs, including indexes, without counting or exposing keys
or content. The existing System database table-size view includes the new
ledger. Operators can sample `route_inbox_acceptance_capacity()` using the
target's own pool and watch the size trend and free disk. Row estimates need
normal ANALYZE and may lag; bytes are the live relation size. Missing capacity
is reported as unavailable, never zero. No capacity threshold authorizes
compaction. A later adopted DB-enforced non-reacceptance fence must precede any
ledger deletion or rewrite.

Both migrations deliberately retain their tables, guards, grants and runtime
RLS on downgrade. Rollback disables the new producer path and keeps evidence;
it does not restore legacy status-only replay or erase acceptance. Older
`route.execute` signatures reject the additional top-level field, and the
retained inbox trigger refuses ingestion envelopes under another inbox id.
Re-upgrade and installer replay preserve existing identities and receipts.
Migration numbers were allocated after a full remote census: main's
predecessors were `core_265` and `sw_044`; foreign unmerged 266/267 were counted
for collision avoidance, not imported as dependencies. Before landing, the
coordinator must reconcile the actual queue tree into one linear chain.

Real PostgreSQL evidence lives in the existing
`tests/config/test_switchboard_message_inbox_partition_migration.py` suite:
concurrent actual-handler calls and distinct-segment sessions, lost responses
and changed work, source clock mutation/pruning, payload pruning, rollback,
installer replay, failed-inbox transaction rollback, closed state/reasons,
retention and bootstrap/runtime role boundaries. Local mocks prove only their
named handler/lease/lookup behavior. Hosted CI supplies real SQL evidence on
hosts without Docker access.

The existing backup/restore test package also publishes a real artifact under
the configured owner login, restores the pruned plans/intents and exact receipt,
replays bootstrap, and verifies runtime/connector denial and non-reacceptance.
