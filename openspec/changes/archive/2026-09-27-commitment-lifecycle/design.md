## Context

`public.owner_conditions` already uses the shared, table-parameterized
`butlers.core.condition_ledger` lifecycle engine. Today an episode can resolve
only when an authoritative producer submits a complete snapshot that omits its
fingerprint. That is correct for re-observable conditions, but it cannot close
a commitment after an owner-confirmed outcome because no producer can prove
absence of that commitment.

This delivery is deliberately limited to task 1 of `commitment-lifecycle`: an
explicit, in-process resolution primitive implementing
`REQ-owner-condition-ledger-004`. It makes no MCP tool available, does not
create commitment records, and does not introduce extraction, jobs, dashboard
behavior, migrations, or live actions. In particular,
`REQ-owner-condition-ledger-005` belongs to `bu-vdv7j`; tasks 2-6 remain
separate deliveries and require their own implementation-ready handoffs.

## Goals / Non-Goals

**Goals:**

- Resolve one active (`open` or `aging`) condition by its existing `(source,
  fingerprint)` identity without requiring a producer snapshot.
- Preserve the ledger's transaction-scoped advisory-lock serialization and
  append-per-recurrence history.
- Add caller-supplied resolution evidence without replacing creation-time or
  identity-provenance metadata.
- Expose the same primitive through the narrow `owner_conditions` facade.

**Non-Goals:**

- Adding `resolve_owner_condition`, any MCP tool, LLM behavior, or transport
  integration.
- Adding the commitment helper, extraction, escalation job, dashboard UI, a
  new table, a migration, backfill, or historical data rewrite.
- Changing `reconcile_snapshot()`'s observation, completeness, escalation, or
  recurrence semantics.
- Inferring a resolution from arbitrary evidence or allowing callers to alter
  creation-time identity/evidence fields.

## Decisions

### Resolve through the existing ledger transaction and lock domain

`condition_ledger.resolve_condition()` is a public async function with the
RFC 0026 §1 signature. `table` remains a static, caller-controlled
fully-qualified identifier, as it is for `reconcile_snapshot()`; it is never
derived from a request or tool input. The function rejects empty `table`,
`source`, or `fingerprint`, and rejects non-object resolution metadata before
acquiring a connection. It then acquires a connection and one transaction.

Inside that transaction it takes the existing lock
`pg_advisory_xact_lock(hashtext(table || ':' || source))`, obtains one stable
`now()` timestamp, and reads the matching active row. No active row means
`None`: both a never-seen identity and an already-resolved episode are
idempotent no-ops. An active row is resolved by the existing inner resolution
path and returns one `ConditionTransition` with `transition="resolved"`.

Reusing the exact lock key avoids a competing per-fingerprint lock domain.
It deliberately serializes all fingerprints for one source, matching the
source-wide completeness pass in `reconcile_snapshot()`.

### Resolution evidence is additive and creation-wins

`resolution_metadata` is an optional JSON object. It is added only at missing
top-level keys: every existing row metadata value wins on a collision. The SQL
therefore uses the semantic equivalent of
`resolution_metadata || existing_metadata`, not
`existing_metadata || resolution_metadata`, because PostgreSQL JSONB
concatenation is right-biased. In particular, `class`, `kind`, `direction`,
`counterparty_entity_id`, `confidence`, `evidence_opened`, and
`identity_payload` cannot be replaced by an explicit resolution call. New keys
such as a later task's `evidence_closed` and `resolution_reason` can be
recorded without discarding creation evidence.

"New keys" is doing real work in that sentence: creation-wins guarantees the
closing evidence lands only for as long as those keys stay new. A producer
that wrote either one at creation time would keep its own value and the
resolver's evidence would be dropped silently. Task 7 closes that by reserving
both names at the reconcile boundary, so the two rules hold together rather
than one quietly defeating the other.

The existing identity-version successor behavior remains intact. Extending
the shared `_resolve_episode()` helper must preserve its current nested
`identity_payload` update when snapshot reconciliation supplies successor
provenance, while explicit resolution supplies no successor provenance. This
keeps task 1 additive rather than creating a second resolution SQL path.

Shallow, creation-wins merging is chosen over recursive merging because this
primitive owns no nested metadata schema. Later commitment-specific code owns
the shape and validation of `evidence_closed`; it can supply that new key but
cannot rewrite opening evidence.

### Concurrency is defined by competing resolution attempts

The required race proof starts `resolve_condition()` and an empty, complete
`reconcile_snapshot()` for the same source from a synchronization barrier.
Both are attempting to resolve the same active row. The lock winner returns
the sole resolved transition; the loser observes no active row and returns its
established no-op result. A bounded test timeout proves the test did not merely
hang. The assertions are one resolved episode, no remaining active row, and no
deadlock.

A snapshot that re-observes the fingerprint is not treated as a duplicate
resolution race. After an explicit resolution, a later complete snapshot that
observes that identity follows the established recurrence rule and creates
episode N+1. That behavior gets its own regression, so the new primitive does
not silently alter the ledger's existing lifecycle contract.

### The owner facade is the only added public surface in this task

`butlers.core.owner_conditions.resolve_condition()` binds
`table="public.owner_conditions"`, re-exports the function in `__all__`, and
otherwise remains a thin facade. The generic core helper remains available for
the later commitment implementation, but task 1 adds no Switchboard module,
MCP registration, role change, or HTTP/API route.

## Risks / Trade-offs

- **Metadata collision could erase opening evidence.** Use creation-wins
  top-level merging and assert protected-key preservation against a real
  PostgreSQL JSONB row.
- **A resolver and complete snapshot could race.** Reuse the existing
  source-scoped advisory lock and prove exactly one resolution transition with
  parallel real-Postgres calls.
- **A new helper could diverge from snapshot resolution.** Route both paths
  through `_resolve_episode()` and retain the existing successor-provenance
  behavior.
- **The generic `table` argument could become an SQL injection seam.** Keep
  the current static-caller-only contract; no request, MCP, or dashboard input
  reaches it in this task.

## Verification and Rollback

Tests cite `REQ-owner-condition-ledger-004` and cover: both `open` and `aging`
resolution with metadata preservation; `None`, absent, and already-resolved
idempotence; empty-argument and non-object-metadata rejection before pool
access; facade export; an empty complete-snapshot race with a start barrier and
bounded timeout; post-resolution recurrence as episode N+1; and preservation
of the existing identity-successor provenance behavior. The race and JSONB
behavior execute against the migrated PostgreSQL fixture rather than a mocked
pool. Focused unit tests cover argument validation and facade binding. Public
function docstrings describe the generic table contract and the creation-wins
merge rule.

No schema migration, configuration change, deployment action, or live runtime
invocation is part of this task. The primitive intentionally mutates a ledger
row only when a future caller invokes it. Rollback is a normal code revert; it
does not delete or rewrite already-resolved ledger rows or their retained
metadata.

## Open Questions

None for task 1. The MCP authorization and validation contract, commitment
metadata schema, extraction confidence policy, escalation delivery, and
dashboard rendering belong to later tasks and must not be inferred from this
primitive.
