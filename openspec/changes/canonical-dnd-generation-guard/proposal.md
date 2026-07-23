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
- Amend RFC 0009 with the shared DND-generation contract and add an executable
  downstream contract-test matrix to the change design/tasks.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `context-bus`: adds the canonical DND mutation, generation snapshot, and
  serialized admission requirements.
- `database-security`: defines the least-privilege public guard and mutation
  boundary required to prevent DND writes from bypassing the generation.

## Impact

This PR is a specification and RFC prerequisite only. It changes
`about/legends-and-lore/rfcs/0009-situational-context-bus.md` and creates the
OpenSpec change artifacts; it intentionally does not add a migration, runtime
API, wake-recovery behavior, provider call, or data migration. The subsequent
implementation will touch the context-bus mutation path, a guarded public
schema migration, and focused contract/integration tests.
