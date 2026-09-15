## Context

Calendar source rows are intentionally materialized in every calendar-enabled schema. The versioned read model truthfully returns that fan-out, but the dashboard router currently selects the first duplicate by incidental schema ordering and invokes every core-capable copy during global sync. In the live deployment, that turns one click into 48 provider/mirror calls, which outlives the frontend's 15-second timeout and produces provider rate-limit errors.

The normal calendar sync poller is disabled by default, so setting its event cannot implement a reliable manual queue. `calendar_action_log` is already schema-local, durable, idempotency-aware, and visible through the workspace audit surface; it can hold control commands without a new table.

## Goals / Non-Goals

**Goals:**

- Preserve every physical source and cursor row while exposing one deterministic operational owner per logical provider source on aggregate dashboard surfaces.
- Reduce global manual sync to one acknowledged command per selected owner, with the owner performing one pull-all/mirror operation rather than one operation per copied source row.
- Make the dashboard acknowledgement quick and durable: a daemon restart resumes active/pending manual sync work instead of silently dropping it.
- Coalesce redundant manual clicks, serialize provider work per owner, and preserve `full=true` as a recovery-strength request.
- Keep source freshness and action-log records as the source of truth for completion and errors.

**Non-Goals:**

- Deleting, migrating, or rewriting stale Finance/provider ledger rows.
- Changing OAuth credentials, provider rate-limit configuration, or normal polling cadence.
- Introducing a general-purpose job system, cross-schema direct writes, or a new table.
- Claiming that a queued acknowledgement means provider work completed.

## Decisions

### Keep fan-out truth in the read model; choose operational ownership in the router

`query_calendar_sources()` continues to return every row with its `db_butler` provenance. A router helper groups duplicate provider rows by `source_key` and ranks them by enabled state, core-tool availability, most recent successful/sync timestamp, then deterministic schema/id ties. This makes the dashboard’s source rail, freshness plaque, writable-calendar selection, and global sync target selection agree without hiding evidence or coupling the read-model v1 contract to dashboard policy.

Alternative considered: delete or reassign stale Finance rows. Rejected because ledger provenance may be needed for diagnosis and a data mutation would not prevent future duplicate fan-out.

### Queue commands in the owning CalendarModule, not the dashboard API

The dashboard calls `calendar_force_sync(queue=true, request_id=...)` through MCP and awaits only durable acceptance. The CalendarModule inserts a `calendar_force_sync` action-log command in its own schema, then wakes an always-running command drainer. The tool’s default `queue=false` preserves direct inline operator/tool semantics.

Alternative considered: FastAPI `BackgroundTasks` or `asyncio.create_task`. Rejected because API-process tasks are best effort and are lost on dashboard restart before completion can be truthfully reported.

### Reuse `calendar_action_log` with an explicit active lifecycle

The action log gains `running` in its status constraint and a partial unique index permitting at most one pending command per owner. A worker atomically claims the oldest pending command as `running`, performs the existing force-sync path, and records `applied` or `failed` with its result. On startup, interrupted `running` commands are returned to `pending` and drained; graceful cancellation does the same. A request arriving while an equivalent/incremental command runs is coalesced; a full recovery request can upgrade or queue the next pending command so recovery intent is never downgraded.

Alternative considered: a new queue table. Rejected because the action log already provides durable status, request correlation, audit visibility, and migration-safe schema locality.

### Batch global sync by selected owner

After canonical provider selection, global sync groups rows by `db_butler` and sends one queued `calendar_force_sync` without `calendar_id` to each owner. The existing tool intentionally pulls all calendars and pushes internal mirrors once for that owner. A source-scoped request remains source-specific and passes its calendar id.

Alternative considered: one global owner regardless of source provenance. Rejected because it could omit a calendar whose only eligible operational copy belongs to another owner.

### Make acknowledgement/completion separate API states

The workspace endpoint returns HTTP `202 Accepted` and `queued` targets with request correlation and coalescing information. It does not set `recovery=true` until completion, so the UI says “queued” rather than “ran.” Existing source freshness and audit/action telemetry are refreshed/polled to show terminal state.

## Risks / Trade-offs

- [A long provider call can still fail or take time] → The command is not held in the browser request; terminal errors are recorded in the source cursor/action log.
- [A daemon restarts during a command] → Startup recovers `running` commands to pending and the drainer resumes them; duplicate provider work is preferable to silently losing a manual recovery request.
- [Two manual clicks race] → A schema-local partial unique pending index and atomic claim/coalescing path allow at most one pending successor per owner.
- [Old code is live during migration] → The migration is additive to the status vocabulary/index; no row is written as `running` until the new daemon is deployed. Downgrade maps interrupted `running` rows back to `pending` before restoring the old constraint.
- [Some provider sources legitimately belong to different owners] → Group by canonical owner rather than assume a single global owner.

## Migration Plan

1. Apply an idempotent core migration in each schema: extend `calendar_action_log` status validation to `running` and create the partial pending force-sync uniqueness index.
2. Deploy the CalendarModule queue/drainer code. Existing action logs need no backfill; startup recovers only `calendar_force_sync` commands.
3. Deploy dashboard API/client code that requests queueing and renders `202`/`queued` acknowledgement honestly.
4. Verify one live manual click produces one accepted command per canonical owner, then inspect action/freshness telemetry after completion. No source/cursor data migration is performed.
5. Rollback: deploy the prior code only after allowing active commands to finish or resetting `running` to `pending`; migration downgrade removes the partial index and restores the prior constraint without deleting action history.

## Open Questions

- None for the initial implementation. The dashboard’s existing source freshness polling and audit surface are sufficient completion observability; a dedicated command-status endpoint can be evaluated later if operators need per-command progress UI.
