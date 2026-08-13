## Why

The context bus currently lets General and Switchboard write `dnd` through the
same row-upsert API used for ordinary situational signals. A Health-owned
wake-recovery admission cannot safely rely on a DND observation from that API:
there is no durable, monotonic version that a later admission can compare, and
a writer can change DND between a read and an irreversible delivery decision.

This prerequisite makes the DND safety boundary explicit before the
owner-Telegram wake-recovery protocol is implemented. It preserves the existing
shared-awareness model and prevents a Health-private state store or direct
cross-schema shortcut from becoming an accidental alternative authority.

## What Changes

- Define one canonical, durable DND generation guard in the shared context-bus
  boundary. Every successful canonical General or Switchboard DND mutation
  advances that generation atomically with its context-row effect.
- Define replay-safe mutation correlation, writer authorization, a durable
  receipt/audit shape, and failure-closed behavior for missing, stale, or
  exhausted guard state.
- Define Health and future Messenger admission consumers' snapshot and
  serialization contract: an admission verifies the captured generation and
  current inactive DND state while holding the shared guard lock through its
  own durable admission record.
- Make TTL expiry, concurrent writers, stale readers, restart recovery, and
  generation exhaustion explicit. The change introduces no wake release,
  scheduler cancellation admission, provider egress, or live-data backfill.
- Amend RFC 0009 with the shared DND-generation contract, including the
  trusted cluster-superuser installer/finalizer handoff and its restricted
  pre-consumer rollback route.
- Add an executable downstream contract-test matrix to the change design/tasks,
  including the required real-PostgreSQL role/catalog proofs. This source-only
  change does not execute that bootstrap, migration, or test environment.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `context-bus`: adds the canonical DND mutation, generation snapshot, and
  serialized admission requirements.
- `database-security`: defines the least-privilege public guard and mutation
  boundary required to prevent DND writes from bypassing the generation.

## Impact

This PR implements the repository-owned prerequisite only: the canonical
OpenSpec/RFC contract, the trusted bootstrap source and ordinary core migration
source, the context-bus DND mutation path, and focused source/unit checks. The
cluster-superuser bootstrap installer/finalizer is the only component permitted
to create or hand off the authority objects; a separate fixed rollback route is
limited to a planned superuser downgrade before any durable mutation exists. The
ordinary upgrade merely catalog-validates a trusted interface and invokes the
fixed installer; only its planned privileged downgrade invokes the restricted
rollback route. The PR does not execute `scripts/init-db.sql`, Alembic, a
database or testcontainer, Compose, a deployment, a restart, a live DND
mutation, a provider call, or a data backfill. Real-PostgreSQL role/catalog and
concurrency proofs remain explicit execution gates before any wake-recovery
consumer is enabled.
