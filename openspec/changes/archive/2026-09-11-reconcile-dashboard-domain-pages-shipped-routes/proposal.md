## Why

The dashboard domain specifications still describe retired standalone contacts and costs pages,
a bare measurements route, and fixed spend polling even though the shipped dashboard has canonical
entity, health, Spend, and house-ledger surfaces. The stale prose turns an already-complete
reconciliation into apparent implementation work.

## What Changes

- Record the five deferred bu-58rlw7 findings as documentation dispositions.
- Correct health identity and measurements routing to the shipped contracts.
- Replace retired contacts and costs page requirements with compatibility aliases and canonical
  successor ownership.
- Record imported contact consumers without misrepresenting their backend-dead readers as
  supported APIs, and preserve live Spend consumers instead of treating a route move as wholesale
  feature deletion.
- Preserve the URL-backed memory maturity filter and anti-pattern attention row.
- Reconcile the affected shell, relationship, Spend, settings, topology, RFC, and inventory
  references.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `dashboard-butler-management`: the legacy contact-detail tab reference becomes a compatibility
  alias contract without renaming its existing scenario heading.
- `dashboard-domain-pages`: routes, identity, memory filters, and shared Spend readers match the
  shipped dashboard.
- `dashboard-relationship`: contact compatibility aliases point to the entity index.
- `dashboard-shell`: route, navigation, shortcut, and Settings panel projections use canonical
  destinations.
- `dashboard-spend-dashboard`: `/spend` is the canonical page.
- `dashboard-settings-console`: the Spend panel and attention route target `/spend`.

## Impact

Documentation and specifications only. No frontend, backend, API, schema, or runtime behavior
changes. `/contacts`, `/contacts/:contactId`, `/costs`, and `/settings/spend` remain live
compatibility aliases.
