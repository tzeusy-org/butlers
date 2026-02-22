# Switchboard Pre-Classification Triage Heuristic Contract

Status: Normative (Target State)
Last updated: 2026-02-22
Primary owner: Platform/Core

## 1. Purpose

This document defines a deterministic pre-classification triage layer for email ingest. The triage layer runs before Switchboard launches LLM classification and decides whether an email should:

- route directly to a target butler,
- skip routing,
- run metadata-only handling,
- enter a low-priority queue, or
- pass through to LLM classification.

Primary outcome: eliminate approximately 50-70% of LLM classification calls for personal-email workloads where deterministic routing signals already exist.

Related specs:
- `docs/roles/switchboard_butler.md` (routing precedence and ingest ownership)
- `docs/frontend/dashboard_email_filters.md` (management UX; sibling task `butlers-0bz3.6`)
- Thread-affinity companion spec (`butlers-0bz3.4`)

## 2. Scope and Non-Goals

In scope:
- Rule data model and persistence contract.
- Deterministic evaluation order and decision flow.
- Dashboard-facing API contract for CRUD and dry-run testing.
- Seed-rule contract.
- Metrics and migration requirements.

Out of scope:
- Detailed dashboard visual design (covered by `butlers-0bz3.6`).
- LLM prompt changes for classification.
- Connector-level label filtering policy (covered by tiered-ingestion spec `butlers-0bz3.5`).

## 3. Throughput Impact Model

### 3.1 Baseline

Assumptions for busy personal inboxes:
- 40-120 emails/day.
- 5-15 seconds per LLM classification in serial mode.
- Burst arrival of 20 emails can create multi-minute wait time if all messages require classification.

### 3.2 Expected impact

Triage removes deterministic messages before LLM routing:

- 120 emails/day x 50-70% triage hit = 60-84 emails handled without LLM.
- Remaining LLM classifications: 36-60/day.

Burst example (20 incoming emails):
- without triage: 20 LLM calls,
- with triage (50-70% hit): 6-10 LLM calls.

This reduces queue depth and total classification wall time while preserving deterministic behavior.

## 4. Rule Data Model

### 4.1 Table: `switchboard.triage_rules`

Required schema:

```sql
CREATE TABLE switchboard.triage_rules (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  rule_type TEXT NOT NULL,
  condition JSONB NOT NULL,
  action TEXT NOT NULL,
  priority INTEGER NOT NULL,
  enabled BOOLEAN NOT NULL DEFAULT TRUE,
  created_by TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  deleted_at TIMESTAMPTZ NULL,

  CONSTRAINT triage_rules_rule_type_check
    CHECK (rule_type IN ('sender_domain', 'sender_address', 'header_condition', 'mime_type')),

  CONSTRAINT triage_rules_action_check
    CHECK (
      action IN ('skip', 'metadata_only', 'low_priority_queue', 'pass_through')
      OR action LIKE 'route_to:%'
    ),

  CONSTRAINT triage_rules_created_by_check
    CHECK (created_by IN ('dashboard', 'api', 'seed')),

  CONSTRAINT triage_rules_priority_check
    CHECK (priority >= 0)
);
```

Indexes:

```sql
CREATE INDEX triage_rules_active_priority_idx
  ON switchboard.triage_rules (enabled, priority, created_at, id)
  WHERE deleted_at IS NULL;

CREATE INDEX triage_rules_rule_type_idx
  ON switchboard.triage_rules (rule_type)
  WHERE deleted_at IS NULL;

CREATE INDEX triage_rules_condition_gin_idx
  ON switchboard.triage_rules
  USING GIN (condition);
```

Soft-delete contract:
- API delete operations MUST set `deleted_at=NOW()` and `enabled=FALSE`.
- Runtime evaluators MUST ignore rows where `deleted_at IS NOT NULL`.

### 4.2 Condition schema by `rule_type`

#### `sender_domain`

```json
{
  "domain": "chase.com",
  "match": "exact"
}
```

Validation:
- `domain` MUST be lowercase and non-empty.
- `match` MUST be `exact` or `suffix`.

Example:
- `{ "domain": "delta.com", "match": "suffix" }` matches `mail.delta.com` and `delta.com`.

#### `sender_address`

```json
{
  "address": "alerts@chase.com"
}
```

Validation:
- `address` MUST be lowercase RFC 5322 mailbox form.

#### `header_condition`

```json
{
  "header": "List-Unsubscribe",
  "op": "present",
  "value": null
}
```

