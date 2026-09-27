# Gmail Ingestion Policy

> **Purpose:** Define the three-tier email ingestion policy that processes inbox events in proportion to their value, reducing LLM and storage cost while preserving high-value workflows.
> **Audience:** Developers and operators configuring email ingestion behavior.
> **Prerequisites:** [Gmail Connector](gmail.md), [Connector Interface Contract](../api_and_protocols/ingestion-envelope.md).

## Overview

Personal inboxes include a large share of low-value traffic (newsletters, promotions, social digests, automated notifications). Running full classification and butler fanout on all email creates avoidable token spend and noisy long-term memory. The tiered ingestion policy shifts low-value traffic to metadata-only or skip behavior before expensive routing and classification.

## Tier Definitions

| Tier | Name | Processing | Storage | Examples |
|---|---|---|---|---|
| 1 | Full | Submit full `ingest.v1` envelope, run classification/routing/butler processing | Full payload + downstream persistence | Direct correspondence, finance, health, travel, calendar invites |
| 2 | Metadata-only | Submit slim envelope, bypass LLM classification | Sender/subject/date/labels/summary reference only | Newsletters, marketing from known senders, social notifications |
| 3 | Skip | Connector does not submit to Switchboard | No message-level persistence (metrics only) | Spam, configurable promotions/social categories, low-value automated notifications |

### Normative Rules

- Tier assignment MUST happen before classification.
- Tier 2 MUST NOT invoke LLM classification.
- Tier 3 MUST NOT enqueue Switchboard ingress work.
- Default MUST be Tier 1 for safety (avoid dropping potentially important mail).

## Cost Model

The tiered approach reduces classification cost significantly:

| Scenario | Emails/day | Tier 1 fraction | Daily tokens | Daily cost | Savings vs. naive |
|---|---|---|---|---|---|
| Naive (all Tier 1) | 120 | 100% | 216,000 | $0.648 | -- |
| Tiered (35/40/25) | 120 | 35% | 75,600 | $0.227 | 65% |

Assumptions: 1,800 tokens/email average, $3.00/1M tokens blended rate. Downstream savings from fewer route fanouts and less storage churn are additional.

## Tier Assignment

Tier assignment happens in the connector (`src/butlers/connectors/gmail.py`, per-message ingest
path), in this order; the first stage that drops a message wins:

1. **Label filter** (`GMAIL_LABEL_INCLUDE` / `GMAIL_LABEL_EXCLUDE`, below). Excluded messages are
   Tier 3.
2. **Connector-scope ingestion rules** (`block` / `pass_through`). A `block` match is Tier 3.
3. **Global-scope ingestion rules**, evaluated by `IngestionPolicyEvaluator`
   (`src/butlers/ingestion_policy.py`) against `switchboard.ingestion_rules` in priority order,
   first match wins, fail-open on DB error. The resolved action sets the tier:

| Global rule action | Tier |
|---|---|
| `route_to:<butler>` | Tier 1 |
| `metadata_only` | Tier 2 |
| `skip` | Tier 3 |
| `low_priority_queue` | Tier 1 (deferred dispatch, not metadata-only) |
| No rule matches (`pass_through`) | Tier 1 (default for safety) |

Tier 3 messages are recorded in the connector's filtered-event buffer with a bounded preview
only; the raw payload is not retained.

## Envelope Contract by Tier

### Tier 1

Standard `ingest.v1` envelope with full normalized text and provider payload.

### Tier 2

Slim envelope that preserves identity and threading while minimizing payload size:

- `payload.raw` MUST be `null` (enforced by the Switchboard envelope contract in
  `roster/switchboard/tools/routing/contracts.py`).
- `payload.normalized_text` MUST contain subject-only text (no full body).
- `control.ingestion_tier` MUST be `"metadata"`.
- Switchboard MUST bypass LLM classification. It persists the event in `switchboard.message_inbox`
  like any other ingress, with `lifecycle_state = 'metadata_ref'` instead of `accepted`.

### Tier 3

Never submitted to Switchboard ingest.

## On-Demand Body Retrieval

Tier 2 stores references only. Full body is fetched on demand from the Gmail API by message ID. Fetching MUST NOT auto-promote Tier 2 to Tier 1; promotion requires explicit ingestion action.

## Gmail Label Filtering

`GMAIL_LABEL_INCLUDE` and `GMAIL_LABEL_EXCLUDE` are normative production controls applied before
ingestion-rule evaluation:

- `GMAIL_LABEL_EXCLUDE` takes precedence over include matches.
- Empty include list means "all labels allowed except excluded."
- Deployments SHOULD exclude `SPAM` and `TRASH`.
- Excluding `CATEGORY_PROMOTIONS` and `CATEGORY_SOCIAL` is configurable and expected for many users.

## Retention by Tier

| Tier | Retention |
|---|---|
| Tier 1 | Butler/domain retention policy (e.g., finance multi-year, health indefinite) |
| Tier 2 | `switchboard.message_inbox` retention (monthly partitions; the row expires with its partition) |
| Tier 3 | No message-level storage beyond the bounded filtered-event preview; metrics only |

## Dashboard Management

Ingestion rules are user-managed at `/ingestion/filters` (backed by the Switchboard
`/ingestion-rules` API in `roster/switchboard/api/router.py`): rule priority, enable/disable,
dry-run testing (`/ingestion-rules/test`), and include/exclude label configuration.

## Metrics

`src/butlers/connectors/gmail_policy.py` emits `butlers_connector_gmail_tier_assigned_total`
(labels `endpoint_identity`, `ingestion_tier`, `reason`) alongside the priority-tier and
label-filter counters. Rule evaluation itself emits the `butlers.ingestion.rule_*` counters from
`src/butlers/ingestion_policy_metrics.py`.

## Verification

To confirm the tiered ingestion policy is enforced as described:

```bash
# 1. Tier counters are emitted for each processed email
curl -s "http://localhost:9090/api/v1/query?query=sum+by+(ingestion_tier)(butlers_connector_gmail_tier_assigned_total)" \
  | python3 -m json.tool | grep -A2 ingestion_tier
# Expected: non-zero counts once emails have been processed

# 2. Tier 2 events land as metadata references, not accepted events
psql -h localhost -U butlers -d butlers -c \
  "SELECT lifecycle_state, ingestion_tier, COUNT(*) FROM switchboard.message_inbox
   GROUP BY 1, 2;"
# Expected: metadata-tier rows carry lifecycle_state = 'metadata_ref'

# 3. Default tier is Tier 1: an email from a new sender with no matching rule
#    appears with ingestion_tier = 'full' and goes through routing

# 4. The global rule set is visible through the dashboard API
curl -s "http://localhost:41200/api/switchboard/ingestion-rules" | python3 -m json.tool | grep action
# Expected: metadata_only and skip actions appear for configured low-value senders/domains
```

## Related Pages

- [Gmail Connector](gmail.md) -- Connector runtime and configuration
- [Connector Interface Contract](../api_and_protocols/ingestion-envelope.md) -- Full `ingest.v1` envelope spec
- [Attachment Handling](attachment-handling.md) -- Attachment fetch policy
- [Metrics](metrics.md) -- Connector statistics and dashboard API
