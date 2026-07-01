# Switchboard Butler

> **Purpose:** Single ingress and routing control plane that receives all incoming messages and dispatches them to the correct specialist butler.
> **Audience:** Contributors and operators.
> **Prerequisites:** [Concepts](../concepts/butler-lifecycle.md), [Architecture](../architecture/butler-daemon.md).

## Overview

![Switchboard Design](./switchboard-design.svg)

The Switchboard Butler is the front door of the entire butler system. Every external interaction -- whether it arrives via Telegram, email, or a direct MCP call -- enters the system through Switchboard. It assigns canonical request context, uses an LLM runtime for message classification and decomposition, fans out work to one or more downstream specialist butlers, and records the full request lifecycle for audit and debugging.

Switchboard never handles domain logic itself. It classifies, routes, and tracks. If routing fails or the classifier is uncertain, the request falls through to the General butler as a safe default.

## Profile

| Property | Value |
|----------|-------|
| **Port** | 41100 |
| **Schema** | `switchboard` |
| **Modules** | calendar, telegram, memory, email, pipeline, switchboard |
| **Runtime** | codex (gpt-5.4-mini) |

## Schedule

| Task | Cron | Description |
|------|------|-------------|
| `memory_consolidation` | `0 */6 * * *` | Consolidate episodic memory into durable facts |
| `memory_episode_cleanup` | `0 4 * * *` | Prune expired episodic memory entries |
| `eligibility_sweep` | `*/5 * * * *` | Butler registry liveness sweep -- checks downstream butler health and updates routing eligibility |

## Tools

Switchboard exposes the standard core tool surface plus routing-specific tools:

- **`route`** -- Dispatch a classified message segment to a downstream butler via its `route.execute` entrypoint. Injects request context and trace metadata before dispatch.
- **Ingress connectors** -- Telegram bot and email bot connectors that normalize incoming messages into canonical request context before classification.
- **Registry management** -- Maintains the butler registry with liveness TTLs and route contract version negotiation. The eligibility sweep runs every 5 minutes to confirm downstream butlers are reachable.
- **Pipeline tools** -- Ingress deduplication to prevent the same message from being processed twice.

## Key Behaviors

**Request Context Assignment.** Every ingress message receives a UUID7 `request_id`, UTC timestamp, source channel identifier, endpoint identity, and sender identity before any routing decision. This context propagates to all downstream butlers unchanged.

**LLM-Driven Routing.** Switchboard uses a lightweight LLM runtime (Codex) to classify incoming messages and decide which specialist butler should handle them. A single message can be decomposed into multiple segments routed to different butlers (e.g., "Call Mom for her birthday and log my weight" splits into relationship and health segments).

**Prompt Injection Safety.** User content is passed as isolated data, never as executable instructions. The router prompt explicitly forbids obeying instructions inside user content. Output is validated against registry-known butlers only.

**Safe Fallback.** On parse failure, validation failure, or runtime error, the full request routes to the General butler. No message is silently dropped.

**Ingestion and Retention.** Switchboard persists all ingress payloads, routing decisions, and downstream outcomes in month-partitioned PostgreSQL tables. Hot data is retained for one month.

## Interaction Patterns

**Users interact with Switchboard indirectly.** They send messages via Telegram or email, and Switchboard routes them transparently. Users never need to specify which butler should handle their request.

**Other butlers interact with Switchboard through `notify`.** When a specialist butler needs to send a message to the user, it calls `notify()` which Switchboard receives, validates as a `notify.v1` envelope, and dispatches to the Messenger butler for delivery.

**Buffer system.** Switchboard uses a buffer queue (capacity 100, 3 workers) with a scanner that processes messages in batches of 50 every 30 seconds, providing backpressure when the system is under load.

## Verification

To confirm the Switchboard Butler's routing pipeline, registry liveness, and ingestion persistence are operating as described:

```bash
# 1. Confirm the butler is listening on the expected port
curl -s http://localhost:41100/health | python3 -m json.tool
# Expected: {"status": "ok", ...} with switchboard-specific fields

# 2. Verify the butler registry is populated and eligibility sweep has run
psql -h localhost -U butlers -d butlers -c \
  "SELECT butler_name, healthy, last_checked_at FROM switchboard.butler_registry
   ORDER BY butler_name;"
# Expected: one row per registered downstream butler; last_checked_at within the past 5 minutes

# 3. Confirm the eligibility_sweep task is scheduled at 5-minute intervals
psql -h localhost -U butlers -d butlers -c \
  "SELECT name, cron, enabled FROM switchboard.scheduled_tasks
   WHERE name = 'eligibility_sweep';"
# Expected: cron = '*/5 * * * *', enabled = true

# 4. Verify request IDs in routing log are UUID7 (starts with high timestamp bits)
psql -h localhost -U butlers -d butlers -c \
  "SELECT request_id, target_butler, routed_at
   FROM switchboard.routing_log
   ORDER BY routed_at DESC LIMIT 5;"
# Expected: request_id values are UUIDs; rows are populated as messages arrive

# 5. Confirm ingestion_events table holds hot data for the current month partition
psql -h localhost -U butlers -d butlers -c \
  "SELECT source_provider, COUNT(*) as event_count
   FROM switchboard.ingestion_events
   WHERE received_at >= date_trunc('month', now())
   GROUP BY source_provider ORDER BY event_count DESC;"
# Expected: rows grouped by provider (telegram, gmail, etc.) for this month

# 6. Verify safe fallback: any routing failure should have target_butler = 'general'
psql -h localhost -U butlers -d butlers -c \
  "SELECT routing_reason, COUNT(*) FROM switchboard.routing_log
   WHERE target_butler = 'general' GROUP BY routing_reason;"
# Expected: entries showing fallback routing with reason codes like 'parse_failure' or 'low_confidence'
```

## Related Pages

- [Architecture: Routing](../architecture/routing.md) -- routing pipeline internals
- [Messenger Butler](messenger.md) -- the delivery execution plane that Switchboard dispatches to
- [General Butler](general.md) -- fallback routing target
