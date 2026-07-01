# Thread Affinity Routing

> **Purpose:** Defines how email thread affinity routes follow-up messages to the same butler without LLM classification, reducing cost and improving routing consistency.
> **Audience:** Developers working on Switchboard routing, operators tuning thread affinity behavior, architects evaluating routing efficiency.
> **Prerequisites:** [Routing Architecture](routing.md), [Pre-Classification Triage](pre-classification-triage.md).

## Overview

Email threads are typically topically coherent. Once the Switchboard routes the first message in a Gmail thread to a butler, follow-up replies in that same thread should route to the same butler without invoking LLM classification. Thread affinity is a deterministic pre-LLM routing stage that checks routing history for known threads and short-circuits classification when a clear routing precedent exists.

## Pipeline Position

Thread affinity is evaluated as part of the pre-classification triage pipeline, before triage rules and before LLM classification:

1. Sender/header triage rules (from the pre-classification triage spec)
2. Thread-affinity global/thread override checks
3. Thread-affinity lookup in routing history
4. LLM classification fallback (only when affinity does not produce a route)

Thread affinity only applies when:
- `source_channel = "email"`
- `event.external_thread_id` is present in the `ingest.v1` envelope

Non-email channels and messages without thread identifiers skip affinity entirely.

## Data Model

Thread affinity extends the existing `routing_log` table with two columns:

- **`thread_id`** (TEXT, nullable) --- populated from `ingest.v1.event.external_thread_id` for email ingress; `NULL` for non-email channels.
- **`source_channel`** (TEXT, nullable) --- the source channel identifier (`"email"`, `"telegram"`, etc.) for all new writes.

An index optimizes the affinity lookup:

```sql
CREATE INDEX idx_routing_log_thread_affinity
ON routing_log (thread_id, created_at DESC)
WHERE thread_id IS NOT NULL AND source_channel = 'email';
```

## Lookup Algorithm

Given `thread_id = :tid` and `source_channel = "email"`:

1. **Global disable check** --- if thread affinity is globally disabled, skip to LLM fallback.
2. **Thread-specific override** --- if an override exists for this thread:
   - `disabled` --- skip affinity, continue to LLM fallback.
   - `force:<butler>` --- route directly to that butler without further checks.
3. **Missing thread ID** --- if `:tid` is missing or empty, skip to LLM fallback.
4. **History query** --- query recent routing history within the TTL window:

```sql
SELECT target_butler, MAX(created_at) AS last_routed_at
FROM routing_log
WHERE source_channel = 'email'
  AND thread_id = :tid
  AND created_at >= NOW() - (:ttl_days || ' days')::INTERVAL
GROUP BY target_butler
ORDER BY last_routed_at DESC
LIMIT 2;
```

5. **Decision:**
   - **0 rows** --- miss, fall through to LLM classification.
   - **1 row** --- hit, route to that butler (skip LLM).
   - **2+ distinct butlers** --- conflict, fall through to LLM classification.

The conflict path prevents pinning a thread that has already been routed to multiple butlers, which indicates the thread's topic has evolved beyond a single domain.

## TTL and Staleness

Thread affinity is bounded by a configurable max-age window:

- **Default:** `thread_affinity_ttl_days = 30`
- Only routing history rows newer than the TTL are eligible.
- If the latest historical route for a thread is older than the TTL, it is treated as stale and the message falls through to LLM classification.

## Override Levels

Two override levels control affinity behavior:

### Global Override

- `thread_affinity_enabled` (boolean, default `true`) --- master switch for the entire feature.

### Thread-Specific Overrides

- `force:<butler>` --- force a specific thread to always route to a named butler.
- `disabled` --- disable affinity for a specific thread, forcing LLM classification.

Thread-specific overrides take precedence over history lookup. They are managed through the dashboard.

## Dashboard Controls

The dashboard email filters/settings page exposes:

- Global enable/disable toggle for thread affinity
- TTL days numeric setting (default 30)
- Per-thread override management (force or disable affinity for individual threads)

## Observability

Three counters in the `butlers.switchboard.*` namespace:

- **`butlers.switchboard.thread_affinity.hit`** --- incremented when affinity produces a route. Attributes: `destination_butler`.
- **`butlers.switchboard.thread_affinity.miss`** --- incremented when affinity does not route. Attributes: `reason` (one of `no_thread_id`, `no_history`, `conflict`, `disabled`, `error`).
- **`butlers.switchboard.thread_affinity.stale`** --- incremented when a historical match exists but falls outside the TTL.

Low-cardinality attribute discipline: tags are bounded to `source=email`, `destination_butler`, `reason`, `policy_tier`, and `schema_version`. Raw `thread_id` values are never used as metric attributes.

## Edge Cases

- **Multi-butler thread history** --- treated as conflict, falls through to LLM.
- **Missing `external_thread_id`** --- affinity not attempted.
- **Non-email channels** --- affinity not attempted.
- **Lookup/storage errors** --- increment `miss` with `reason=error` and continue to LLM fallback. Thread affinity never causes a hard failure.

## Migration and Rollout

Rollout sequence:
1. Deploy Alembic migration adding `routing_log.thread_id`, `routing_log.source_channel`, and the affinity index.
2. Deploy routing-log write path to persist `thread_id` for email.
3. Deploy triage lookup logic with feature flag default on.
4. Expose dashboard controls for enable/disable, TTL, and thread overrides.

No backfill required --- affinity starts from newly logged routed email threads.

## Verification

To confirm thread affinity routing is functioning as described:

```bash
# 1. routing_log table has thread_id and source_channel columns
psql -h localhost -U butlers -d butlers -c \
  "SELECT column_name, data_type
   FROM information_schema.columns
   WHERE table_schema='switchboard' AND table_name='routing_log'
   AND column_name IN ('thread_id','source_channel');"
# Expected: both columns present with type text

# 2. Affinity index exists
psql -h localhost -U butlers -d butlers -c \
  "SELECT indexname, indexdef FROM pg_indexes
   WHERE schemaname='switchboard' AND tablename='routing_log'
   AND indexname='idx_routing_log_thread_affinity';"
# Expected: one row with the partial index definition on (thread_id, created_at DESC)
#           WHERE thread_id IS NOT NULL AND source_channel = 'email'

# 3. Hit counter increments after a known thread receives a follow-up email
curl -s "http://localhost:9090/api/v1/query?query=butlers_switchboard_thread_affinity_hit_total" \
  | python3 -m json.tool | grep -E "destination_butler|value"
# Expected: counter entries with the target butler name; count increases after replies
#           in already-routed threads arrive

# 4. Miss counter reflects expected miss reasons
curl -s "http://localhost:9090/api/v1/query?query=butlers_switchboard_thread_affinity_miss_total" \
  | python3 -m json.tool | grep "reason"
# Expected: no_thread_id (non-email or missing ID), no_history (first message in thread),
#           or conflict (multi-butler history)

# 5. Thread-specific overrides persist and take effect
# Set a force-override for a thread via the dashboard, then send another message in that thread
psql -h localhost -U butlers -d butlers -c \
  "SELECT thread_id, override_type, target_butler FROM switchboard.thread_affinity_overrides;"
# Expected: override row exists; subsequent routing for that thread skips history lookup
```

## Related Pages

- [Routing Architecture](routing.md) --- how thread affinity fits into the overall routing pipeline
- [Pre-Classification Triage](pre-classification-triage.md) --- the rule-based triage layer that runs alongside thread affinity
- [Email Priority Queuing](email-priority-queuing.md) --- tier-based queue ordering
- [Observability](observability.md) --- metrics infrastructure
