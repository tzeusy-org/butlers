# Tiered Email Ingestion Policy

Status: Normative (Target State)  
Last updated: 2026-02-22  
Primary owners: Switchboard + Email Connectors

Depends on:
- `docs/connectors/interface.md`
- `docs/connectors/gmail.md`
- Bead `butlers-0bz3.3` (pre-classification triage rules)
- Bead `butlers-0bz3.6` (dashboard email filters UX, superseded by `butlers-0bz3.11` `/ingestion?tab=filters`)

## 1. Purpose
This document defines a three-tier email ingestion policy so inbox events are processed in proportion to value:

- Tier 1: full pipeline (route + classify + process + store)
- Tier 2: metadata-only pipeline (store reference, no LLM classification)
- Tier 3: skip pipeline (do not ingest into Switchboard)

Goal: reduce LLM and storage cost while preserving high-value workflows (finance, health, direct correspondence, travel, and calendar/itinerary traffic).

## 2. Motivation and Cost Model

### 2.1 Why tiering is required
Personal inboxes typically include a large share of low-value traffic (newsletters, promotions, social digests, automated status updates). Running full classification + butler fanout on all email creates avoidable token spend and noisy long-term memory.

Tiering shifts low-value traffic to metadata-only or skip behavior before expensive routing/classification.

### 2.2 Savings model
Let:
- `V` = emails/day
- `p1`, `p2`, `p3` = tier fractions (`p1 + p2 + p3 = 1`)
- `T_full` = avg tokens/email for full LLM classification
- `R` = blended $/1M tokens

Baseline daily classification cost:

`C_baseline = (V * T_full / 1_000_000) * R`

Tiered daily classification cost:

`C_tiered = (V * p1 * T_full / 1_000_000) * R`

Savings:

`Savings = C_baseline - C_tiered`

### 2.3 Worked example (classification only)
Assumptions:
- `V = 120` emails/day
- `T_full = 1,800` tokens/email
- `R = $3.00` / 1M tokens
- Tier mix: `p1 = 0.35`, `p2 = 0.40`, `p3 = 0.25`

Results:
- Baseline tokens/day: `216,000`
- Baseline cost/day: `$0.648`
- Tiered tokens/day: `75,600`
- Tiered cost/day: `$0.227`
- Savings/day: `$0.421` (about `65%`)
- Savings/month (30d): `$12.63`

This model intentionally isolates classification spend. Downstream savings (fewer route fanouts, less storage churn, lower retrieval noise) are additional.

## 3. Tier Definitions

| Tier | Name | Processing behavior | Storage behavior | Examples |
|---|---|---|---|---|
| 1 | Full | Submit full `ingest.v1` envelope to Switchboard, run normal classification/routing and butler processing | Full payload + downstream butler persistence | Direct correspondence, finance transactions, health communication, travel confirmation, calendar invites |
| 2 | Metadata-only | Submit slim envelope, bypass LLM classification | Store sender/subject/date/labels/summary reference only | Newsletters, marketing from known senders, social notifications |
| 3 | Skip | Connector does not submit to Switchboard ingest | No message-level persistence (metrics only) | Spam, configurable promotions/social categories, low-value automated notifications |

Normative rules:
- Tier assignment MUST happen before classification.
- Tier 2 MUST NOT invoke LLM classification.
- Tier 3 MUST NOT enqueue Switchboard ingress work.

## 4. Tier Assignment and Triage Action Mapping
Tier assignment is driven by triage rules from `butlers-0bz3.3`, evaluated in priority order in the connector pre-classification path.

Action-to-tier mapping:
- `route_to` -> Tier 1
- `metadata_only` -> Tier 2
- `skip` -> Tier 3
- `low_priority_queue` -> Tier 1 (deferred dispatch path, not metadata-only)

If no rule matches:
- Default MUST be Tier 1 for safety (avoid dropping potentially important mail).

## 5. Envelope Contract by Tier

### 5.1 Tier 1 envelope
Tier 1 uses the standard `ingest.v1` contract from `docs/connectors/interface.md` with full normalized text and provider payload.

### 5.2 Tier 2 envelope
Tier 2 sends a slim envelope that preserves identity and threading while minimizing payload size:

```json
{
  "schema_version": "ingest.v1",
  "source": { "channel": "email", "provider": "gmail", "endpoint_identity": "gmail:user:alice@gmail.com" },
  "event": {
    "external_event_id": "gmail_message_id",
    "external_thread_id": "gmail_thread_id",
    "observed_at": "2026-02-22T10:00:00Z"
  },
  "sender": { "identity": "sender@example.com" },
  "payload": {
    "raw": null,
    "normalized_text": "Subject: ... "
  },
  "control": {
    "idempotency_key": "gmail:gmail:user:alice@gmail.com:gmail_message_id",
    "ingestion_tier": "metadata"
  }
}
```

