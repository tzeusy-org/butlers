# Email Priority Queuing via Policy Tiers

Status: Normative (Target State)
Last updated: 2026-02-22
Primary owner: Platform/Core

## 1. Purpose and Motivation

Email ingestion currently accepts `control.policy_tier` in `ingest.v1`, but Gmail ingestion does not yet assign tier values based on message urgency.

This causes poor queue fairness during burst periods. Example: at 8:00 AM, 15 newsletter emails and one urgent doctor appointment confirmation arrive together. Without tiered dequeue ordering, the urgent message can wait behind bulk traffic.

This spec defines how to:
- assign policy tiers at connector ingest time,
- dequeue by tier in Switchboard while preserving FIFO within a tier,
- prevent starvation of lower tiers,
- expose tier behavior in dashboard and telemetry.

Related documents:
- `docs/connectors/interface.md`
- `docs/roles/switchboard_butler.md`

## 2. Connector-Side Tier Assignment (Gmail)

### 2.1 Required behavior

Before calling Switchboard ingest, the Gmail connector MUST set `control.policy_tier` to one of:
- `high_priority`
- `interactive`
- `default`

Tier assignment MUST run in deterministic order. The first matching rule wins.

### 2.2 Ordered assignment rules

1. `high_priority` for known contacts
- Condition: normalized sender address matches a cached known-contact address.
- Contact source: relationship butler contact data (via integration contract in section 4).

2. `high_priority` for replies to user outbound mail
- Condition: `In-Reply-To` references a `Message-ID` previously sent by the user.
- Implementation note: requires a sent-message id index available to the Gmail connector.

3. `interactive` for direct correspondence
- Conditions (all required):
  - user address is present in `To` or `Cc` recipients,
  - no `List-Unsubscribe` header,
  - no bulk signal in `Precedence` header (for example `bulk` or `list`).

4. `default` fallback
- All messages that do not match rules 1-3.

### 2.3 Normalization constraints

The connector MUST normalize header matching to avoid casing and formatting drift:
- email address comparisons trim surrounding whitespace, strip wrapper formatting (for example angle brackets), and compare lowercase local/domain values,
- provider-specific alias rewrites (for example Gmail dot removal or `+tag` stripping) MUST NOT be applied unless both compared datasets are normalized with the same provider-aware routine,
- header key checks are case-insensitive,
- missing headers are treated as absent (not as match).

## 3. Switchboard Queue Ordering

### 3.1 Priority contract

Switchboard DurableBuffer dequeue order MUST be:
1. `high_priority`
2. `interactive`
3. `default`

Within a given tier, FIFO order MUST be preserved by accepted ingest order.

### 3.2 Starvation prevention

To prevent permanent deferral of lower tiers during sustained high-priority bursts, dequeue logic MUST enforce a starvation guard:
- `max_consecutive_same_tier` (default: `10`)
- After `N` consecutive dequeues from tier `T`, if any lower-priority tier queue is non-empty, the next dequeue MUST come from the highest available lower tier.
- If no lower-priority queue is non-empty, processing MAY continue from tier `T`.
- Consecutive counter semantics:
  - counter tracks the currently served tier,
  - counter resets to `1` whenever dequeued tier changes,
  - after a forced lower-tier dequeue, next selection re-evaluates from highest non-empty tier.

This yields bounded fairness while preserving urgency preference.

## 4. Known-Contact Integration Contract

Known-contact detection for rule 1 requires Gmail connector access to relationship contact data.

### 4.1 Integration options

1. Shared read-only DB view
- Pros: fresh data.
- Cons: runtime cross-butler coupling, stricter permission boundaries.

2. Periodic export to connector-accessible store
- Pros: low ingest latency, loose coupling, failure isolation.
- Cons: eventually consistent freshness.

3. Synchronous API call to relationship butler
- Pros: freshest read.
- Cons: adds ingest-time network dependency and latency.

### 4.2 Recommended approach

The target default is option 2: periodic export every 15 minutes.

Requirements:
- export includes normalized contact email addresses,
- connector consumes local cached set at ingest time,
- cache metadata includes `generated_at` for staleness visibility.

If contact cache is unavailable or stale beyond policy threshold, connector MUST continue ingest using rules 2-4 and MUST emit telemetry indicating degraded known-contact matching.

## 5. Dashboard Visibility

### 5.1 `/filters` page

Dashboard MUST show a read-only "Email priority tier rules" panel that summarizes:
- current ordered rules,
- active starvation guard default/config,
- contact cache source and freshness timestamp.

### 5.2 Connector detail page

Connector detail MUST include tier distribution visibility for selected time window:
- pie chart for `high_priority`, `interactive`, `default`,
- counts and percentages by tier,
- last-updated timestamp for metric snapshot.

## 6. Telemetry Contract

### 6.1 Connector metric

`butlers.connector.gmail.priority_tier_assigned` (counter)

Increment once per ingested email after tier assignment.

Required low-cardinality attributes:
- `provider=gmail`
- `endpoint_identity`
- `policy_tier`
- `assignment_rule` (`known_contact|reply_to_outbound|direct_correspondence|fallback_default`)

### 6.2 Switchboard metric

`butlers.switchboard.queue.dequeue_by_tier` (counter)

Increment once per message dequeued by DurableBuffer.

Required low-cardinality attributes:
- `policy_tier`
- `queue_name` (when multiple named queues exist)
- `starvation_override` (`true|false`)

### 6.3 Cardinality and correlation rules

Metrics MUST follow the low-cardinality discipline already defined in `docs/roles/switchboard_butler.md`:
- no raw message IDs,
- no raw sender identities,
- no free-text payload fragments.

Tier counters SHOULD be correlated with existing Switchboard queue depth and lifecycle metrics for SLO and fairness analysis.

## 7. Acceptance Mapping

This spec satisfies issue acceptance criteria by defining:
1. specific `policy_tier` assignment rules and signals,
2. Switchboard dequeue ordering by tier,
3. starvation prevention behavior and default limit,
4. contact-list integration options plus recommended approach,
5. dashboard visibility requirements,
6. required OpenTelemetry metrics.
