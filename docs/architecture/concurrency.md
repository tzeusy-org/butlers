# Concurrency Architecture

Status: Normative (describes current state + planned changes)
Last updated: 2026-02-18

## 1. Overview

This document describes the concurrency model across the entire Butlers
end-to-end message pipeline: from connector ingestion through switchboard
classification, butler dispatch, runtime execution, and response delivery.

Each stage has different parallelism characteristics.  Understanding where
requests serialize and where they fan out is critical for reasoning about
throughput, latency, and backpressure.

### End-to-End Pipeline Diagram

```
 Connectors (Gmail, Telegram, ...)
 ──────────────────────────────────
   │  Semaphore-bounded parallel fetch
   │  (default 8 concurrent per connector)
   ▼
 Switchboard Ingest Tool
 ──────────────────────────────────
   │  Persist to message_inbox (serial per msg, ~5ms)
   │  Return immediately to connector
   │  Fire background asyncio.create_task()
   ▼
 Background Classification Tasks          ← parallel, unbounded
 ──────────────────────────────────
   │  Each task calls pipeline.process()
   │  which calls spawner.trigger()
   ▼
 Switchboard Spawner Lock                 ← SERIAL BOTTLENECK
 ──────────────────────────────────
   │  asyncio.Lock — one runtime session at a time
   │  Queued tasks wait here
   ▼
 Switchboard Runtime Session
 ──────────────────────────────────
   │  LLM classifies message
   │  Calls route_to_butler() tool(s)
   │  CURRENTLY: blocks until target butler session completes (5–60s)
   │  PLANNED:   returns on accept (~50ms), target processes async
   ▼
 Target Butler route.execute              ← per-butler SERIAL BOTTLENECK
 ──────────────────────────────────
   │  Acquires target butler's spawner lock
   │  Spawns runtime session for the routed message
   ▼
 Target Runtime Session
 ──────────────────────────────────
   │  Butler processes message (5–60s typical)
   │  Calls notify() to deliver response
   ▼
 Response Delivery (notify → messenger)
 ──────────────────────────────────
   │  MCP call to switchboard deliver tool
   │  Messenger sends via channel adapter
   ▼
 User receives response
```

## 2. Stage-by-Stage Concurrency

### 2.1 Connector Ingestion

| Property | Value |
|----------|-------|
| **Concurrency model** | Semaphore-bounded parallel |
| **Default concurrency** | 8 (`CONNECTOR_MAX_INFLIGHT`) |
| **Locking** | `asyncio.Semaphore` per connector process |
| **Latency per message** | 1–5s (API fetch + normalization) |
| **Under load** | Bounded by semaphore; excess messages queue locally |

**Code:** `src/butlers/connectors/gmail.py:229`,
`src/butlers/connectors/telegram_bot.py:218`

Connectors poll or subscribe to external sources, fetch message batches, and
submit each message to the switchboard `ingest` tool via MCP.  The semaphore
prevents overwhelming the source API or the switchboard.

**Bottleneck risk:** Low.  Connector-level parallelism is tunable and
messages are small.  The connector is rarely the bottleneck; the
switchboard spawner downstream is.

### 2.2 Switchboard Ingest Accept

| Property | Value |
|----------|-------|
| **Concurrency model** | Serial DB write, then fire-and-forget |
| **Locking** | DB-level (UPSERT with dedupe key) |
| **Latency** | <10ms per message |
| **Under load** | DB pool handles concurrent writes (max 10 connections) |

**Code:** `src/butlers/daemon.py:1731–1772`

The `ingest` tool persists the message to `message_inbox` with deduplication,
then immediately returns to the caller.  Classification is dispatched as a
background `asyncio.create_task()`, decoupling acceptance latency from
processing latency.

**Key design decision:** Ingest returns before classification.  This means
the connector gets fast acknowledgment (~5ms) regardless of how long
classification takes.  The message is durably stored and will be classified
even if the switchboard crashes and restarts (via inbox replay — not yet
implemented).

### 2.3 Background Classification Dispatch

| Property | Value |
|----------|-------|
| **Concurrency model** | Unbounded parallel (asyncio tasks) |
| **Locking** | None at this stage |
| **Under load** | N messages create N concurrent background tasks |

**Code:** `src/butlers/daemon.py:1755–1768`

