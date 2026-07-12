# RFC 0022: Cross-Process Event Transport (NOTIFY/LISTEN Fleet Event Bridge)

**Status:** Accepted
**Date:** 2026-07-12

## Summary

Daemon-originated live events (session lifecycle, per-call spend, `notify()` deliveries, approval gate decisions) are published from the `butlers-up` container via `SELECT pg_notify(channel, payload)` on the daemon's own database pool. The dashboard-api container LISTENs on that same Postgres channel and bridges every NOTIFY back into its existing in-process fleet event bus (`butlers.api.routers.events.emit_event`, `WS /api/events/stream`), so every existing WebSocket consumer keeps working unchanged. The transport is additive: it does not replace or delete the pre-existing in-process `emit_event()` / `emit_spend_event()` / `emit_approvals_event()` call sites in the daemon and its modules — those become genuinely dead code only once this bridge is proven in production, at which point bu-01r64.2 deletes them.

## Motivation

The dashboard-api process (`dashboard-api` / `dashboard-api-hotreload` container) and the butler daemon process (`butlers-up` / `butlers-up-hotreload` container) are separate OS processes in separate containers (`docker-compose.yml`). `butlers.api.routers.events.emit_event()` — the function backing `WS /api/events/stream` — is a plain in-process pub/sub broker: a module-level ring buffer (`_events_ring`) and a list of `asyncio.Queue` subscribers (`_events_subscribers`), both process-local Python state.

Several daemon-side and module-side call sites import and call `emit_event()` (and the older per-feature `emit_spend_event()` / `emit_approvals_event()`, which additionally fan onto the same bus) directly:

- `src/butlers/core/sessions.py` — `session` events (`phase: started|ended`)
- `src/butlers/core/spawner.py` — `spend` events (per-call cost)
- `src/butlers/core_tools/_notifications.py` — `notification` events (`notify()` deliveries)
- `src/butlers/modules/approvals/gate.py`, `src/butlers/modules/approvals/email_guard.py` — `approval` events (`created` pending actions)

Every one of these runs inside the daemon process. Calling `emit_event()` there mutates the **daemon's own, unobserved** copy of `_events_ring` / `_events_subscribers` — no WebSocket client is ever connected to that process. The call succeeds, logs nothing, raises nothing, and produces zero observable effect. The dashboard's Live indicator (which only reflects socket connectivity to the dashboard-api process) shows "connected" throughout — this is failure impersonating liveness, not an outage anyone would notice from the UI. The only visible symptom is staleness: an approval created by a daemon-side gate, a session that just finished, a notification that was just sent — none of it appears live; the page only catches up on its next poll (see bu-01r64.3 for the complementary poll-interval hardening).

