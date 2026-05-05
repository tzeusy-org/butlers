## Why

`openspec/changes/detail-page-archetype/tasks.md` §5.1 explicitly deferred the
ConnectorDetailPage spec home decision and filed it as an open item:

> ConnectorDetailPage spec home — decide whether it lives in `connector-base-spec`,
> a new `dashboard-connector-detail` spec, or an existing spec.

The `detail-page-archetype` change brought six of the seven adopters into
spec-level conformance (Fact, Rule, Episode, Contact, Butler, Entity). Connector
was intentionally left out because its spec home was unresolved at the time PR #1397
merged.

That PR implemented the archetype migration in code — `ConnectorDetailPage.tsx` already
uses `<DetailPage>` (which wraps `<Page archetype="detail">`) with `pulse`, `primary`,
and `auxiliary` slots wired correctly. The spec has not caught up.

## What Changes

- **Modified capability**: `connector-base-spec` — a new ADDED requirement documents
  that `ConnectorDetailPage` MUST conform to the `detail-page-archetype` spec, with
  explicit slot mappings for each of the six body tiers. This is a documentation delta
  to the existing `§Requirement: Dashboard Connector Page` section, not a new
  capability spec.

## Spec Home Decision

**Chosen home: `connector-base-spec`.**

Three options were evaluated:

1. **`connector-base-spec`** — The spec already owns all connector dashboard
   requirements (`§Requirement: Dashboard Connector Page`, `§Requirement: Pydantic
   Response Models`, `§Requirement: Connector Settings API`). The archetype conformance
   delta is a tightening of the existing dashboard connector page requirement, not a
   new page. Adding it here follows the same pattern as the sibling adopters: each
   archetype conformance delta was added to the spec that owns the original page
   requirement (`dashboard-domain-pages` for Fact/Rule/Episode, `dashboard-relationship`
   for Contact, `dashboard-butler-management` for Butler).

2. **New `dashboard-connector-detail` spec** — Clean isolation, but creates a thin
   spec (one requirement) that duplicates context already in `connector-base-spec`.
   The cost of a new spec file is not justified when the parent spec already has a
   dashboard section.

3. **Existing dashboard spec (`dashboard-domain-pages`, `dashboard-butler-management`)**
   — Connector is not a domain managed by a butler (it is a standalone ingestion
   process), so grafting it onto a butler-domain spec would be misleading.

**Verdict:** Option 1. The archetype conformance requirement lives in
`connector-base-spec` as a companion to `§Requirement: Dashboard Connector Page`.

## Capabilities

### Modified Capabilities

- `connector-base-spec`: `ConnectorDetailPage` archetype conformance documented.
  Slot mappings cover: `pulse` = ingest health strip, `primary` = events feed / status
  / counters / discretion settings / rules, `supporting` = omitted (not applicable),
  `auxiliary` = checkpoint cursor (dangerous operator action), `practical` =
  reset / delete drawer (destructive actions, collapsed by default).

## Impact

- **No new specs.** One delta to `connector-base-spec`.
- **No frontend changes.** `ConnectorDetailPage.tsx` is already conformant in code.
  This change closes the spec-to-implementation gap.
- **No database changes. No new API endpoints.**

## Source References

- `openspec/changes/detail-page-archetype/tasks.md` §5.1 — deferred open item
- `openspec/changes/detail-page-archetype/design.md` §D4 — ConnectorDetailPage scope note
- `openspec/specs/detail-page-archetype/spec.md` — archetype contract being adopted
- `frontend/src/pages/ConnectorDetailPage.tsx` — implementation reference
- `frontend/src/components/layout/DetailPage.tsx` — `<DetailPage>` shell wrapper
- `about/lay-and-land/detail-page-audit.md` §1.7 — ConnectorDetailPage audit score (17/25)