Each accepted message spawns an independent background task that calls
`pipeline.process()`.  There is no backpressure mechanism: if 100 messages
arrive in 1 second, 100 tasks are created.

**Bottleneck risk:** These tasks all converge on the spawner lock (next
stage).  The unbounded fan-out here means the spawner queue can grow without
limit.

**Planned change:** Replace with a durable hybrid buffer — bounded
in-memory queue for the hot path, periodic DB scanner for crash recovery.
See Section 4.1 for the full buffer design.

### 2.4 Switchboard Spawner (Critical Serialization Point)

| Property | Value |
|----------|-------|
| **Concurrency model** | **Serial** — one runtime session at a time |
| **Locking** | `asyncio.Lock()` per Spawner instance |
| **Latency** | 20–75s per session (classification + synchronous route wait) |
| **Under load** | Messages queue behind the lock; Nth message waits ~N×session_time |

**Code:** `src/butlers/core/spawner.py:235,324`

This is the primary throughput bottleneck.  The spawner enforces serial
dispatch: only one LLM runtime session runs at a time per butler.  When
multiple classification tasks arrive concurrently, they queue behind the
`asyncio.Lock` and process one at a time.

**Why serial?** The spawner was designed with safety as the priority:

1. **State isolation** — Runtime sessions read/write butler state (KV store,
   DB).  Concurrent sessions could produce race conditions.
2. **Cost control** — LLM sessions are expensive.  Serial dispatch provides
   natural rate limiting.
3. **Session logging** — The session model assumes non-overlapping sessions
   per butler for clean audit trails.

**Self-trigger deadlock guard:** When `trigger_source == "trigger"` and the
lock is already held, the spawner rejects immediately instead of queueing.
This prevents a running session from calling the trigger tool on its own
butler, which would deadlock.

```python
# spawner.py:307-317
if trigger_source == "trigger" and self._lock.locked():
    return SpawnerResult(success=False, error="...")
```

**Synchronous route coupling:** The switchboard session currently blocks on
`route_to_butler()` until the target butler finishes its entire session.
The switchboard spawner lock is held not just for classification (~5–15s)
but for classification + target butler processing (~20–75s total).  See
Section 2.5 for details and Section 4.4 for the planned fix.

**Latency under load (10 concurrent messages, current behavior):**

| Message | Wait time | Total latency |
|---------|-----------|---------------|
| 1 | 0s | ~45s |
| 2 | ~45s | ~90s |
| 5 | ~180s | ~225s |
| 10 | ~405s | ~450s |

This is unacceptable for real-time messaging channels.

**Planned change:** Introduce a concurrency pool to allow N concurrent
sessions per butler, with N configurable per butler (see Section 4.2).

### 2.5 Switchboard → Target Butler Routing (Synchronous Coupling)

| Property | Value |
|----------|-------|
| **Concurrency model** | Sequential within session; parallel across sessions |
| **Locking** | Per-endpoint MCP client lock |
| **Latency** | **5–60s per route call** (blocks on full target butler session) |

**Code:** `src/butlers/daemon.py:1775–1851`,
`roster/switchboard/tools/routing/route.py:166–354`

During a switchboard runtime session, the LLM calls `route_to_butler()` for
each target.  These calls are sequential (the LLM makes them one at a time
within a single turn).

**Critical: the call chain is fully synchronous.**  `route_to_butler()`
calls `route()` which makes an MCP call to the target butler's
`route.execute` tool, which calls `spawner.trigger()` on the target butler,
which runs an entire LLM session (5–60s).  The switchboard runtime session
blocks until the target butler's session completes:

```
Switchboard runtime session (holds switchboard spawner lock the entire time)
  │
  ├─ LLM classifies message                               ~5–15s
  │
  ├─ route_to_butler("health", prompt) ──────────────────────────
  │     │                                                       │
  │     ├─ route() → MCP call to health butler                  │
  │     │     │                                                 │
  │     │     ├─ health route.execute                           │
  │     │     │     ├─ health spawner.trigger()                 │
  │     │     │     │     └─ full LLM session ──── 5–60s        │
  │     │     │     └─ return result                            │
  │     │     └─ return result                                  │
  │     └─ return {"status": "ok"}                              │
  │                                                             │
  ├─ LLM outputs summary text                              ~1s │
  │                                                             │
  └─ session ends, switchboard spawner lock released        total: 20–75s
```

