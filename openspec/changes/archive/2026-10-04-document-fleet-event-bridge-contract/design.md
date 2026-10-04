## Context

See `proposal.md` for the maintenance authorization and baseline. RFC 0022 records the accepted transport contract; [Observed] its broad producer/listener path is implemented, with the discrepancies below; no active delta supplies a fleet-event spec. Its runtime helper, listener, producers, and frontend cache registry remain untouched.

## Goals / Non-Goals

The adopted WHAT moves to one `core-fleet-events` main spec. The RFC retains the accepted PostgreSQL transport choice, its rationale, and alternatives. This is not new feature approval or an assertion that presence of a test citation certifies every clause of a requirement.

## Decisions

- Keep all existing transport and producer obligations `v1-mandatory`. No uncovered behavior is relabeled post-v1.
- Use existing behavior tests for requirement-ID citations; comments identify the seam each test actually exercises. No extra tests, new gate species, or test logic are introduced.
- Scaffold and archive with the repository-pinned OpenSpec CLI (1.9.0). Its new-capability Purpose template conflicts with the shared trace check's delta-heading rules, so the delta contains ADDED requirements only and the generated main-spec Purpose is filled after archive.
- Run strict trace checks on a temporary projection of this capability and its cited test files. Repo-wide strict trace checks include legacy specs without metadata and are not this task's acceptance scope. The projection copies real spec and test content, never synthetic citation fixtures.

### Source and test map

The IDs below have prefix `REQ-core-fleet-events-`. Test names are existing gates, not new evidence claims.

| IDs | Source paths | Existing test evidence |
| --- | --- | --- |
| 001, 002 | `src/butlers/fleet_events.py` | `tests/core/test_fleet_events.py`: exact envelope/default data, rejected oversized/serialization payloads, swallowed execute failure |
| 003 | `src/butlers/db.py`, `api/deps.py`, `api/fleet_events_bridge.py` | `tests/api/test_fleet_events_bridge.py::test_listener_daemon_publisher_and_api_pools_resolve_the_same_database_target` and pathless URL rejection tests |
| 004 | `api/fleet_events_bridge.py`, `api/routers/events.py` | bridge valid-envelope test; `tests/api/test_events.py` live/snapshot tests; `tests/integration/test_fleet_events_notify_bridge.py::test_calendar_and_chronicler_child_processes_reach_websocket` |
| 005 | `api/fleet_events_bridge.py` | bridge wrong-channel, malformed/non-object/missing-type, and non-dict-data tests |
| 006 | `api/fleet_events_bridge.py`, `api/app.py` | fake listener register/reconnect tests; `tests/api/test_app_lifespan_supervision.py` startup/shutdown wiring; real-Postgres reconnect test |
| 007 | `fleet_events.py`, frontend bus-aware poll hook | publisher failure isolation; `frontend/src/hooks/use-bus-aware-poll-interval.test.tsx` healthy reconciliation and degraded fallback |
| 008 | `core/sessions.py`, `core/spawner.py`, `core_tools/_notifications.py`, approvals gate/email guard | session lifecycle tests in `tests/api/test_events.py`; spend producer/failure tests in `tests/core/test_core_spawner.py`; creation/failure tests in `tests/modules/test_gate.py` and `test_email_guard.py` |
| 009 | Switchboard `tools/ingestion/ingest.py` | `roster/switchboard/tests/test_ingest_ingestion_events.py::TestIngestionEventPublishesOnBus`: exact bounded metadata and duplicate suppression |
| 010 | `connectors/filtered_event_buffer.py` | `tests/connectors/test_filtered_event_buffer.py`: write/release/publication ordering, empty payload, failed/empty write silence, publication failure does not retry |
| 011 | `modules/calendar.py` | `tests/modules/test_module_calendar.py`: provider projection/counts, internal projection, empty-sweep suppression; child-process transport test |
| 012 | `chronicler/jobs.py` | `tests/jobs/test_chronicler_jobs.py`: exact material/promotion-only payload, empty/skipped suppression, adapter-error rejection; child-process transport test |
| 013 | frontend cache registry and ingestion hook | `event-cache-registry.test.ts`: ingestion/Calendar/Chronicler invalidations; `use-ingestion-events.test.tsx`: active polling without bus events |

## Risks / Trade-offs

- Citation presence is a mechanical lower bound, not whole-contract coverage. [Observed] the existing oversize test uses 8500 characters, not the exact 7800-byte boundary; malformed tests do not directly exercise non-string `type`; producer tests do not inject commit-order failures for Switchboard/Calendar, exercise the `notify()` publication callback, or prove the absence of daemon-local emitter imports. Startup tests exercise successful supervision and shutdown, not an import-time bridge-start failure. These clauses remain mandatory and source-backed; coverage gaps are follow-up work, not silently deferred obligations.
- [Observed] publication awaits the caller's database operation and has no explicit timeout, despite the RFC's “never block” obligation. Foreign-channel callbacks return silently, despite the RFC failure table requiring drops to be logged. Both accepted obligations remain mandatory in requirements 002 and 005; this documentation-only change neither claims implementation compliance nor adds a timeout or logging behavior. Separate runtime follow-ups must resolve those discrepancies.
- [Observed] transport tests prove independent PostgreSQL connections; the Calendar/Chronicler harness additionally proves child processes and real WebSocket frames. Neither certifies Compose deployment, browser connectivity, durable NOTIFY replay, or every producer's full business transaction.
- Authorization remains the existing dashboard WebSocket contract. This transport creates no new ingress credentials or approval policy; accessibility and migrations are unchanged because no UI or schema is modified.

## Migration Plan

Sync the new spec and archive this change using OpenSpec, then replace the generated placeholder Purpose with the observed capability description. Reverting the documentation commit restores the former RFC/spec arrangement; runtime state and data require no migration.
