## Context

The canonical settings endpoint is already implemented at
`/api/ingestion/connectors/{type}/{identity}/settings`. `flush_interval_s` is
read by the batch connector's flush scanner without a restart, while other
settings can retain their connector-specific reload behavior.

## Goals / Non-Goals

**Goals:**

- Give the settings lifecycle one canonical requirement name and route.
- Preserve the archive guard's evidence that the retired requirement was
  intentionally replaced rather than silently edited.

**Non-Goals:**

- Changing the endpoint payload, connector runtime, migration schema, or
  provider-owned configuration APIs.

## Decisions

The old requirement is removed and a new named requirement is added rather
than mutating the archived wording in place. The archive-integrity guard treats
an archived removal as an explicit supersession, while a later textual edit
would otherwise make the old archived clause appear never to have landed.

The batch-settings requirement is modified in full so its existing scenarios
remain intact while its endpoint name changes.

## Risks / Trade-offs

- [A later archive overwrites the replacement] → the only active modified
  connector-base requirement is rebuilt against the current baseline in the
  same delivery and verified by the overwrite guard.
