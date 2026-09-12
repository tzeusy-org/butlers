## Context

`DashboardPage` currently normalizes a failed `useSpendSummary("today")` response to
`null` and a failed `useTopSessions()` response to `[]`. The downstream components then
use their truthful calm branches: `CostWidget` formats the summary fallback as `$0.00`, and
`TopSessionsTable` renders its successful-empty message. A recently added `source_error`
branch correctly handles successful compatibility envelopes, but it cannot see a direct
query failure after the page has discarded that state.

The dashboard's degraded-envelope convention requires an unavailable source to remain
visible rather than becoming a fabricated calm result. The change remains inside the
Overview reader components and does not alter query, API, or Spend-page behavior.

## Goals / Non-Goals

**Goals:**

- Carry direct query failure explicitly from `DashboardPage` to each affected reader.
- Render a named, accessible unavailable state before any calm fallback branch.
- Preserve successful `source_error` rendering and valid zero or empty results.
- Add regression coverage at the page and component boundaries.

**Non-Goals:**

- Change dashboard API responses, hooks, query keys, retries, or cache policy.
- Change daily-trend or unpriced-model behavior.
- Refactor generic query boundaries or change the Spend page.

## Decisions

### Pass local unavailable props from the page

`DashboardPage` will pass an explicit unavailable boolean based on each direct query's
`isError` state. `CostWidget` and `TopSessionsTable` will use that boolean rather than
inferring an error from fallback data. This preserves the provenance of a direct failure.

Alternative considered: convert direct query failures into `source_error`. Rejected because
`source_error` describes a successful compatibility envelope, while a rejected request has
not produced such an envelope.

### Give unavailable precedence over calm data branches

Each reader will preserve its loading handling, then render direct unavailability before
the compatibility-degraded, unpriced, zero, or empty branches. This also suppresses a
stale or fallback value if the current query is in error.

Alternative considered: let the page continue passing `0` or `[]` and teach the existing
calm messages to hedge. Rejected because the page would still render a value as if it were
evidence.

### Reuse the existing degraded-note treatment

The new named unavailable states will use the existing `SourceDegradedNote` treatment. It
already names the failed reader, carries `role="alert"`, and preserves the dashboard's
established state vocabulary without adding a generic boundary or a new visual pattern.

Alternative considered: introduce a page-level query error boundary. Rejected because the
cost band is an independently readable section and the task excludes generic query-boundary
work.

## Risks / Trade-offs

- [A retained query result could be mistaken for current evidence] -> The unavailable prop
  takes precedence over all summary and table data branches.
- [Direct and compatibility failures could become visually indistinguishable] -> The two
  branches use separate labels and regression test IDs while retaining the common degraded
  visual vocabulary.
- [A successful zero or empty response could regress into a warning] -> Focused tests cover
  valid `$0.00` and the successful empty-session copy separately from direct failure.