Normative Tier 2 constraints:
- `payload.raw` MUST be `null`.
- `payload.normalized_text` MUST contain subject-only text (no full body).
- `control.ingestion_tier` MUST be `"metadata"`.
- Switchboard MUST bypass LLM classification and persist metadata reference only.

### 5.3 Tier 3 behavior
Tier 3 events are dropped at connector level (after rule evaluation) and never submitted to Switchboard ingest.

## 6. Tier 2 Metadata Storage Contract
Tier 2 records are stored in `switchboard.email_metadata_refs`.

Required schema:

```sql
CREATE TABLE switchboard.email_metadata_refs (
  id UUID PRIMARY KEY,
  gmail_message_id TEXT NOT NULL,
  thread_id TEXT,
  sender TEXT NOT NULL,
  subject TEXT NOT NULL,
  received_at TIMESTAMPTZ NOT NULL,
  labels JSONB NOT NULL,
  summary TEXT NOT NULL,
  tier INTEGER NOT NULL CHECK (tier = 2),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

Required indexes/uniqueness:
- Unique on `(gmail_message_id)` per mailbox identity boundary.
- Index on `(received_at DESC)` for timeline queries.
- Index on `(sender, received_at DESC)` for sender lookups.

Summary generation contract:
- `summary` MUST be one line.
- Summary generation MAY use deterministic heuristic extraction or a lightweight local model.
- The full Switchboard classifier model MUST NOT be invoked for Tier 2 summaries.

## 7. On-Demand Body Retrieval
Tier 2 stores references only. Full body is fetched on demand from Gmail API by message id.

Tool contract (normative behavior):
- Input: `message_id` and connector/mailbox identity context.
- Output: normalized full body + selected headers + safe metadata.
- Side effect policy:
  - Fetching MUST NOT auto-promote Tier 2 to Tier 1.
  - Promotion to Tier 1 requires explicit ingestion action (separate operation).

## 8. Connector Behavior by Tier
For each Gmail message:

1. Apply label include/exclude filters (section 9).
2. Evaluate triage rules in priority order.
3. Map rule action to tier (section 4).
4. Execute tier-specific behavior:
   - Tier 1: submit full envelope to Switchboard ingest.
   - Tier 2: submit slim metadata envelope (`ingestion_tier=metadata`).
   - Tier 3: skip submission, increment skip counters/metrics.

Failure handling:
- Retry semantics remain idempotent (same dedupe identity).
- Tier 3 skip decisions SHOULD still emit structured connector logs for auditability.

## 9. Gmail Label Filtering (Now Normative)
`GMAIL_LABEL_INCLUDE` and `GMAIL_LABEL_EXCLUDE` are normative controls (not future-only).

Rules:
- Label filters MUST be applied before triage evaluation.
- `GMAIL_LABEL_EXCLUDE` takes precedence over include matches.
- Empty include list means "all labels allowed except excluded labels."
- Deployments SHOULD exclude `SPAM` and `TRASH`.
- Excluding `CATEGORY_PROMOTIONS` and `CATEGORY_SOCIAL` is configurable and expected for many users.

This policy updates `docs/connectors/gmail.md` expectations: label filters are production controls for tiered ingestion, not placeholder settings.

## 10. Retention Policy by Tier
- Tier 1: follows butler/domain retention policy (for example finance multi-year retention, health indefinite where configured).
- Tier 2: default 90-day retention for `email_metadata_refs`; configurable per category/profile.
- Tier 3: no message-level storage. Only connector metric counters and aggregate stats remain.

Retention enforcement:
- Tier 2 retention MUST be enforced with scheduled pruning.
- Tier 3 retention follows metrics retention in connector statistics policy.

## 11. Dashboard Management UX
Tier rules are user-managed through dashboard filters UX:

- Original bead contract: `butlers-0bz3.6` (email filters page)
- Current consolidated UX: `butlers-0bz3.11` (`/ingestion?tab=filters`)

Required UX capabilities:
- Rule table mapping conditions -> actions -> effective tier.
- Rule priority ordering and enable/disable toggles.
- Dry-run testing against recent emails.
- Include/exclude label configuration.

No direct DB editing is required for normal operations.

## 12. Metrics and Observability
Connectors and Switchboard MUST emit counters:

- `butlers.connector.gmail.tier_1_ingested`
- `butlers.connector.gmail.tier_2_metadata`
- `butlers.connector.gmail.tier_3_skipped`

Recommended dimensions:
- `endpoint_identity`
- `rule_id` (if matched)
- `reason` (for skips, e.g., `label_excluded`, `rule_skip`)

Operational interpretation:
- Rising `tier_3_skipped` with stable inbox volume indicates successful noise suppression.
- Rising `tier_2_metadata` with flat Tier 1 indicates lower LLM load while preserving searchable references.

## 13. Non-Goals
This policy does not:
- Replace canonical `ingest.v1` ownership by Switchboard.
- Define full UI implementation details (covered in dashboard specs).
- Define backfill orchestration (covered by selective backfill spec work).
