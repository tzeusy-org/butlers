## Why

Finance derives `recurring_groups.last_seen_date` and `next_expected_date` from transaction
history, and separately stores tracked subscription renewal dates, but neither path proves which
producer was expected to observe the next charge. Treating an elapsed date as a missed renewal can
therefore turn a dead feed, a one-off CSV import, or mixed ingestion into a fabricated claim about
the owner's payment or subscription state.

## What Changes

- Define the authoritative producer mapping for Gmail-ingested, explicitly owner-entered,
  SimpleFIN, CSV/bulk, API/bank-sync, legacy/backfill, and mixed Finance records.
- Bind Gmail authority to the exact server-derived source endpoint; another healthy Gmail account
  must never authorize absence for a dead sibling account.
- Inventory the separately registered `track_subscription_fact` MCP writer and classify its
  property facts as outside current recurrence readers, and unmeasurable if later consumed without
  trusted provenance.
- Require recurring-group producer resolution to use the complete contributing transaction source
  set; row order, merchant name, `source_message_id`, and a generic `source` label cannot choose
  authority.
- Distinguish a tracked `subscriptions.next_renewal` declaration from evidence that a renewal
  charge was observed, missed, paid, cancelled, or stopped.
- Make stale, dead/offline, unhealthy, missing, mixed, unsupported, or unreadable producer evidence
  unmeasurable with no owner-behavior or missed-renewal wording.
- Preserve the existing forward-looking `subscription-renewal` and `bill-predicted` policies;
  this change creates no new absent-recurrence notification.
- Amend RFC 0012 and RFC 0029, the Finance capability deltas, and Finance operator/dashboard
  guidance in the same change.
- Implement the shared `producer_endpoint_identity` schema/helper/API migration, existing Health
  compatibility, exact-endpoint liveness, and migrated-PostgreSQL proof under continued
  `bu-8cdl1.3` ownership.

Explicit non-goals:

- No Gmail-as-universal-Finance-source assumption.
- No new notification, renewal, cancellation, payment, or inferred subscription-state policy.
- No new scheduled-producer liveness type for SimpleFIN and no connector guess for it.
- No cross-schema read shortcut and no runtime adoption beyond the executable planning contract.
- No backfill of producer claims onto current transaction, recurring-group, or subscription rows.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `finance-supporting-tables`: Bind recurring-group dates to a complete, single-producer source set
  or an unmeasurable result.
- `butler-finance`: Require honest recurrence/renewal absence semantics across tracked and inferred
  Finance records.
- `finance-alerts`: Preserve only the existing approved proactive policies and forbid inferred
  missed-renewal output from expected-signal absence.
- `expected-signals`: Require connector-backed signals to carry and evaluate the exact producer
  endpoint identity.

## Impact

The contract covers transaction and subscription writers, `detect_recurring()`,
`subscription_audit()`, the separate `track_subscription_fact` property-fact surface,
`predict_bills()`, `run_bill_reconciliation_sweep()`, the daily subscription-renewal candidate
path, RFC 0029 expected-signal adoption, and the Finance dashboard subscription/recurrence
presentation. Continued `bu-8cdl1.3` owns the shared endpoint-aware expected-signals migration,
Health transition, and Finance runtime adoption.
