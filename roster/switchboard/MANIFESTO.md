# Switchboard — Infrastructure Contract

**Service type:** Staffer (infrastructure)
**Port:** 41100
**Schema:** `switchboard`

---

## Purpose

The Switchboard is the sole entry point for all inbound messages. It classifies incoming user messages and routes them to the appropriate domain butler. It is the routing backbone of the butler ecosystem.

---

## Responsibilities

- **Ingress ownership:** Receive all inbound messages regardless of origin channel (Telegram, Email, direct MCP).
- **Message classification:** Classify each message and determine the target domain butler(s). Only butler-typed agents are routing candidates; staffer-typed agents are excluded from user-message classification.
- **Routing execution:** Dispatch classified messages to the selected domain butler(s), including fanout for multi-domain messages.
- **Durable buffer:** Maintain a priority-tiered, crash-recovery-capable ingestion buffer. Messages are retained until successfully dispatched.
- **Agent registry:** Maintain liveness state for all registered agents (butlers and staffers). Run periodic eligibility sweeps.
- **Connector registry:** Track which ingress connectors are active, healthy, and eligible for message receipt.
- **Butler-to-staffer routing:** Route delivery requests (e.g., `notify()`) from domain butlers to infrastructure staffers such as Messenger.

## Non-Responsibilities

- Switchboard does **not** register with another switchboard instance.
- Switchboard does **not** perform outbound user-channel delivery (delegated to Messenger).
- Switchboard does **not** execute domain logic (delegated to domain butlers).

---

## SLAs

| Metric | Target |
|---|---|
| Message classification latency | < 5 s p99 under normal load |
| Buffer durability | No message loss on restart (DB-backed cold path) |
| Eligibility sweep interval | Every 5 minutes |
| Insight delivery cycle | Daily at 08:00 UTC |
| Availability | Must be running before any domain butler starts; no planned downtime window |

---

## Failure Modes and Recovery

| Failure | Symptom | Recovery |
|---|---|---|
| Classification timeout | Message stays in buffer, dispatch not attempted | Cold-path scanner reclaims after `scanner_grace_s`; retried automatically |
| Domain butler unreachable | Dispatch returns error; route marked ineligible | Eligibility sweep reinstates the route once butler is healthy |
| Buffer worker crash | In-flight dispatches lost | Scanner recovers items from DB after `scanner_grace_s` grace period |
| Switchboard restart | In-memory queue drained | Buffer scanner re-ingests unfinished items from DB on startup |
| Staffer classification leak | Staffer appears as routing candidate | Classification layer enforces type=butler filter; staffers are never candidates |

---

## Dependency Graph

### Depends On

- **PostgreSQL (`butlers.switchboard` schema):** Durable buffer, agent registry, connector registry
- **Domain butlers (general, health, finance, …):** Routing targets; must be reachable at registered ports
- **Messenger staffer:** Downstream target for `notify()` delivery intents from butlers

### Depends On Switchboard

- **All domain butlers:** Register at startup; send heartbeats; use Switchboard as the routing entry point
- **All staffers (including Messenger):** Register at startup; remain reachable for butler-to-staffer routing
- **Ingress connectors:** Forward all inbound messages here

---

## Capacity Limits

| Parameter | Value |
|---|---|
| In-memory queue capacity | 100 messages |
| Dispatch worker count | 3 concurrent |
| Scanner interval | 30 s |
| Scanner grace period | 10 s |
| Scanner batch size | 50 items |
| Max concurrent runtime sessions | 3 |

---

## Escalation

If the Switchboard is unreachable, all inbound message processing halts. This is a **critical** failure.

- Domain butlers cannot receive new user messages.
- Ingress connectors have no valid delivery target.
- Outbound delivery via `notify()` may also be disrupted if butler-to-staffer routing is unavailable.

Escalate immediately with severity CRITICAL.