The switchboard does not use the target butler's output — it only inspects
`{"status": "ok"}` vs `{"status": "error"}` for telemetry.  The target
butler delivers its response directly via `notify()` → messenger, bypassing
the switchboard entirely on the return path.  This means the synchronous
wait is architecturally unnecessary.

**Planned change:** Make `route.execute` accept-then-process asynchronously,
so `route_to_butler()` returns as soon as the target butler acknowledges
receipt (see Section 4.4).

**MCP client caching:** The switchboard maintains a cached MCP client per
target butler endpoint.  A per-endpoint lock prevents concurrent connection
setup, but once established, the client is reused across calls.

### 2.6 Target Butler Spawner (Second Serialization Point)

| Property | Value |
|----------|-------|
| **Concurrency model** | **Serial** — same as switchboard spawner |
| **Locking** | `asyncio.Lock()` per butler |
| **Latency** | 5–60s per session |
| **Under load** | Messages to the same butler queue behind its lock |

**Code:** `src/butlers/daemon.py:1140–1358`

When `route.execute` arrives at a target butler, it calls
`spawner.trigger(trigger_source="trigger")`.  This acquires the target
butler's spawner lock — independent from the switchboard's lock.

**Cross-butler parallelism:** Messages routed to different butlers process
in parallel (different spawner instances, different locks).  Messages to the
same butler serialize.

```
Message A → health butler spawner ─────── processing A ─────── done
Message B → relationship butler spawner ── processing B ── done
Message C → health butler spawner ────────────────────── wait ── processing C ── done
```

### 2.7 Response Delivery

| Property | Value |
|----------|-------|
| **Concurrency model** | Sequential within session; parallel across sessions |
| **Locking** | None (fire-and-forget MCP call) |
| **Latency** | 500–2000ms per delivery |

**Code:** `src/butlers/daemon.py:2035–2160`

Runtime sessions call `notify()` to send responses.  The tool makes an MCP
call to the switchboard's `deliver` tool, which forwards to the messenger
butler for channel-specific delivery (Telegram API, email send, etc.).

Responses from different concurrent sessions are independently delivered.
Within a single session, `notify()` calls are sequential.

### 2.8 Scheduler/Tick

| Property | Value |
|----------|-------|
| **Concurrency model** | Serial per butler; parallel across butlers |
| **Locking** | Goes through the same spawner lock |
| **Under load** | Scheduled tasks compete with incoming messages for the spawner lock |

**Code:** `src/butlers/core/scheduler.py:115–183`

The heartbeat butler calls `tick()` on each registered butler every 10
minutes.  Due tasks dispatch through the same `spawner.trigger()` path.
A scheduled task can block incoming messages (and vice versa) if they target
the same butler.

### 2.9 Database Connection Pools

| Property | Value |
|----------|-------|
| **Pool size** | min=2, max=10 per butler |
| **Isolation** | Each butler owns a dedicated PostgreSQL database |
| **Under load** | >10 concurrent queries queue for a connection |

**Code:** `src/butlers/db.py:83–84,156–157`

Each butler has its own asyncpg connection pool.  The switchboard also
maintains a small audit pool (min=1, max=2) shared for cross-butler audit
logging.  Pool exhaustion is unlikely given the spawner serialization — the
spawner lock is hit long before the connection pool.

## 3. Bottleneck Summary

```
Stage                      Concurrency   Bottleneck Risk   Latency Impact
─────────────────────────  ────────────  ────────────────  ──────────────
Connector fetch            8 parallel    Low               Negligible
Ingest accept              Serial/fast   Low               <10ms
Background dispatch        Unbounded     Medium (memory)   None (async)
Switchboard spawner        SERIAL        HIGH              20–75s × queue depth ← includes route wait
Route to target butler     Synchronous   HIGH              5–60s (blocks switchboard session)
Target butler spawner      SERIAL        HIGH              5–60s × queue depth
Response delivery          Per-session   Low               500–2000ms
Scheduler tick             Via spawner   Medium            Competes with messages
DB pool                    10 conn       Low               <5ms per query
```

The switchboard spawner lock time is inflated by the synchronous coupling
with target butler sessions.  The switchboard holds its lock for
classification (~5–15s) **plus** target butler processing (~5–60s), making
it the dominant bottleneck.  Decoupling these (Section 4.4) reduces
switchboard lock time to classification-only.

