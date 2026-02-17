# Switchboard message_inbox Partition Migration (`sw_008`)

## Scope
`sw_008` rewrites `message_inbox` from the legacy flat table (with `sw_005` base columns and `sw_006` ingress dedupe columns) into a month-partitioned lifecycle store.

New lifecycle payload columns:
- `request_context` (JSONB, includes source identity and dedupe metadata)
- `raw_payload`
- `normalized_text`
- `decomposition_output`
- `dispatch_outcomes`
- `response_summary`
- `lifecycle_state`
- `schema_version`
- `processing_metadata`
- `final_state_at`

## Upgrade Behavior
1. Rename `message_inbox` to a temporary backup table (`message_inbox_sw007_backup`).
2. Create partitioned `message_inbox` (`PARTITION BY RANGE (received_at)`).
3. Install partition maintenance functions:
   - `switchboard_message_inbox_ensure_partition(reference_ts)`
   - `switchboard_message_inbox_drop_expired_partitions(retention default '1 month', reference_ts)`
4. Backfill all `sw_007` rows (including `sw_006` dedupe columns) into the canonical `sw_008` shape. Dedupe metadata is preserved inside `request_context`.
5. Apply one-month retention cleanup.

## Default Retention Policy
- Default hot retention is one month (`INTERVAL '1 month'`).
- Write paths should call partition maintenance before inserts.

## Rollback (Downgrade to `sw_007`)

To downgrade back to the pre-partition schema:

```bash
uv run alembic downgrade switchboard@sw_007
```

Downgrade reconstructs the legacy `sw_007` schema from `sw_008` records by:
1. Renaming partitioned `message_inbox` to backup.
2. Recreating legacy columns/indexes (including `sw_006` dedupe columns and indexes).
3. Backfilling legacy-compatible fields from canonical payloads.
4. Dropping partition maintenance functions.

The downgrade path is covered by migration integration tests.
