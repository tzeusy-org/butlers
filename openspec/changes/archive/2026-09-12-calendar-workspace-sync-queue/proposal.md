## Why

Calendar Workspace currently treats duplicate provider-source ledger rows as separate operational owners. A single Sync now request fans out across every core-enabled schema, exceeds the browser's 15-second request budget, and causes rate-limited duplicate provider work, while a stale non-core copy can make the UI say “Never synced” despite a healthy duplicate.

## What Changes

- Select one deterministic, sync-capable operational owner for each duplicate provider `source_key` on aggregate Calendar Workspace read and global-sync surfaces, without deleting or rewriting ledger rows.
- Change dashboard-initiated manual sync from an inline provider operation to a durable per-owner command lifecycle: queued work is coalesced, serialized, resumed after daemon restart, and recorded as `pending`/`running`/terminal in the existing calendar action log.
- Return HTTP `202 Accepted` and per-owner queued targets from `POST /api/calendar/workspace/sync`; completion remains visible through the action log and source freshness rather than an untruthful immediate “completed” response.
- Preserve direct MCP `calendar_force_sync` inline behavior unless the dashboard explicitly requests queueing, and preserve `full=true` as a cursor-recovery request.

## Capabilities

### New Capabilities

- None.

### Modified Capabilities

- `module-calendar`: Define durable queued force-sync command acceptance, coalescing, execution, and restart recovery alongside the existing inline tool behavior.
- `dashboard-api`: Define canonical source-owner selection and HTTP `202` queued sync acknowledgements for Calendar Workspace.

## Impact

- `src/butlers/modules/calendar.py` gains durable command processing backed by `calendar_action_log`.
- `alembic/versions/core/` evolves the action-log status vocabulary and active-command uniqueness safely for every schema.
- `src/butlers/api/routers/calendar_workspace.py`, API models, frontend types/UI, and their tests change to use queued per-owner acknowledgements.
- `docs/frontend/backend-api-contract.md` documents the new acknowledgement/completion boundary. No new external provider, credential, or dependency is introduced.