## 4. Planned Changes

Four changes address the serialization bottleneck, applied together:

### 4.1 Durable Buffer: DB-Backed Queue with In-Memory Hot Path

**Problem:** Background classification tasks are currently unbounded
`asyncio.create_task()` calls.  Under burst load the spawner queue grows
without limit.  If the process crashes, in-flight tasks are lost (though
their messages are already in `message_inbox`).  There is no replay
mechanism and no backpressure.

**Design requirements:**

- **Fast persistence:** Messages must be durably stored before the ingest
  tool returns (<10ms accept latency).
- **At-least-once processing:** Every accepted message must eventually be
  classified and routed, even after crashes or restarts.
- **Low average latency:** In steady state (one message every few seconds),
  processing should start immediately — no polling delay.
- **High max-latency tolerance:** During bursts (e.g., 50 emails at once),
  it is acceptable for the last message to wait minutes, as long as it is
  eventually processed.

**Solution: hybrid in-memory + DB-backed buffer.**

The `message_inbox` table (already written by `ingest_v1()`) is the durable
backing store.  An in-memory `asyncio.Queue` provides the hot path for
immediate dispatch.  A periodic DB scanner recovers messages that were
accepted but never processed (crash recovery, stuck tasks).

```
                    ┌──────────────────────────┐
                    │  Connector submits        │
                    │  ingest.v1 envelope       │
                    └────────────┬─────────────┘
                                 │
                                 ▼
               ┌─────────────────────────────────────┐
               │  ingest_v1()                        │
               │  1. Validate envelope               │
               │  2. Persist to message_inbox        │
               │     (lifecycle_state = 'accepted')  │
               │  3. Return request_id to connector  │
               └────────────┬────────────────────────┘
                            │
                  ┌─────────┴─────────┐
                  │ Hot path (normal) │
                  ▼                   │
         ┌──────────────┐             │
         │  In-memory    │             │
         │  asyncio.Queue│             │
         │  (bounded)    │             │
         └──────┬───────┘             │
                │                     │
       ┌────────┴────────┐            │
       ▼        ▼        ▼            │
    Worker   Worker   Worker          │
      1        2       ...N           │
       │        │        │            │
       ▼        ▼        ▼            │
    pipeline.process()                │
    (acquires spawner semaphore)      │
       │                              │
       ▼                              │
    Update message_inbox              │
    lifecycle_state →                 │
    'parsed' or 'errored'            │
                                      │
                  ┌───────────────────┘
                  │ Cold path (recovery)
                  ▼
         ┌──────────────────┐
         │  Periodic scanner │
         │  (every 30s)      │
         │  SELECT ... WHERE │
         │  lifecycle_state  │
         │  = 'accepted'     │
         │  AND received_at  │
         │  < now() - 10s    │
         └──────┬───────────┘
                │
                ▼
         Re-enqueue to
         in-memory queue
```

#### Hot path (steady state)

1. `ingest_v1()` persists to `message_inbox` with
   `lifecycle_state = 'accepted'`.  This already happens today.

2. Instead of `asyncio.create_task()`, `ingest` pushes a lightweight
   reference (request_id + message_inbox_id + normalized_text + routing
   args) onto a bounded `asyncio.Queue`.

3. A fixed pool of N worker coroutines drain the queue.  Each worker calls
   `pipeline.process()`, which goes through the spawner.  N matches
   `max_concurrent_sessions` (Section 4.2) — no point draining faster than
   the spawner can process.

4. On successful processing, the worker updates `message_inbox` to
   `lifecycle_state = 'parsed'`.  On failure, `'errored'`.

**Average latency in steady state:** <1ms queue wait (queue is empty when
a message arrives, worker picks it up immediately) + spawner processing
time.  No polling delay.

#### Cold path (crash recovery and burst drain)

A background scanner task runs every 30 seconds:

```sql
SELECT id, received_at, request_context, raw_payload, normalized_text
FROM message_inbox
WHERE lifecycle_state = 'accepted'
  AND received_at < now() - interval '10 seconds'
ORDER BY received_at ASC
LIMIT 50
```

The 10-second grace period prevents the scanner from racing with the hot
path (a message just accepted and already in the in-memory queue).  The
`LIMIT 50` prevents the scanner from flooding the queue on startup after
a long outage.

