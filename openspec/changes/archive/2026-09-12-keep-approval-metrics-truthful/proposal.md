## Why

`GET /api/approvals/metrics` currently turns a failed `pending_actions` or
`approval_rules` pool into a numeric zero. Dashboard readers can therefore
present an incomplete count as a calm all-clear or hide a section whose source
could not be read. The owner has approved a focused availability-contract
correction; it must land vertically so producer and readers agree on what is
known.

## What Changes

- Preserve successful approval metric contributions while publishing stable,
  per-pool degraded metadata for `pending_actions` and `approval_rules` reads.
- Make approval, overview, dashboard, Sidebar, and Settings readers render a count only
  when its backing pool is complete; otherwise rich readers name the unavailable
  source and offer a safe read retry where they own a query, while Sidebar shows
  an accessible unavailable marker on its existing destination.
- Make autonomy-suggestion and rule-promotion readers distinguish a successful
  empty response from an unavailable response, retaining usable cached data
  with an explicit degraded state rather than hiding the section.
- Preserve all approval authorization, confirmation, lifecycle, executor,
  retention, and mutation behavior.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `dashboard-api`: Approval metric responses distinguish configured empty pools
  from failed action or rule sources.
- `dashboard-approvals`: Approval and promotion surfaces name unavailable
  reader sources instead of rendering an empty or zero-derived state.
- `dashboard-overview`: Pending-approval attention and Now signals do not use
  partial aggregate counts as a trustworthy all-clear.
- `dashboard-visibility`: The approval KPI and Sidebar badge remain numeric
  only when the pending-action aggregate is complete.

## Impact

- `src/butlers/api/routers/approvals.py` and approval response models.
- Typed frontend approval API responses, approval hooks, overview derivation,
  `ApprovalsPage`, `DashboardPage`, `Sidebar`, `SettingsConsolePage`, and the
  existing rule-promotion presentation readers.
- Focused API, page, model, and promotion behavior coverage; no dependency,
  persistence, credential, or mutation changes.