Some call sites *also* emit onto older, per-feature dedicated streams (`/api/approvals/stream`, `/api/spend/stream`) via `emit_approvals_event()` / `emit_spend_event()`. Those are equally broken when invoked from the daemon process, for the identical reason — and are separately known to have zero remaining WS consumers now that the unified bus exists (bu-01r64.2 deletes those routes and the now-fully-dead upward `from butlers.api.routers.X import emit_Y_event` imports once this RFC's bridge supersedes them).

## Design

### Why Postgres NOTIFY/LISTEN

The daemon and dashboard-api already share one PostgreSQL database (RFC 0006: single-PG, multi-schema topology — every butler schema plus `public` lives in the same `butlers` database that dashboard-api also connects to). Postgres's `LISTEN`/`NOTIFY` is exactly a lightweight, no-new-infrastructure pub/sub channel scoped to a *database* (not to a schema, role, or table) — any connection to the same database can `LISTEN` on a channel any other connection `NOTIFY`s, regardless of which process, container, or schema-scoped role sent it. No new service, port, or credential is introduced; no message broker (Redis, NATS, etc.) is added to the deployment topology (RFC 0008).

The alternative considered — an HTTP callback from daemon to dashboard-api — was rejected: it would require dashboard-api to expose an authenticated ingress endpoint reachable from every daemon container, duplicating auth surface the two processes don't otherwise share, for a use case (best-effort live UI updates) that does not need request/response semantics or delivery guarantees beyond "usually arrives quickly."

### Wire Contract

**Channel:** `butlers_fleet_events` (`butlers.fleet_events.FLEET_EVENTS_CHANNEL`).

**Payload:** UTF-8 JSON, matching the exact envelope shape the in-process bus already uses:

```json
{"type": "session", "data": {"phase": "started", "session_id": "...", "butler": "general", "trigger_source": "tick", "model": "..."}}
```

`type` is one of the values in `butlers.api.routers.events.EVENT_TYPES` that a daemon process can originate: `session`, `spend`, `notification`, `approval`. `data` is whatever dict the original in-process `emit_event(event_type, data)` call would have carried — the bridge does not transform or re-shape it, so a bridged event is indistinguishable on the wire from one emitted natively inside the dashboard-api process (e.g. `header_delta`/`issue`/`attention_*`, which originate in-process and are unaffected by this RFC).

**Timestamp:** intentionally *not* part of the envelope. `emit_event()` stamps `ts` itself at the moment it runs inside the dashboard-api process (arrival time), consistent with how every other event on the bus is stamped — this RFC does not introduce a second, origin-side clock that could drift or arrive out of order relative to it.

### Publish Side (`butlers.fleet_events.publish_fleet_event`)

```python
async def publish_fleet_event(pool, event_type: str, data: dict | None = None) -> bool
```

A single shared, best-effort helper: JSON-encodes the envelope and runs `SELECT pg_notify($1, $2)` on the caller's own `asyncpg.Pool`/`Connection`. It is deliberately symmetrical with the existing `emit_event()` call sites — same call shape, same "never raise, never block the caller's real work" contract:

- **Never raises.** Every failure mode (oversized payload, non-serializable `data`, connection loss, pool exhaustion) is caught, logged at `warning` (payload-shape problems) or `debug` (transient delivery problems), and reported back only via a `bool` return value that call sites are free to ignore.
- **Payload size guard.** Postgres hard-caps a single NOTIFY payload at 8000 bytes (server-enforced — exceeding it raises `payload string too long`). `publish_fleet_event` checks the encoded size against a 7800-byte budget *before* attempting the NOTIFY and drops (logs + returns `False`) rather than risking that exception. None of today's four event shapes (session/spend/notification/approval, all small, mostly scalar fields) come close to this limit; a future event type carrying unbounded user content would need to publish a reference (e.g. a row id) rather than the full payload, not raise the cap.
- **No queuing, no replay, no delivery guarantee.** A NOTIFY sent while nobody is LISTENing (dashboard-api restarting, bridge not yet started) is simply not delivered — Postgres does not persist or queue NOTIFYs for later delivery to a channel with zero current listeners. This matches the pre-existing behavior of the in-process bus itself (a WS client that isn't connected when an event fires misses it; the ring-buffer snapshot-on-connect only covers events that *did* reach the bus) and is an acceptable loss profile for a live-UI freshness signal that is always backed by a poll-based fallback (bu-01r64.3) and the underlying durable row (the `sessions` table, `pending_actions` table, etc.) as source of truth.

Call sites publish *additively*, alongside their existing (silently-inert-from-the-daemon) `emit_event()`/`emit_spend_event()`/`emit_approvals_event()` calls, each independently wrapped so a NOTIFY failure can never affect the other:

```python
try:
    from butlers.api.routers.events import emit_event
    emit_event("session", session_event_data)          # dead when run in the daemon process
except Exception:
    logger.debug(...)

try:
    from butlers.fleet_events import publish_fleet_event
    await publish_fleet_event(pool, "session", session_event_data)  # the real cross-process path
except Exception:
    logger.debug(...)
```

This additive shape is deliberate: bu-01r64.2 deletes the first block (and the upward `from butlers.api.routers.X import emit_Y_event` imports it requires) once the NOTIFY-based path has proven itself in production, without this slice needing to coordinate a simultaneous cutover.

### Bridge Side (`butlers.api.fleet_events_bridge.run_fleet_events_listener`)

A background `asyncio.Task` started from the dashboard-api `lifespan` handler (`butlers/api/app.py`), alongside the process's other startup loops (secrets lifecycle scan, settings-console delta loop, etc.):

1. Opens a **dedicated, non-pooled** `asyncpg.Connection` (`_connect_listener`, using the same `POSTGRES_*` / `DATABASE_URL` env resolution as `Database.from_env`). LISTEN registrations are connection-scoped in Postgres — a connection borrowed from and returned to a recycling pool would silently lose its LISTEN the moment the pool hands that physical connection to an unrelated caller. The bridge holds this connection for its own lifetime instead of going through `DatabaseManager`'s per-butler, schema-scoped pools (which is also why the bridge does not need to pick one specific butler's pool: LISTEN/NOTIFY is database-scoped, so a single dedicated connection to the shared database observes every schema's NOTIFYs).
2. Registers `add_listener(FLEET_EVENTS_CHANNEL, _on_notify)`.
3. `_on_notify` parses the JSON envelope and calls the real `butlers.api.routers.events.emit_event(event_type, data)` — the exact function every in-process, same-container caller already uses. From this point on, a bridged event is handled identically to a native one: ring buffer, subscriber fan-out, WS delivery.
4. **Self-healing.** The task polls `conn.is_closed()` on a short interval and, on any connection loss (server restart, network blip) or connect failure, closes/discards the dead connection and reconnects after a fixed backoff — looping forever until cancelled at shutdown. A bridge that silently stopped after one connection blip would recreate the exact "indicator says connected, nothing arrives" failure mode this RFC exists to fix, so staying down is not an acceptable failure mode; only process shutdown (task cancellation) stops it.
5. Malformed payloads (bad JSON, non-object, missing/non-string `type`) are logged and dropped rather than raised — a malformed NOTIFY must not tear down the listener connection.
6. Startup is wrapped in its own `try/except` in `lifespan()`, independent of `DatabaseManager` initialization: a bridge failure degrades to "daemon-originated live events don't arrive" (the pre-existing bug, unchanged), not to the dashboard-api failing to start.

## Delivery Semantics and Failure Modes

| Failure | Behavior |
|---|---|
| Dashboard-api not yet started / bridge not yet LISTENing | NOTIFY is dropped silently by Postgres (no listener registered); event is lost, no error surfaces to the publisher. |
| Dashboard-api restarts mid-session | Bridge reconnects (backoff loop); events published during the gap are lost; events published after reconnect are delivered normally. |
| Postgres connection drops under the bridge | Detected via `is_closed()` health poll; bridge reconnects; no manual intervention or process restart required. |
| Payload exceeds ~7.8KB | Publish side drops it before sending, logs a warning; no exception reaches the caller's business logic (session completion, notify() delivery, approval gating all proceed unaffected). |
| Malformed/foreign-channel NOTIFY reaches `_on_notify` | Dropped and logged; listener connection stays up. |
| `publish_fleet_event()` itself raises for any reason | It doesn't — every internal step is caught; worst case is a debug-level log and a `False` return. |

None of these failure modes affect the durable record each event describes (the `sessions` row, the `pending_actions` row, the delivered notification) — this transport only carries a **best-effort live freshness signal** layered on top of state that already persists correctly. bu-01r64.3's bus-aware poll intervals are the deliberate backstop for the "live signal was lost" case.

## Integration

- `src/butlers/fleet_events.py` — shared publish-side contract (`FLEET_EVENTS_CHANNEL`, `publish_fleet_event`). Imported by both daemon-side call sites and (for the channel constant only) the bridge, with no import cycle: it depends on nothing else in `butlers.core` or `butlers.api`.
- `src/butlers/api/fleet_events_bridge.py` — bridge implementation, started/stopped from `src/butlers/api/app.py`'s `lifespan()`.
- `src/butlers/core/sessions.py`, `src/butlers/core/spawner.py`, `src/butlers/core_tools/_notifications.py`, `src/butlers/modules/approvals/gate.py`, `src/butlers/modules/approvals/email_guard.py` — daemon-side `publish_fleet_event()` call sites, added alongside the pre-existing (now cross-process-dead) `emit_event()`/`emit_spend_event()`/`emit_approvals_event()` calls.
- No frontend changes: `WS /api/events/stream`, its ring-buffer snapshot, and every existing cache-invalidation consumer are unaware the event's origin process changed.
- No schema/migration changes: `pg_notify` requires no table.

## Alternatives Considered

- **HTTP callback from daemon to dashboard-api.** Rejected — requires an authenticated ingress surface on dashboard-api reachable from every daemon container, for a best-effort UI-freshness signal that doesn't need request/response semantics.
- **A dedicated message broker (Redis pub/sub, NATS, etc.).** Rejected — adds a new service to the deployment topology (RFC 0008) for exactly the pub/sub primitive Postgres already provides between two processes that already share a database connection.
- **Route the event through a durable table + polling.** Rejected as the *primary* mechanism (it already exists, and is the reason the bug was invisible to automated checks: polling still worked, so nothing paged anyone) — but is retained as the correctness backstop; this RFC only fixes the *live* signal, and bu-01r64.3 tightens the poll fallback specifically for when the live signal is absent.
- **Have the daemon call `emit_event()` over an internal RPC to the api process.** Rejected — reinvents a bespoke transport for something Postgres already solves at the connection level the two processes already share; would also need its own reconnect/backoff/auth story that NOTIFY/LISTEN gets for free from the existing DB connection.
