# bu-v9jyf: Home Lane Connector Table Verification

**Date:** 2026-04-30
**Issue:** bu-v9jyf — verify(chronicler): confirm Home lane connector table exists and is populated in production
**Discovered during:** bu-3zagb gen-1 reconciliation

---

## Summary

`connectors.home_assistant_history` does **not exist** in the dev/prod database. No Alembic
migration creates it. The HA connector is running and connected to HA (dev evidence below), but
the connector's current implementation never writes to this table — a critical piece of the
write path is simply not implemented yet.

---

## Evidence

### 1. Database probe — table absent

Queried the dev database (`butlers-db-dev.parrot-hen.ts.net`) directly from the running
`butlers-dev-connector-home-assistant-1` Docker container:

```
=== connectors schema tables ===
  connectors.filtered_events
  connectors.filtered_events_202603
  connectors.filtered_events_202604
  connectors.filtered_events_202605
  connectors.owntracks_points
  connectors.spotify_listening_sessions
  connectors.steam_cursors
  connectors.steam_play_history

=== home_assistant_history exists: False ===
```

`connectors.home_assistant_history` is absent. The table was **never created**.

### 2. No Alembic migration exists

The comparable tables for other connectors have dedicated Alembic migrations:

| Connector | Evidence table | Migration |
|-----------|---------------|-----------|
| OwnTracks | `connectors.owntracks_points` | `core_081_owntracks_points.py` |
| Steam | `connectors.steam_play_history` | `core_011_steam_play_history_fix.py` |
| Spotify | `connectors.spotify_listening_sessions` | (separate migration) |
| **Home Assistant** | `connectors.home_assistant_history` | **NONE** |

Searched `alembic/versions/core/` — no file references `home_assistant_history` or creates
that table. The directory's last migration is `core_083_contact_info_context.py`.

Applied DB head is `core_083` (confirmed from `chronicler.alembic_version`).

### 3. Connector is running but events are never written

The HA connector (`src/butlers/connectors/home_assistant.py`) is an active Docker service and
**is connected** to the real HA instance:

```
15:55:28 [info] HAWebSocketClient: WebSocket connected and authenticated.
endpoint_identity=home_assistant:homeassistant.parrot-hen.ts.net:443
```

However, the current `_main()` entrypoint uses a `_null_dispatch` stub:

```python
# src/butlers/connectors/home_assistant.py  (lines 1376-1389)
async def _null_dispatch(event_type: str, event: dict[str, Any]) -> None:
    """No-op event dispatch placeholder.

    In a full implementation (tasks 5–6), this would route events through
    the three-layer filter pipeline and construct ingest.v1 envelopes.
    Tasks 5–7 (REST fallback, filtering, envelope construction, checkpoint)
    remain pending; this stub ensures the connector loop and health state
    are exercised in the meantime.
    """
    logger.debug(
        "HAConnector: event received (type=%r, not yet dispatched to pipeline)",
        event_type,
    )
    connector.on_event_received(passed_all_filters=False)
```

The openspec `tasks.md` for the HA connector confirms Tasks 5–9 (filtering, envelope
construction, checkpoint, filtered-event persistence) are not yet implemented — they are all
unchecked `[ ]` items in
`openspec/changes/archive/2026-03-28-connector-home-assistant/tasks.md`.

There is no writer anywhere in `src/butlers/connectors/home_assistant*.py` that inserts into
`connectors.home_assistant_history`. The `HAFilterPipeline`, `HAWebSocketClient`, and
`HAConnector` classes are implemented, but the integration path that would persist state-changed
events to the evidence table has never been wired up.

### 4. Adapter silently skips — confirmed expected

`src/butlers/chronicler/adapters/home_assistant.py` (lines 157–168) uses
`information_schema.tables` to check whether the table exists before querying it, and returns
`skipped=True` when absent:

```python
exists = await conn.fetchval(
    "SELECT EXISTS (SELECT 1 FROM information_schema.tables
     WHERE table_schema = 'connectors'
       AND table_name = 'home_assistant_history')"
)
if not exists:
    return None   # → AdapterResult(skipped=True)
```

The docstring confirms this is intentional: _"Missing evidence table degrades gracefully
(module not enabled / migration not run on this deployment)."_

The adapter is marked `optional_schema=True` in
`src/butlers/chronicler/contracts.py` — silently returning empty results is by design.

---

## Root-Cause Analysis

Three separate gaps compound to produce a permanently empty Home lane:

| Gap | Location | Status |
|-----|----------|--------|
| **No DDL migration** for `connectors.home_assistant_history` | `alembic/versions/core/` | Missing — never created |
| **No connector write path** — HA events never written to the evidence table | `src/butlers/connectors/home_assistant.py` | Tasks 5–9 of HA openspec not implemented |
| **Connector task list** (tasks 5–7) not yet filed as beads | `openspec/changes/archive/2026-03-28-connector-home-assistant/tasks.md` | Unimplemented |

The Chronicler's Home lane adapter is correctly instrumented and will work as soon as both gaps
are fixed. The adapter code is complete and correct (`HomeAssistantHistoryAdapter`).

---

## Required Follow-Up Work

Two work items are needed to close this gap:

### FU-1: Add `connectors.home_assistant_history` migration (task analogue to `core_081`)

A new core Alembic migration (e.g. `core_084_home_assistant_history.py`) must:
- `CREATE TABLE connectors.home_assistant_history (id, entity_id, state, attributes, recorded_at, ...)`
- Grant `SELECT, INSERT, UPDATE, DELETE` to `connector_writer`
- Grant `SELECT` to `butler_chronicler_rw`
- Match the columns expected by `HomeAssistantHistoryAdapter._fetch_rows()`:
  `id`, `entity_id`, `state`, `attributes`, `recorded_at`

### FU-2: Wire the HA connector write path (tasks 5–9 of openspec)

The HA connector's `_null_dispatch` must be replaced with a real dispatcher that:
- Runs the three-layer filter pipeline (already implemented in `HAFilterPipeline`)
- Constructs `ingest.v1` envelopes
- Persists raw `state_changed` events for `person.*` entities to `connectors.home_assistant_history`
- Updates the checkpoint

Until FU-2 is complete, even creating the migration (FU-1) alone will result in an empty table.
Both FU-1 and FU-2 are required for the Home lane to produce any data.

---

## Acceptance Criteria — Status

| Criterion | Result |
|-----------|--------|
| Status of `connectors.home_assistant_history` documented | ✓ Absent — table never created, no migration |
| Root cause identified | ✓ Two-gap: missing DDL migration + connector write path not implemented (tasks 5–9 pending) |
| Findings captured for gen-2 reconciliation action | ✓ This document |