Validation:
- `header` comparison is case-insensitive.
- `op` MUST be one of `present`, `equals`, `contains`.
- `value` MUST be present and non-empty for `equals` and `contains`.
- `value` MUST be null or omitted for `present`.

#### `mime_type`

```json
{
  "type": "text/calendar"
}
```

Validation:
- `type` MUST be lowercase and non-empty.
- `type` supports:
  - exact matching (e.g. `text/calendar`),
  - wildcard subtype matching with `/*` suffix (e.g. `image/*`).
- Matching is evaluated across all normalized MIME parts/attachments in the envelope payload.

### 4.3 Thread-affinity interaction

Thread affinity is a deterministic pre-LLM rule category but is not persisted in `triage_rules`.

Contract:
- Switchboard MUST execute thread-affinity lookup before evaluating `triage_rules`.
- If thread affinity returns an eligible target, Switchboard MUST emit `route_to:<target>` and stop triage evaluation.
- If no affinity hit exists, Switchboard continues with `triage_rules` evaluation.

## 5. Evaluation Pipeline

### 5.1 Pipeline position

Triage runs:
1. after ingest acceptance and dedupe,
2. after envelope normalization,
3. before classification runtime spawn.

### 5.2 Inputs

Input contract is normalized `ingest.v1` envelope data, including:
- sender identity/address,
- source metadata,
- normalized headers,
- MIME parts/attachment metadata,
- canonical request context (`request_id`, `source_*`).

### 5.3 Output decision contract

`TriageDecision`:

```json
{
  "decision": "route_to|skip|metadata_only|low_priority_queue|pass_through",
  "target_butler": "finance",
  "matched_rule_id": "uuid-or-null",
  "matched_rule_type": "sender_domain|sender_address|header_condition|mime_type|thread_affinity|null",
  "reason": "human-readable explanation"
}
```

Rules:
- `target_butler` is required only when `decision=route_to`.
- `pass_through` means continue to LLM classification unchanged.
- `pass_through` can be produced by either:
  - no deterministic rule match, or
  - an explicit matched rule action (`action='pass_through'`) used for high-priority exceptions.

### 5.4 Deterministic evaluation order

Order:
1. thread affinity (built-in, if enabled),
2. `triage_rules` rows where `enabled=true` and `deleted_at IS NULL`, sorted by:
   - `priority ASC` (lower number first),
   - `created_at ASC`,
   - `id ASC`.

First match wins.
No match returns `pass_through`.

### 5.5 Runtime cache contract

Runtime MUST cache active rules in memory and refresh by either trigger:
- event-driven invalidation on rule mutation (create/update/delete/enable toggle),
- periodic reload every 60 seconds.

Reload behavior:
- reload must be atomic (swap full rule set),
- stale cache on reload failure MUST fail open (`pass_through`) rather than blocking ingest,
- invalid rule rows MUST be skipped and logged with actionable validation errors.

## 6. Dashboard API Surface

All rule-management UX is dashboard-first. The dashboard consumes these endpoints:

### 6.1 List rules

`GET /api/switchboard/triage-rules?rule_type=<optional>&enabled=<optional>`

Response:

```json
{
  "data": [
    {
      "id": "uuid",
      "rule_type": "sender_domain",
      "condition": {"domain": "chase.com", "match": "exact"},
      "action": "route_to:finance",
      "priority": 10,
      "enabled": true,
      "created_by": "dashboard",
      "created_at": "2026-02-22T00:00:00Z",
      "updated_at": "2026-02-22T00:00:00Z"
    }
  ],
  "meta": {
    "total": 1
  }
}
```

### 6.2 Create rule

`POST /api/switchboard/triage-rules`

Request:

```json
{
  "rule_type": "header_condition",
  "condition": {"header": "Precedence", "op": "equals", "value": "bulk"},
  "action": "low_priority_queue",
  "priority": 50,
  "enabled": true
}
```

Response: `201 Created` with created rule payload.

Validation contract:
- `rule_type` and `condition` MUST satisfy section 4.2.
- `action=route_to:<butler>` target MUST be an eligible registry butler.
- `action` MUST be one of `skip`, `metadata_only`, `low_priority_queue`, `pass_through`, or `route_to:<butler>`.

### 6.3 Update rule

`PATCH /api/switchboard/triage-rules/:id`

Request supports partial fields:
- `condition`,
- `action`,
- `priority`,
- `enabled`.

Response: `200 OK` with updated rule.

### 6.4 Soft delete rule

`DELETE /api/switchboard/triage-rules/:id`

Behavior:
- soft-delete only (`deleted_at`, `enabled=false`),
- returns `204 No Content`.

