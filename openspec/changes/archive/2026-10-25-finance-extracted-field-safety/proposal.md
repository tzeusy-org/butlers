# Finance extracted-field safety

## Why

An extracted renewal date can currently replace an earlier date before the renewal scan's SQL
window filter, suppressing a warning. Extracted service and payee names can also survive storage
and insight batching as active link text. These are two connected integrity gaps in the existing
Finance path, not a request for a new financial adviser or a second message renderer.

## Status and authority

This is the governing security-maintenance source preparation for bu-svqpzo.19, based on
`3e07dfec26de9544d9e3bbb30e2186df83e1a127`. ROOT recorded existing-authority acceptance and
selected the owning Finance MCP currentness mechanism on 2026-10-25 (decision 3736), following
DIFFERENT review of the exact behavior matrix. The resulting canonical/archive source still
requires DIFFERENT conformance and actual merge before .19 can complete. No new SQL exception,
role or grant is authorized. The currentness operation is future .16 implementation, not a
claimed delivered endpoint. No runtime security fix is delivered by this source-only change.

## What changes

- Specify a retained baseline and immutable unresolved claims for renewal dates and complete
  amount/currency pairs. Extracted disagreement cannot silently replace a baseline or owner value.
- Select the earliest still-actionable date before the actual SQL filter, and use it consistently
  in the warning, ledger period, deduplication key and expiry. Explicit owner writes resolve prior
  claims for the supplied fields and invalidate known superseded pending warnings.
- Specify server-derived origins and exact revisions for service/payee names, without mutating
  their raw stored values or granting authority to a caller's provenance labels.
- Persist a monotonic protection floor through the real broker hold, digest, synthesis, amendment
  and retry paths, then bridge the production notification closure to the existing canonical
  Messenger whole-envelope renderer. Owner-only, independently unexposed output stays ordinary.
- Protect the distinct successful in-place amendment closure with canonical whole-replacement rendering or truthful pre-provider refusal and retained safe fold; ordinary owner-only edits remain compatible.
- Qualify the existing owner cancellation-door scenario: owner authority over its stored URL is
  preserved, while an independently exposed or mixed-origin envelope renders **all** links inert.
- Bind the complete implementation packets for bu-svqpzo.16 and bu-svqpzo.17, with real producer,
  transaction, migration, currentness and final-provider proof seams.

## Scope and non-goals

The four delta specs, design and behavior matrix form one coherent contract. Existing recurrence
absence policy, yearly 14-day reminder horizon, next-local-midnight expiry, priorities, quiet hours,
budgets, deduplication, price thresholds, attempt accounting, permissions and unknown-outcome
semantics remain. There is no new URL parser, dashboard editor, FX calculation, automatic pending
claim confirmation, alert for elapsed dates, approval bypass, runtime activation or provider send.
No generic deferred queue is added to the broker's existing durable delay/retry path.

## Impact and completion

The governing requirements are REQ-finance-alerts-003, REQ-finance-supporting-tables-002,
REQ-proactive-insight-engine-035 and REQ-insight-delivery-001, plus the complete modified
REQ-finance-alerts-002. ID 034 already belongs to Bounded Explicit Insight Feedback and is retained.
The source trace and all 31 proposed behavior/proof rows are in
`docs/concepts/finance-extracted-field-safety.md`.

This draft changes no runtime behavior and adds no tests: Tests: +0 ~0 -0. Completion of .19 requires
DIFFERENT final conformance, merged canonical spec application/archive, and coordinator application of
the approved .16/.17 packets. Prepared governing text is not evidence that either security fix works.
