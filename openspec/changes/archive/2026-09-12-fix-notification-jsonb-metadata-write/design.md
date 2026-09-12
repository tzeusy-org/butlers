## Context

`log_notification()` is the production Switchboard writer reached by the
delivery paths. Every repository-managed asyncpg pool registers a JSONB codec
whose encoder serializes its bound Python value once. The current writer
serializes `metadata` before binding it, so the codec receives a string and
persists a JSONB string instead of an object.

## Goals / Non-Goals

**Goals:**

- Ensure new notification metadata writes are JSONB objects.
- Preserve JSON-safe metadata content, including values that require string
  normalization for JSON encoding.
- Protect the production writer with a real Postgres regression rather than a
  mocked-pool assertion.

**Non-Goals:**

- Backfilling, repairing, or read-normalizing existing string-shaped rows.
- Changing notification API/UI output, status vocabulary, provenance or
  request-id propagation, retry behavior, Messenger integration, schema,
  migrations, quiet-window behavior, broker/scheduler behavior, secrets, or
  retention.

## Decisions

### Normalize in memory, then bind the mapping directly

`log_notification()` will use the repository-standard
`json.loads(json.dumps(..., default=str))` round trip only to produce a
JSON-safe Python mapping. It will pass that mapping directly to the INSERT
parameter; it will not pass a pre-serialized JSON string or require an
explicit JSONB cast. This lets the registered asyncpg codec perform the one
wire-level serialization.

Binding the raw mapping was rejected because non-JSON-native values can reach
metadata. Pre-serializing the mapping was rejected because it is the source of
the JSONB-string corruption. A new shared serializer was rejected because the
repository already has a narrow, established normalization pattern.

### Exercise the actual writer against a real pool

The regression will use the existing Switchboard integration fixture, which
provides a real Postgres pool with the production JSONB codec. It will invoke
`log_notification()`, then query the persisted row for both
`jsonb_typeof(metadata)` and the decoded value. This proves the driver and SQL
boundary, which a mocked `fetchrow()` call cannot cover.

### Leave legacy data untouched

The write correction prevents new corruption only. Read-side compatibility and
historical repair are separate planned slices, so this change deliberately
does not add a coercion path or migration.

## Risks / Trade-offs

- **[Risk]** Integration coverage requires Docker and can be skipped on a
  developer machine without it. **Mitigation:** retain the repository's
  standard integration marker and run the focused test plus final required
  gates where Docker is available.
- **[Risk]** Existing JSONB-string rows remain string-shaped. **Mitigation:**
  state that boundary explicitly; later repair work can act on historical data
  without mixing it into this write-only change.

## Migration Plan

No migration or data repair is required. Deploying the code changes only new
writes. Rollback is a code rollback; neither path mutates historical rows.

## Open Questions

None. The existing JSONB codec and real-pool fixture establish the required
writer and regression boundaries.
