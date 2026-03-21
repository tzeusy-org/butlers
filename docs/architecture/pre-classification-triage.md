# Pre-Classification Triage

> **Purpose:** Defines the deterministic pre-classification triage layer that routes email messages before LLM classification, eliminating 50-70% of classification calls.
> **Audience:** Developers working on Switchboard routing, operators managing triage rules, architects evaluating routing efficiency.
> **Prerequisites:** [Routing Architecture](routing.md), [Thread Affinity Routing](thread-affinity-routing.md).

## Overview

The pre-classification triage layer is a deterministic routing stage that runs after ingestion acceptance and before LLM-based classification. It evaluates incoming email messages against persistent rules and heuristics to decide whether a message can be routed directly, skipped, queued at low priority, or passed through to the LLM classifier. For typical personal-email workloads, this eliminates approximately 50-70% of LLM classification calls.

## Throughput Impact

For busy personal inboxes (40-120 emails/day) with 5-15 seconds per LLM classification in serial mode, a burst of 20 emails can create multi-minute wait times. With triage:

- 120 emails/day at 50-70% hit rate = 60-84 emails handled without LLM
- Burst of 20 emails reduced to 6-10 LLM calls
- Significant reduction in queue depth and classification wall time

## Rule Data Model

Rules are stored in `switchboard.triage_rules` with the following structure:

| Column | Type | Purpose |
|---|---|---|
| `id` | UUID | Primary key |
| `rule_type` | TEXT | One of: `sender_domain`, `sender_address`, `header_condition`, `mime_type` |
| `condition` | JSONB | Type-specific matching condition |
| `action` | TEXT | `skip`, `metadata_only`, `low_priority_queue`, `pass_through`, or `route_to:<butler>` |
| `priority` | INTEGER | Evaluation order (lower number = higher priority) |
| `enabled` | BOOLEAN | Dispatch gate |
| `created_by` | TEXT | One of: `dashboard`, `api`, `seed` |
| `deleted_at` | TIMESTAMPTZ | Soft-delete marker |

### Condition Schemas

**`sender_domain`** — Match by email domain. Supports `exact` and `suffix` matching. Example: `{"domain": "delta.com", "match": "suffix"}` matches both `mail.delta.com` and `delta.com`.

**`sender_address`** — Match by exact email address (lowercase RFC 5322 form). Example: `{"address": "alerts@chase.com"}`.

**`header_condition`** — Match by email header. Operations: `present`, `equals`, `contains`. Example: `{"header": "List-Unsubscribe", "op": "present"}`.

**`mime_type`** — Match by MIME content type across all parts/attachments. Supports exact matching and wildcard subtype (`image/*`). Example: `{"type": "text/calendar"}`.

## Evaluation Pipeline

### Pipeline Position

Triage runs:
1. After ingest acceptance and deduplication
2. After envelope normalization
3. Before classification runtime spawn

### Evaluation Order

1. **Thread affinity** (built-in, if enabled) — checked first. If a thread affinity hit produces an eligible route, triage stops immediately.
2. **Triage rules** — rows where `enabled=true` and `deleted_at IS NULL`, sorted by `priority ASC`, `created_at ASC`, `id ASC`. First match wins.
3. **No match** — returns `pass_through`, continuing to LLM classification.

### Output

The triage produces a `TriageDecision`:

```json
{
  "decision": "route_to|skip|metadata_only|low_priority_queue|pass_through",
  "target_butler": "finance",
  "matched_rule_id": "uuid-or-null",
  "matched_rule_type": "sender_domain|...|thread_affinity|null",
  "reason": "human-readable explanation"
}
```

### Runtime Cache

Active rules are cached in memory and refreshed via:
- Event-driven invalidation on rule mutation (create/update/delete/enable toggle)
- Periodic reload every 60 seconds

Reload is atomic (full rule set swap). On reload failure, the cache fails open (`pass_through`) rather than blocking ingest. Invalid rule rows are skipped and logged.

## Seed Rules

The system ships with default seed rules for common routing patterns:

| Priority | Type | Condition | Action | Rationale |
|---|---|---|---|---|
| 10 | sender_domain | `chase.com` (suffix) | `route_to:finance` | Bank alerts |
| 11 | sender_domain | `americanexpress.com` (suffix) | `route_to:finance` | Card notifications |
| 20 | sender_domain | `delta.com` (suffix) | `route_to:travel` | Flight updates |
| 21 | sender_domain | `united.com` (suffix) | `route_to:travel` | Flight updates |
| 30 | sender_domain | `paypal.com` (suffix) | `route_to:finance` | Payment activity |
| 40 | header_condition | `List-Unsubscribe` present | `metadata_only` | Newsletters |
| 41 | header_condition | `Precedence=bulk` | `low_priority_queue` | Bulk mail |
| 42 | header_condition | `Auto-Submitted=auto-generated` | `skip` | Auto-generated replies |
| 50 | mime_type | `text/calendar` | `route_to:relationship` | Calendar invites |

Seed rules are idempotent on repeated import and marked `created_by='seed'`.

## Dashboard API

Rule management is dashboard-first. The API surface includes:

- `GET /api/switchboard/triage-rules` — list rules (filterable by type and enabled status)
- `POST /api/switchboard/triage-rules` — create a new rule
- `PATCH /api/switchboard/triage-rules/:id` — update condition, action, priority, or enabled
- `DELETE /api/switchboard/triage-rules/:id` — soft-delete (sets `deleted_at`, disables)
- `POST /api/switchboard/triage-rules/test` — dry-run a rule against a sample envelope (read-only, no side effects)

## Telemetry

- `butlers.switchboard.triage.rule_matched` (counter) — when a rule or thread affinity matches. Attributes: `rule_type`, `action`, `source_channel`.
- `butlers.switchboard.triage.pass_through` (counter) — when no deterministic match occurs. Attributes: `source_channel`, `reason` (`no_match`, `cache_unavailable`, `rules_disabled`).
- `butlers.switchboard.triage.evaluation_latency_ms` (histogram) — end-to-end triage evaluation latency. Attributes: `result` (`matched`, `pass_through`, `error`).

## Related Pages

- [Routing Architecture](routing.md) — how triage fits into the overall routing pipeline
- [Thread Affinity Routing](thread-affinity-routing.md) — the first step in the triage pipeline
- [Email Priority Queuing](email-priority-queuing.md) — tier-based queue ordering for triaged messages
