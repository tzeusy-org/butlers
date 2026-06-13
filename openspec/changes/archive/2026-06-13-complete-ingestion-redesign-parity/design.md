## Context

The ingestion redesign has two kinds of source material:

1. A broad archived OpenSpec change, `redesign-ingestion-dispatch-console`, that
   captured API, route, and policy work but archived without visual parity.
2. A concrete prototype bundle under `pr/overview/ingestion-redesign/` whose
   handoff says the real page should be Dispatch-language, route-based, and
   bespoke rather than card-based.

The live implementation currently follows neither the prototype's visual
system nor its information model closely enough. It still exposes the old
`Ingestion Events` card/table and a page-level tab switcher. The completion
plan therefore has to be treated as a page-level product gap, not a cosmetic
cleanup.

## Decisions

### D1: Create a new completion change instead of reopening the archive

The archived change is useful history but it has already mixed backend, policy,
and UI concerns. Reopening it would blur the root problem: the missing artifact
is a binding visual and operational acceptance contract for the actual route.

This change is intentionally narrower. It defines what must be visible, how it
must behave, and what verification proves it landed.

### D2: Treat the prototype handoff as binding implementation input

The prototype is not a vague mock. It names the routes, components, data
shapes, typography, interaction rules, and non-negotiable visual constraints.
Implementation beads may adapt code structure to the repository, but they must
preserve the visible behavior unless a spec amendment records a deliberate
tradeoff.

### D3: Visual parity requires live verification, not checklist closure

The previous process allowed all beads to close while the live route still
looked materially different. This change makes screenshot and route smoke
evidence part of the closure path:

- desktop and mobile live screenshots;
- prototype-reference screenshots or a documented prototype capture fallback;
- explicit assertions that primary ingestion surfaces do not render the old
  card/table/tab shell;
- a final reconciliation report attached to the epic.

### D4: Backend scope is driven by visible inspection needs

The design depends on real ingestion data: raw payloads, replay history,
connector health/auth state, pipeline counts, priority contacts, and channel
defaults. API work is in scope when a visible surface would otherwise be fake,
empty forever, or unable to explain the system.

API work is out of scope when it is a speculative generalization not needed by
the visible redesign. For example, OAuth scope drift belongs to
`add-connector-oauth-scope-surface`, not this change, except that this UI must
consume that capability when available.

### D5: The redesigned surface replaces, rather than wraps, the old tab page

The old `?tab=timeline|connectors|filters|history` surface can remain as a
redirect compatibility path, but it is not the architecture of the redesigned
page. The redesigned routes are:

- `/ingestion` for Timeline;
- `/ingestion/connectors` for the connector roster;
- `/ingestion/connectors/:connectorType/:endpointIdentity` for connector detail;
- `/ingestion/filters` for the pipeline.

History becomes a saved view or range in Timeline. It is not a fourth tab.

## Reconciliation Summary

### R1: Doctrine alignment

The redesign aligns with the dashboard doctrine because ingestion is a
read-mostly observability surface. The ledger, roster, connector detail, and
pipeline all help the owner detect, diagnose, and act on signal flow. A
marketing-style hero or generic admin table would be misaligned.

### R2: Spec and archive alignment

The archived ingestion change contains useful API and route ideas, but it is
not sufficient as a completion contract because its tasks could close without
the prototype visual system. This change adds the missing completion criteria.

### R3: Implementation fitness

The current frontend has reusable domain logic, but the visible surfaces are
still card-heavy and tab-based. The lowest-churn route is to add ingestion
Dispatch components under `frontend/src/components/ingestion/` and then retire
the old page-level tab shell from the routed experience.

### R4: Verification fitness

Unit tests alone cannot prove this work. The completion path requires
Playwright route smoke checks and screenshot artifacts, plus a textual
reconciliation report that maps prototype obligations to live evidence.

## Risks

- The prototype file may not render reliably in headless automation. If so, the
  final verification bead must document the capture method and use stable
  reference screenshots derived from the prototype bundle.
- `add-connector-oauth-scope-surface` is still an active spec-only change.
  Connector detail should render the reauth/scope surfaces opportunistically
  and show explicit unsupported/unavailable states until the spec lands.
- Replacing the ingestion tab shell may touch router, hooks, tests, and
  multiple component families. The Beads graph serializes the shared foundation
  before splitting Timeline, Connectors, and Filters work.
