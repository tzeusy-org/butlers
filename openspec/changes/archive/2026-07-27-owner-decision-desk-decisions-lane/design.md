## Context

The dashboard did not expose the owner decision queue even though
`butlers.jobs.decision_review.compute_decision_digest()` already assembled a
read-only digest from the exported Beads snapshot. The change adds a small
Decisions lane without giving the dashboard direct tracker access or authority
to alter a decision.

The lane is deliberately an API reader and navigation surface. The digest is
the existing source of truth for eligibility, oldest-first ordering, and
escalation. Its degraded-envelope state is significant: an unavailable export
must not be presented as an empty queue.

## Goals / Non-Goals

**Goals:**

- Expose the existing open, decision-labelled digest through a read-only
  `/api/decisions` endpoint and `/decisions` page.
- Preserve the distinction between a readable zero-decision export and a
  missing, stale, or unreadable export.
- Provide an owner-facing list with the established keyboard-triage pattern
  and an inline read-only summary.
- Make the lane reachable from the Main sidebar section with a positive-count
  badge.

**Non-Goals:**

- Live Dolt or `bd` access from the dashboard process.
- Decision mutation, default application, approval, close, or Telegram
  controls.
- Reimplementing label classification, escalation, or export parsing in the
  dashboard API.
- Per-decision structured options, defaults, or deadlines. Those require a
  separately scoped successor rather than an implicit expansion of this lane.

## Decisions

### Reuse the digest through a thin read-only endpoint

`GET /api/decisions` delegates to `compute_decision_digest()` and projects its
result into the established `ApiResponse` envelope. This keeps one classifier,
one escalation calculation, and one exported-JSONL read path.

Direct Dolt access and a new dashboard-side JSONL parser were rejected. Both
would duplicate tracker semantics, broaden the dashboard trust boundary, and
make failure behaviour diverge from the weekly decision-review path.

### Model source availability separately from the decision list

The endpoint carries `meta.decisions_available`, an unavailable reason, and an
export timestamp. A readable export with no eligible records returns an empty
list with `decisions_available: true`; a missing, stale, or unreadable export
returns an empty compatibility list with `decisions_available: false`.

This preserves a stable list shape while preventing the page verdict and list
empty state from treating compatibility `[]` as an all-clear. Returning a
transport error instead was rejected because the dashboard can still describe
the source failure and any known export age in its normal operator surface.

### Give Decisions an isolated routed lane

The owner queue is a dedicated `/decisions` page rather than a second
keyboard-triaged list on Overview. Each `useListTriage` registration listens
for j/k at page scope, so adding another independent list to Overview would
move both selections. The dedicated route gives the queue one keyboard scope,
one URL, and a direct navigation target.

The page reuses the verdict opener, rule-separated rows, and footer-hint
patterns already used by adjacent dashboard lanes. It shows only fields the
digest already exposes. A selected row opens an informational detail, not an
action panel.

### Keep the original sidebar badge count-only

The original lane registration maps the digest to a positive numeric badge.
Its count-only contract intentionally treats an unavailable digest as no
count, while the page itself names source degradation. A later navigation
availability affordance, if needed, must be a separately specified successor
instead of changing this completed carrier's scope retroactively.

## Risks / Trade-offs

- [A compatibility empty list can look calm] -> The page gates its verdict and
  list empty state on `decisions_available` and renders a named degraded note.
- [The export can age while remaining readable] -> The API exposes
  `export_as_of`, and the page keeps an as-of plaque visible even when the
  source becomes unavailable.
- [A dashboard reader could grow into a tracker client] -> The API remains a
  thin wrapper with no `bd`, Dolt, linter, or mutation call path.
- [Keyboard shortcuts could conflict with another list] -> The route contains
  a single triaged decision list rather than embedding it in Overview.

## Migration Plan

1. Register the read-only router and mount the existing exported snapshot into
   the dashboard API runtime.
2. Add the typed frontend client, verdict opener, page, and sidebar route.
3. Exercise available, empty, degraded, and escalated digest states with
   focused tests.
4. Deploy with no database migration and roll back by removing the route and
   reader; the existing digest and Beads export remain unchanged.

## Open Questions

None for this completed carrier. Structured decision context and richer
availability presentation are intentionally successor work.