Recovered messages are pushed onto the same in-memory queue.  The workers
process them identically to hot-path messages.  Deduplication is handled
by checking `lifecycle_state` before processing — if a message has already
transitioned past `'accepted'`, it is skipped.

#### Backpressure

When the in-memory queue is full (configurable capacity, default 100):

- The message is **not lost** — it is already in `message_inbox` with
  `lifecycle_state = 'accepted'`.
- The `ingest` tool still returns `{"status": "accepted"}` to the
  connector (the message is durably stored).
- The periodic scanner will pick up the message on its next sweep.
- A `butlers.buffer.backpressure_total` counter is incremented for
  alerting.

This means backpressure degrades latency (message waits for scanner
sweep, up to 30s) but never drops messages.

#### Lifecycle state transitions

```
accepted ──hot path──→ parsed      (normal completion)
accepted ──hot path──→ errored     (processing failure)
accepted ──scanner───→ (re-enqueue to hot path)
accepted ──operator──→ cancelled   (manual cancellation)
```

#### Why not a pure DB-polling queue?

Polling adds latency floor equal to the poll interval.  Even at 1-second
polls, steady-state latency would increase by 500ms on average.  The
hybrid approach gives zero additional latency in steady state (in-memory
queue push is instant) while using the DB as the durable fallback.

#### Why not LISTEN/NOTIFY?

PostgreSQL `LISTEN/NOTIFY` could replace the periodic scanner for faster
recovery.  However:

- It adds connection-management complexity (dedicated listener connection).
- Notification payloads are limited to 8KB.
- It doesn't help the steady-state hot path (which is already instant).
- The periodic scanner is simpler and sufficient given the high
  max-latency tolerance for burst recovery.

If recovery latency becomes a concern, LISTEN/NOTIFY can be added later
as an optimization to the cold path without changing the hot path.

#### Configuration

```toml
# butler.toml
[buffer]
queue_capacity = 100         # in-memory queue size
worker_count = 3             # should match max_concurrent_sessions
scanner_interval_s = 30      # cold path sweep interval
scanner_grace_s = 10         # age threshold for scanner eligibility
scanner_batch_size = 50      # max messages per scanner sweep
```

#### Metrics

- `butlers.buffer.queue_depth` (gauge) — current in-memory queue depth
- `butlers.buffer.enqueue_total` (counter, label: path=hot|cold) — messages
  enqueued via hot path vs scanner recovery
- `butlers.buffer.backpressure_total` (counter) — queue-full events (message
  deferred to scanner)
- `butlers.buffer.scanner_recovered_total` (counter) — messages recovered
  by periodic scanner
- `butlers.buffer.process_latency_ms` (histogram) — time from
  `message_inbox.received_at` to processing start (measures total queue
  wait, including burst delays)

### 4.2 Per-Butler Concurrency Pool

**Problem:** The spawner `asyncio.Lock` serializes all sessions.  A butler
that could safely handle 3 concurrent sessions is artificially limited to 1.

**Solution:** Replace `asyncio.Lock` with `asyncio.Semaphore(n)` where `n`
is configurable per butler.

```
Before:
  self._lock = asyncio.Lock()             # n=1, always

After:
  self._session_semaphore = asyncio.Semaphore(
      config.runtime.max_concurrent_sessions  # default 1, tunable per butler
  )
```

**Design:**

- `max_concurrent_sessions` is configured in `butler.toml` under `[runtime]`.
  Default: 1 (preserves current behavior for butlers that haven't been
  audited for concurrency safety).
- The switchboard butler is the first candidate for `max_concurrent_sessions > 1`
  because its classification sessions are read-only (they don't mutate butler
  state; they only call `route_to_butler` which writes to the target butler's
  domain).
- For stateful butlers (health, relationship), concurrency requires auditing
  state access patterns.  The safe starting point is:
  - **Read-only sessions** (queries, summaries): safe to parallelize.
  - **Write sessions** (state mutations): serialize or use optimistic
    concurrency control at the DB level.
- The self-trigger deadlock guard (`trigger_source == "trigger"` rejection)
  is replaced by a check against the semaphore count — if a session is
  in-flight and triggers itself, reject; but concurrent sessions from
  different sources are allowed.

**Session isolation requirements for concurrent sessions:**

