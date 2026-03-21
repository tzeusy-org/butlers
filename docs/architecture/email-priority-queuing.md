# Email Priority Queuing

> **Purpose:** Defines how email messages are classified into priority tiers at ingestion and dequeued with tier-aware ordering in the Switchboard.
> **Audience:** Developers working on email connectors or Switchboard queue logic, operators tuning queue fairness.
> **Prerequisites:** [Routing Architecture](routing.md), [System Topology](system-topology.md).

## Overview

Email ingestion supports policy-tier-based priority queuing to prevent urgent messages from being buried behind bulk traffic during burst periods. The system assigns priority tiers at connector ingest time and dequeues by tier in the Switchboard while preserving FIFO ordering within each tier and enforcing starvation guards.

## Motivation

At 8:00 AM, 15 newsletter emails and one urgent doctor appointment confirmation may arrive together. Without tiered dequeue ordering, the urgent message waits behind bulk traffic. Priority queuing ensures that high-priority messages (from known contacts, replies to outbound mail, direct correspondence) are processed first.

## Connector-Side Tier Assignment

Before calling Switchboard ingest, the Gmail connector sets `control.policy_tier` to one of three values. Assignment rules are evaluated in deterministic order; the first matching rule wins:

### 1. `high_priority` — Known Contacts

Condition: The normalized sender address matches a cached known-contact address (sourced from relationship butler contact data via periodic export, refreshed every 15 minutes).

### 2. `high_priority` — Replies to User Outbound Mail

Condition: The `In-Reply-To` header references a `Message-ID` previously sent by the user. Requires a sent-message ID index available to the Gmail connector.

### 3. `interactive` — Direct Correspondence

All conditions must be met:
- User address is present in `To` or `Cc` recipients
- No `List-Unsubscribe` header
- No bulk signal in `Precedence` header (e.g., `bulk` or `list`)

### 4. `default` — Fallback

All messages that don't match rules 1-3.

### Normalization

Email address comparisons trim whitespace, strip wrapper formatting (angle brackets), and compare lowercase local/domain values. Header key checks are case-insensitive. Missing headers are treated as absent.

## Switchboard Queue Ordering

The Switchboard's `DurableBuffer` dequeues in tier order:

1. `high_priority`
2. `interactive`
3. `default`

Within a given tier, FIFO order is preserved by accepted ingest order.

### Starvation Prevention

To prevent permanent deferral of lower tiers during sustained high-priority bursts, a starvation guard is enforced:

- **`max_consecutive_same_tier`** (default: 10) — After N consecutive dequeues from tier T, if any lower-priority tier queue is non-empty, the next dequeue comes from the highest available lower tier.
- If no lower-priority queue is non-empty, processing continues from tier T.
- The consecutive counter tracks the currently served tier, resets to 1 when the dequeued tier changes, and after a forced lower-tier dequeue, the next selection re-evaluates from the highest non-empty tier.

## Known-Contact Integration

Known-contact detection requires Gmail connector access to relationship contact data. The recommended approach is periodic export (option 2 of three evaluated):

- Export includes normalized contact email addresses
- Connector consumes a local cached set at ingest time
- Cache metadata includes `generated_at` for staleness visibility
- If the contact cache is unavailable or stale beyond threshold, the connector continues with rules 2-4 and emits telemetry indicating degraded known-contact matching

## Telemetry

### Connector Metric

`butlers.connector.gmail.priority_tier_assigned` (counter) — incremented per ingested email after tier assignment. Attributes: `provider`, `endpoint_identity`, `policy_tier`, `assignment_rule` (one of `known_contact`, `reply_to_outbound`, `direct_correspondence`, `fallback_default`).

### Switchboard Metric

`butlers.switchboard.queue.dequeue_by_tier` (counter) — incremented per message dequeued. Attributes: `policy_tier`, `queue_name`, `starvation_override` (`true`/`false`).

## Dashboard Visibility

The dashboard exposes:
- A read-only "Email priority tier rules" panel on the filters page showing current rules, starvation guard configuration, and contact cache freshness
- Tier distribution visibility on the connector detail page with pie chart and counts by tier

## Related Pages

- [Routing Architecture](routing.md) — how priority-queued messages flow through classification
- [Pre-Classification Triage](pre-classification-triage.md) — deterministic routing that runs before LLM classification
- [Observability](observability.md) — metrics infrastructure
