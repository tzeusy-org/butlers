# Switchboard Thread Affinity Routing (Email)

Status: Draft (Implementation Spec)  
Last updated: 2026-02-22  
Primary owner: Switchboard/Core

## 1. Motivation

Email threads are typically topically coherent. Once Switchboard routes the first
message in a Gmail thread to a butler, follow-up replies in that same thread
should route to the same butler without invoking LLM classification.

Goals:
- Reduce classification cost and latency for thread replies.
- Increase routing consistency within ongoing conversations.
- Keep deterministic triage behavior before LLM fallback.

## 2. Scope and Pipeline Position

This spec adds a thread-affinity check to Switchboard email triage.

Order in the triage pipeline:
1. Sender/header triage rules (from `butlers-0bz3.3`)
2. Thread-affinity global/thread override checks
3. Thread-affinity lookup in routing history
4. LLM classification fallback (only when affinity does not produce a route)

Thread affinity only applies when:
- `source_channel = "email"`
- `event.external_thread_id` is present in `ingest.v1`

## 3. Data Model Extension

### 3.1 `routing_log` schema change

Add thread affinity fields needed by the lookup:

```sql
ALTER TABLE routing_log
ADD COLUMN thread_id TEXT,
ADD COLUMN source_channel TEXT;

CREATE INDEX idx_routing_log_thread_affinity
ON routing_log (thread_id, created_at DESC)
WHERE thread_id IS NOT NULL AND source_channel = 'email';
```

Population rule:
- Set `routing_log.source_channel` from ingress metadata (`email`, `telegram`,
  etc.) for all new writes.
- For email ingress, set `routing_log.thread_id` from
  `ingest.v1.event.external_thread_id`.
- For non-email channels, keep `thread_id = NULL`.

## 4. Lookup Algorithm

Given `thread_id = :tid` and `source_channel = "email"`:

1. If affinity is globally disabled, skip and continue to LLM fallback.
2. If a thread-specific override exists:
   - `disabled` override: skip affinity and continue to LLM fallback.
   - `force:<butler>` override: route directly to that butler.
3. If `:tid` is missing/empty, skip affinity and continue to LLM fallback.
4. Query recent routing history within TTL window:

```sql
SELECT
  target_butler,
  MAX(created_at) AS last_routed_at
FROM routing_log
WHERE source_channel = 'email'
  AND thread_id = :tid
  AND created_at >= NOW() - (:ttl_days || ' days')::INTERVAL
GROUP BY target_butler
ORDER BY last_routed_at DESC
LIMIT 2;
```

5. Decision:
   - `0` rows: miss -> LLM fallback
   - `1` row: hit -> route to that butler (skip LLM)
   - `2` rows (or more distinct butlers): conflict -> LLM fallback

Notes:
- The conflict path is required to avoid pinning a thread that has already
  decomposed across multiple butlers.

## 5. TTL and Staleness

Thread affinity is bounded by a configurable max-age window.

Default:
- `thread_affinity_ttl_days = 30`

Behavior:
- Only rows newer than TTL are eligible.
- If latest historical route for a thread is older than TTL, treat as stale and
  fall through to LLM classification.

Configuration surface:
- Dashboard email filters/settings page must expose:
  - Global enable/disable toggle for thread affinity
  - TTL days numeric setting (default `30`)

## 6. Overrides

Two override levels are required:

1. Global:
- `thread_affinity_enabled` (bool, default `true`)

2. Thread-specific:
- Force a thread to a specific butler (`force:<butler>`)
- Disable affinity for a thread (`disabled`)

Thread-specific overrides take precedence over history lookup.

## 7. Observability and Metrics

Add counters in the `butlers.switchboard.*` namespace:

- `butlers.switchboard.thread_affinity.hit`
  - Increment when a route is chosen from thread affinity.
- `butlers.switchboard.thread_affinity.miss`
  - Increment when affinity does not route and pipeline falls through.
  - `reason` values: `no_thread_id`, `no_history`, `conflict`, `disabled`,
    `error`.
- `butlers.switchboard.thread_affinity.stale`
  - Increment when historical match exists but is outside TTL.

Low-cardinality attribute guidance:
- Keep tags bounded (`source=email`, `destination_butler`, `reason`,
  `policy_tier`, `schema_version`).
- Never tag with raw `thread_id`.

## 8. Migration and Rollout

Migration requirement:
- Add a new Alembic revision in `roster/switchboard/migrations/` that adds
  `routing_log.thread_id`, `routing_log.source_channel`, and
  `idx_routing_log_thread_affinity`.

Rollout sequence:
1. Deploy migration.
2. Deploy routing-log write path to persist `thread_id` for email.
3. Deploy triage lookup logic with feature flag default on.
4. Expose dashboard controls for enable/disable, TTL, and thread overrides.

Backfill:
- Not required; affinity starts from newly logged routed email threads.

## 9. Edge Cases

- Multi-butler thread history: treat as conflict, fall through to LLM.
- Missing `external_thread_id`: affinity not attempted.
- Non-email channels: affinity not attempted.
- Lookup/storage errors: increment `miss` with `reason=error` and continue to
  LLM fallback (no hard failure).

## 10. Acceptance Mapping

This spec explicitly defines:
1. `routing_log` extension with nullable `thread_id`, `source_channel`, and
   the affinity lookup index.
2. Lookup algorithm with conflict and stale handling.
3. TTL configuration and staleness behavior (default 30 days).
4. Triage pipeline placement (after sender/header rules, before LLM fallback).
5. OpenTelemetry metric additions (`hit`, `miss`, `stale`).
6. Dashboard controls for global toggle, TTL, and thread-specific overrides.