1. **Session logging:** Each session gets a unique `session_id`.  The
   `sessions` table already supports overlapping sessions.
2. **State store:** KV operations use PostgreSQL transactions.  Concurrent
   sessions writing to the same key need conflict resolution (last-write-wins
   is acceptable for most state; critical state should use conditional updates).
3. **Memory module:** `memory_context` retrieval is read-only and safe to
   parallelize.  `store_session_episode` writes are append-only and safe.
4. **MCP tools:** Tools that mutate external state (send email, create event)
   are idempotent by design.  Concurrent calls are safe.

**Rollout:**

| Phase | Butler | `max_concurrent_sessions` | Rationale |
|-------|--------|---------------------------|-----------|
| 1 | switchboard | 3 | Classification is read-only |
| 2 | messenger | 2 | Delivery is stateless per-message |
| 3 | specialist butlers | 1 (audit first) | Requires state access audit |

**Metrics:**

- `butlers.spawner.active_sessions` (gauge) — current concurrent sessions
- `butlers.spawner.queued_triggers` (gauge) — tasks waiting for semaphore
- `butlers.spawner.session_duration_ms` (histogram) — per-session duration

### 4.3 Separate Classification from Execution

**Problem:** The switchboard spawner serializes classification (which is
read-only) behind the same lock as everything else the switchboard does.
Classification could safely run in parallel even when other switchboard
operations serialize.

**Solution:** This is achieved by applying Section 4.2 to the switchboard
with `max_concurrent_sessions >= 3`.  No separate mechanism is needed —
once the switchboard spawner allows concurrent sessions, classification
sessions naturally run in parallel.

The switchboard is uniquely suited for higher concurrency because:

1. Its runtime sessions are **read-only** — they read the butler registry
   and call `route_to_butler`.
2. The routing context (`_routing_session_ctx`) is currently shared state
   that assumes serial access.  This must be changed to per-session context
   (passed via session-scoped closure or task-local variable).
3. `route_to_butler` tool calls write to target butler domains, not the
   switchboard's own state.

**Required refactoring:**

- Make `_routing_session_ctx` per-session instead of per-daemon.  Pass it
  through the spawner as a session-scoped dict.
- Audit the `route_to_butler` tool registration to ensure it captures
  session-scoped context from the spawner, not from shared daemon state.

### 4.4 Async Route Dispatch (Accept-then-Process)

**Problem:** The switchboard runtime session blocks on `route_to_butler()`
until the target butler finishes its entire LLM session.  The switchboard
spawner lock is held for classification + target processing (~20–75s),
even though the switchboard doesn't use the target's output.

This doesn't change user-perceived latency (which is dominated by the
specialist butler's processing time), but it bloats the switchboard's
flame graph span and — critically — holds the switchboard spawner lock
far longer than necessary, blocking classification of subsequent messages.

**Solution:** Split `route.execute` on the target butler into two phases:
accept (synchronous) and process (asynchronous).

```
Before (synchronous):
  route_to_butler()
    → MCP: route.execute on health butler
      → spawner.trigger() ─── full LLM session (5–60s) ───
      → return result
    → return {"status": "ok"}                          total: 5–60s

After (accept-then-process):
  route_to_butler()
    → MCP: route.execute on health butler
      → validate envelope, persist to work queue
      → return {"status": "accepted"}                  total: <50ms
    (background) → spawner.trigger() ─── full LLM session ───
    → return {"status": "accepted"}
```

**Design:**

The target butler's `route.execute` tool changes behavior:

1. **Accept phase (synchronous, <50ms):** Validate the route envelope,
   persist the request to a durable work queue (the `message_inbox` table
   already serves this purpose — the request is stored with
   `lifecycle_state = 'accepted'`), and return `{"status": "accepted",
   "request_id": "..."}` immediately.

2. **Process phase (asynchronous):** A background task picks up the accepted
   request and calls `spawner.trigger()`.  This runs under the target
   butler's spawner lock as before, but the switchboard is no longer waiting.

3. **The switchboard sees a fast acknowledgment.** `route_to_butler()`
   returns `{"status": "accepted"}` instead of the full result.  The
   `_extract_routed_butlers()` telemetry in `pipeline.py` treats `"accepted"`
   the same as `"ok"` for the acked/failed classification.