### 6.5 Test rule against sample envelope

`POST /api/switchboard/triage-rules/test`

Request:

```json
{
  "envelope": {
    "sender": {"identity": "alerts@chase.com"},
    "payload": {
      "headers": {
        "List-Unsubscribe": "<mailto:unsubscribe@example.com>"
      },
      "mime_parts": [
        {"type": "text/plain"}
      ]
    }
  },
  "rule": {
    "rule_type": "sender_address",
    "condition": {"address": "alerts@chase.com"},
    "action": "route_to:finance",
    "priority": 10,
    "enabled": true
  }
}
```

Response:

```json
{
  "data": {
    "matched": true,
    "decision": "route_to",
    "target_butler": "finance",
    "matched_rule_type": "sender_address",
    "reason": "sender address exact match"
  }
}
```

Contract:
- endpoint is dry-run only,
- endpoint MUST NOT write inbox/routing state,
- endpoint MUST apply the same evaluator used in production path.

## 7. Seed Rules Contract

The system MUST ship a seed set importable by dashboard/API.

Minimum default seed rules:

| Priority | Rule Type | Condition | Action | Rationale |
|---|---|---|---|---|
| 10 | sender_domain | `{"domain":"chase.com","match":"suffix"}` | `route_to:finance` | bank alerts/statements |
| 11 | sender_domain | `{"domain":"americanexpress.com","match":"suffix"}` | `route_to:finance` | card notifications |
| 20 | sender_domain | `{"domain":"delta.com","match":"suffix"}` | `route_to:travel` | itinerary and flight updates |
| 21 | sender_domain | `{"domain":"united.com","match":"suffix"}` | `route_to:travel` | itinerary and flight updates |
| 30 | sender_domain | `{"domain":"paypal.com","match":"suffix"}` | `route_to:finance` | payment activity |
| 40 | header_condition | `{"header":"List-Unsubscribe","op":"present"}` | `metadata_only` | newsletters/promotions |
| 41 | header_condition | `{"header":"Precedence","op":"equals","value":"bulk"}` | `low_priority_queue` | bulk mail |
| 42 | header_condition | `{"header":"Auto-Submitted","op":"equals","value":"auto-generated"}` | `skip` | auto-generated replies |
| 50 | mime_type | `{"type":"text/calendar"}` | `route_to:relationship` | calendar invites/updates |

Seed rules MUST be idempotent on repeated import and marked `created_by='seed'`.

## 8. Metrics Contract

Required telemetry:

1. `butlers.switchboard.triage.rule_matched` (counter)
- Increment when a rule (or thread affinity) matches, regardless of resulting action.
- Required attributes: `rule_type`, `action`, `source_channel`.

2. `butlers.switchboard.triage.pass_through` (counter)
- Increment only when no deterministic match occurs.
- Required attributes: `source_channel`, `reason` (`no_match|cache_unavailable|rules_disabled`).

3. `butlers.switchboard.triage.evaluation_latency_ms` (histogram)
- Measure end-to-end triage evaluation latency.
- Required attributes: `result` (`matched|pass_through|error`).

Cardinality policy:
- Metrics MUST NOT include raw email addresses, domains, thread IDs, or request IDs as attributes.

## 9. Migration Contract

A switchboard Alembic revision MUST create `switchboard.triage_rules` and indexes.

Chain requirement:
- newest known switchboard revision is `sw_005`; this migration MUST continue linearly as `sw_006` (or next direct successor if `sw_006` already exists at implementation time).

Migration scope:
- create table + constraints + indexes,
- optional seed loader hook (or companion seed operation),
- no backfill required.

Rollback requirement:
- downgrade MUST drop added indexes and table cleanly.

## 10. Dashboard Management Contract

Rule management UX is dashboard-first:
- Users MUST manage triage rules through the dashboard filters page and the API in section 6.
- Direct SQL/DB edits are unsupported operationally except emergency break-glass procedures.
- Dashboard changes MUST invalidate triage cache immediately (or within 60-second maximum refresh window).

This contract explicitly makes the dashboard the intended management surface for triage behavior.

## 11. Acceptance Criteria Mapping

1. Table schema defined in section 4.1 (with column types and constraints).
2. Condition JSONB schemas and examples defined in section 4.2.
3. Evaluation position and deterministic flow defined in section 5.
4. REST API surface with request/response schemas defined in section 6.
5. Seed rules included in section 7.
6. OpenTelemetry metric definitions included in section 8.
7. Dashboard-first management requirement explicitly defined in section 10.
