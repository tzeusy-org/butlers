## Context

`MessagePipeline` already contains the entity-first identity hook, but
`wire_pipelines()` constructs it with the default
`enable_identity_resolution=False` and no `notify_owner_fn`. The helper
creates a transitory `public.entities` row but its inactive notification path
still emits a legacy contacts link and does a non-atomic read-then-write against
the nonexistent `butler_state` name.

The authoritative entity contract requires one content-free owner-facing
attempt through the normal delivery boundary, an Unidentified Entities review
link, and no repeat storm after either an accepted claim or a delivery failure.
The real per-butler durable KV table is Switchboard's existing `state` table.

## Goals / Non-Goals

**Goals:**

- Enable identity resolution solely for the production Switchboard pipeline.
- Deliver an owner-facing `notify.v1` request through Switchboard's existing
  `deliver()` → Messenger path.
- Reserve an atomic, durable per `(channel_type, channel_value)` claim before
  the delivery attempt, so only the winner can notify.
- Preserve entity-only semantics, safe review routing, observability, and
  fail-open ingress routing.

**Non-Goals:**

- Reintroducing `public.contacts`, `public.contact_info`, or a `contact_id`
  dependency.
- Adding a migration, an outbox, retries, new delivery preferences, or a
  generic notification subsystem.
- Changing the dashboard route or unrelated identity documentation.

## Decisions

### Use the existing notify.v1 Switchboard-to-Messenger boundary

The wiring callback resolves the configured Telegram owner recipient through
the daemon's existing resolver, constructs a `notify.v1` send envelope, and
calls Switchboard `deliver()` with that envelope. This preserves Messenger's
recipient validation, route logging, and standard delivery path without a
model-facing `notify()` call or direct connector invocation.

Direct connector delivery was rejected because it would bypass the established
control-plane boundary. A new generic callback service was rejected because it
would broaden this activation slice.

### Claim before delivery with the existing Switchboard state row

The helper performs one `INSERT INTO state ... ON CONFLICT DO NOTHING
RETURNING` for `identity:unknown_notified:<channel>:<value>`. A returned row is
the sole permission to make the delivery attempt. The successful claim remains
after delivery failure, intentionally providing at-most-one attempt and
preventing a retry storm. If the state operation fails, the helper logs a
warning, skips delivery, and returns the normal unknown-sender identity result
so routing continues.

A read-then-write check was rejected because concurrent ingress can notify more
than once. Retrying after delivery failure was rejected because the contract
prioritizes bounded owner interruption; durable retries would require a scoped
outbox design and migration that are explicitly out of scope.

### Reserve the transitory entity before pipeline activation

The notification claim prevents duplicate owner sends, but it does not close
the separate mint-to-fact gap: the relationship-owned channel fact is asserted
only after identity resolution returns to `MessagePipeline`. For each unknown
sender, the Switchboard passes an opt-in state key to `create_temp_contact()`.
Inside one transaction, that helper inserts or locks the corresponding
Switchboard-local `state` row, reuses a committed entity UUID when present, or
inserts `public.entities` and records its UUID before committing. A competing
first message waits on the state row and then reuses the same entity.

This reservation remains deliberately separate from the notification claim and
from `relationship.entity_facts`: it writes only Switchboard state and the
entity row. The existing deterministic relationship-owned fact assertion stays
after identity resolution in the pipeline. If the reservation cannot be
persisted, temporary-entity creation returns no entity and the existing
fail-open routing path proceeds without an owner-notification claim. A stale
reservation whose entity no longer exists is repaired while holding the same
state-row lock before a replacement is minted.

### Use the canonical Unidentified Entities route and a safe label

The notice points to `/entities/index?state=unidentified`, the actual frontend
review route. It renders a normalized source display name when one is available
and otherwise uses `Unknown sender`; it never includes the inbound body, raw
channel identifier, contact ID, or legacy contacts route.

## Risks / Trade-offs

- **[A process can stop after the durable claim but before transport begins]**
  → The contract is deliberately one bounded attempt rather than a retrying
  outbox; the transitory entity remains visible for dashboard review.
- **[State or delivery infrastructure is unavailable]** → Log the failure and
  continue normal ingress routing without an unclaimed delivery attempt.
- **[Telegram owner recipient is not configured]** → Treat it as the bounded
  failed attempt after the claim; do not block routing or repeat on every
  message.
- **[A transitory entity is manually deleted while its reservation remains]**
  → Validate the reserved UUID under the state-row lock, clear a stale value,
  and mint one replacement entity in the same transaction.

## Migration Plan

No schema migration is required. Deploy the wiring and helper together; the
existing `state` primary key serializes both competing notification claims and
the per-sender transitory-entity reservation. Rollback returns the pipeline to
its prior inactive behavior, while any completed claim or reservation remains
harmless because the transitory entity itself remains reviewable in the
dashboard.