**What the switchboard loses:** Synchronous confirmation that the target
butler successfully processed the message.  It only knows the target
accepted the request.  Processing failures surface through:

- Target butler session logs and telemetry
- `message_inbox.lifecycle_state` transitioning to `"errored"` instead of
  `"parsed"`
- Alerting on failed sessions (existing observability)

This is acceptable because the switchboard already doesn't use the target
butler's output content — it only checks success/failure for telemetry, and
the response flows back to the user via `notify()` → messenger, not via the
switchboard.

**Flame graph impact:**

```
Before:
  switchboard.message ─────────────────────────────────────────── 45s
    switchboard.routing.llm_decision ─────────────────────────── 44s
      butler.llm_session (switchboard) ───────────────────────── 44s
        route_to_butler ──────────────────────────────────────── 30s
          switchboard.route.dispatch ─────────────────────────── 30s
            health.route.execute ─────────────────────────────── 30s
              butler.llm_session (health) ────────────────────── 29s

After:
  switchboard.message ────────── 15s
    switchboard.routing.llm_decision ────── 14s
      butler.llm_session (switchboard) ──── 14s
        route_to_butler ── 50ms
          switchboard.route.dispatch ── 45ms

  (separate trace, linked by request_id)
  health.route.process ──────────────────────────── 30s
    butler.llm_session (health) ──────────────────── 29s
```

The switchboard span drops from ~45s to ~15s.  The health butler span is
unchanged but appears as a sibling trace linked by `request_id`, not nested
under the switchboard.

**Trace linkage:** The `request_id` (UUIDv7) is already propagated through
the route envelope and stored in both the switchboard's routing log and the
target butler's session record.  After decoupling, traces are correlated by
`request_id` rather than by parent-child span relationship.

**Metrics:**

- `butlers.route.accept_latency_ms` (histogram) — time for target butler
  to acknowledge receipt
- `butlers.route.queue_depth` (gauge) — accepted-but-unprocessed requests
  per butler
- `butlers.route.process_latency_ms` (histogram) — time from acceptance to
  processing start (queue wait time)

## 5. Concurrency Principles

These principles govern concurrency decisions across the entire codebase:

### P1: Accept Fast, Process Async

Message acceptance (ingest) must complete in <50ms.  All classification and
processing happens asynchronously after the message is durably stored.  The
connector should never block waiting for a butler to finish processing.

### P2: Serialize by Default, Parallelize by Audit

New butlers default to `max_concurrent_sessions = 1`.  Increasing concurrency
requires an explicit audit of the butler's state access patterns and tool
side effects.  The audit checklist:

- [ ] All KV state writes use conditional updates or are idempotent
- [ ] No shared mutable state outside PostgreSQL transactions
- [ ] External tool calls (email, calendar, etc.) are idempotent
- [ ] Session logging handles overlapping sessions
- [ ] Memory module operations are safe for concurrent access

### P3: Backpressure Over Unbounded Queuing

Every queue in the system must have a capacity limit.  When the limit is
reached, the upstream stage receives a backpressure signal (rejection, retry
hint, or throttle).  Unbounded queues hide latency problems and risk OOM
under burst load.

### P4: Cross-Butler Parallelism is Free

Messages to different butlers never contend with each other.  Each butler
has its own spawner, its own database, and its own connection pool.  The
architecture deliberately isolates butlers so that adding more specialist
butlers increases natural parallelism without additional coordination.

### P5: The Switchboard is the Multiplexer

The switchboard's job is to classify and fan out — it should be the fastest
stage in the pipeline.  Its concurrency limit should be the highest of any
butler, and its sessions should be as short as possible (classification
only, no heavy processing).

### P6: Spawner Lock Scope Matches Session Scope

The spawner serialization unit is one complete LLM session (prompt → tool
calls → response).  There is no finer-grained locking within a session.  If
a butler needs sub-session concurrency (e.g., parallel tool calls), that
happens within the runtime's own execution model, not at the spawner level.

## 6. Related Documents

- `docs/connectors/horizontal_scaling.md` — Connector-level scaling patterns
- `docs/connectors/interface.md` — Connector contract and ingest.v1 format
- `docs/operations/switchboard_operator_runbook.md` — Switchboard operations
- `src/butlers/core/spawner.py` — Spawner implementation
- `src/butlers/modules/pipeline.py` — MessagePipeline implementation
