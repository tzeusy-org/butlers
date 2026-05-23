# Project Direction: Ingestion Redesign Completion

**Date:** 2026-05-23
**Scope:** `/ingestion` redesign gap between live dashboard and
`pr/overview/ingestion-redesign` prototype
**OpenSpec change:** `openspec/changes/complete-ingestion-redesign-parity`
**Beads epic:** `bu-y25mj`

## Direction

The project should treat `/ingestion` as the operator's Dispatch console for
external signal flow, not as a generic dashboard table. The real direction is a
route-backed operational surface:

1. Timeline as a ledger stream.
2. Connectors as a dense roster.
3. Connector detail as a two-zone operational dossier.
4. Filters as an explainable pipeline.

This aligns with the dashboard doctrine: the page exists so the owner can
detect, diagnose, and act on external events entering the system. It should be
read-mostly, dense, inspectable, and backed by real data.

## What The Project Should Work On Next

Work should start with spec ratification, then proceed through the Beads graph
below. The first implementation bead must replace the page-level tab shell and
establish the route/primitive foundation. Timeline, Connectors, and Filters can
then proceed in parallel because they share the foundation but own distinct
surfaces.

The project should not continue treating the old closed ingestion epic as proof
that the redesign is complete. It was closed without a live visual parity gate.

## What To Stop Pretending

- Stop pretending an archived OpenSpec change means the prototype landed
  visually.
- Stop treating `Card`/table/tab cleanup as a minor polish pass. The prototype
  defines a different information model.
- Stop accepting "all beads closed" as complete unless live screenshots and
  route smoke tests prove the target behavior.
- Stop leaving prototype assets as passive references. For this work they are
  binding inputs unless a spec amendment says otherwise.

## Evidence

### Prototype Ground Truth

- `pr/overview/ingestion-redesign/INGESTION_HANDOFF.md` defines the target
  routes, surfaces, data shapes, component inventory, URL state, and compliance
  rules.
- The handoff explicitly says not to reach for `<Card>` for the primary
  surfaces.
- The prototype requires `IngestionSubNav`, Timeline ledger rows/drawer,
  connector roster/detail, pipeline diagram, priority senders, channel defaults,
  and shared Dispatch primitives.

### Live Implementation Gap

- `frontend/src/pages/IngestionPage.tsx` still owns the old page-level tab
  shape.
- `frontend/src/components/ingestion/TimelineTab.tsx` still renders a visible
  `Ingestion Events` card/table.
- `frontend/src/components/ingestion/ConnectorsListPage.tsx`,
  `ConnectorCard.tsx`, `ConnectorDetailPage.tsx`, and switchboard filter
  components still use card-centric composition.
- Search showed the prototype component names are absent from `frontend/src`
  except as references in handoff/design docs.

### Beads State

Before this project-direction pass, no open bead specifically owned the
remaining ingestion visual parity gap. Existing open redesign work was mostly
entity/detail-page or OAuth-scope related.

## OpenSpec Assets Generated

Created `openspec/changes/complete-ingestion-redesign-parity/`:

- `proposal.md`: explains why the archived redesign did not close the visible
  gap and scopes the completion change.
- `design.md`: records decisions, risks, and reconciliation summary.
- `tasks.md`: provides the implementation and verification breakdown.
- `specs/dashboard-ingestion-dispatch-console/spec.md`: new binding capability
  for routes, visual language, Timeline, Connectors, connector detail, Filters,
  data states, and visual verification.
- `specs/dashboard-shell/spec.md`: route-map delta for ingestion sub-routes.

Validation:

```bash
openspec validate complete-ingestion-redesign-parity --strict
```

Result: valid.

## Beads Graph Generated

Epic:

- `bu-y25mj` - Epic: Complete `/ingestion` visual redesign parity

Children:

| Bead | Title | Depends on |
|---|---|---|
| `bu-y25mj.2` | Ratify complete-ingestion-redesign-parity OpenSpec | none |
| `bu-y25mj.1` | Implement ingestion Dispatch route foundation | `bu-y25mj.2` |
| `bu-y25mj.4` | Build ingestion Timeline ledger and drawer | `bu-y25mj.1` |
| `bu-y25mj.3` | Build ingestion connector roster and detail | `bu-y25mj.1` |
| `bu-y25mj.5` | Build ingestion Filters pipeline | `bu-y25mj.1` |
| `bu-y25mj.6` | Verify ingestion redesign visual parity | `bu-y25mj.3`, `bu-y25mj.4`, `bu-y25mj.5` |
| `bu-y25mj.7` | Reconcile ingestion redesign implementation against spec | `bu-y25mj.6` |
| `bu-y25mj.8` | Generate epic report for: Complete `/ingestion` visual redesign parity | `bu-y25mj.7` |

Cycle check:

```bash
bd dep cycles --json
```

Result: `[]`.

## Reconciliation Passes

### R1: Doctrine

The proposed work is aligned. Ingestion is not a marketing page or generic
admin table; it is a single-owner operational audit surface. Ledger, roster,
detail, and pipeline views directly serve detect, diagnose, and act loops.

### R2: Existing Specs

`ingestion-event-registry`, `ingestion-policy`, `connector-base-spec`, and
`dashboard-shell` already define parts of the underlying system, but none
binds the final ingestion page composition. The new capability fills that gap
without rewriting unrelated connector specs.

### R3: Implementation Fitness

The lowest-churn path is to add a route and Dispatch foundation first, then
replace the Timeline, Connectors, and Filters surfaces independently. This
avoids cross-editing the same tab shell in every feature bead.

### R4: Verification Fitness

The previous process failed because visual parity was not a closure gate. The
new graph makes Playwright route checks, desktop/mobile screenshots, absence of
old card/tab shell, reconciliation, and report generation explicit blockers.

## Handoff

The next actionable bead is `bu-y25mj.2`: ratify the OpenSpec change. Delivery
should not start until that signoff is recorded. After ratification,
`beads-coordinator` can run the graph beginning with `bu-y25mj.1`.
