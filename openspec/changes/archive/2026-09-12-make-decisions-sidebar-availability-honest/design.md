## Context

`GET /api/decisions` already distinguishes a readable zero-decision digest
from a missing, stale, unreadable, or otherwise unavailable export through
`meta.decisions_available`. The Decisions page consumes that truth, but the
sidebar's count-only hook currently collapses it to `0`. The result is a calm
no-badge treatment for both successful emptiness and source failure.

The change is limited to navigation state. It must not add tracker access,
route changes, API fields, or decision actions. The structured decision
context carrier remains separate and is not part of this design.

## Goals / Non-Goals

**Goals:**

- Preserve no badge for a readable empty digest.
- Preserve numeric badges for positive open-decision counts.
- Render one labelled unavailable marker when the digest declares itself
  unavailable or the query fails directly.
- Use the same truthful state in rail, expanded desktop, and mobile sidebar
  variants.

**Non-Goals:**

- Changes to `GET /api/decisions`, its models, the Beads export, or runtime
  mounts.
- Decisions page changes, including structured context, deep links, or list
  selection.
- Decision mutations, default application, action controls, or a live Dolt
  bridge.
- A generalized badge framework for other navigation items.

## Decisions

### Keep availability as a narrow Decisions-only discriminated state

`useDecisionsOpenBadge` will return a `DecisionsBadgeState` with either a
numeric-count variant or an unavailable variant. The existing QA and approval
badges remain plain numeric values. `useBadgeCounts` carries the union only so
the existing sidebar seam can receive the Decisions state without a new
provider, manager, or second query.

The count variant represents loading, compatibility data without `meta`, and
successful data. Its count is zero unless a readable digest has records. The
unavailable variant is reserved for `decisions_available === false` and the
query's direct error state. This preserves the established quiet loading and
empty behavior while making only affirmed source failure visible.

Returning `0` with a side-channel boolean was rejected because it leaves the
renderer able to forget the distinction again. Changing every sidebar badge to
a richer object was rejected because the other badge sources have no matching
availability contract and that would create the generic badge system this
change explicitly avoids.

### Reuse the existing status-dot vocabulary for the marker

The sidebar will render the unavailable variant through the existing `StateDot`
primitive in its degraded state, with the explicit accessible label
`Decisions digest unavailable`. This gives the indicator an established amber
meaning, a non-color accessible name, and a compact rail-compatible form.

Rail rendering places the marker on the Decisions glyph. Expanded desktop and
mobile use the same marker as the trailing navigation metadata. Positive
counts keep the existing numeric badge styling. A textual `0`, an icon-only
custom SVG, and a new colour token were rejected because they either repeat
the fabricated-calm failure, lack an accessible name, or create visual drift.

### Test the state boundary and all rendered variants

Focused hook tests will prove available-empty, positive-count,
`decisions_available: false`, and direct-error mapping. Sidebar tests will
assert no marker for available zero, numeric rendering for a positive count,
and the labelled unavailable marker in rail, expanded, and mobile variants.
The tests assert observable state rather than internal implementation details.

## Risks / Trade-offs

- [A direct query error has no export reason] -> The marker names the digest as
  unavailable without inventing a reason; the Decisions page remains the
  source for fuller error text.
- [A small dot can be missed visually] -> The marker uses the established
  degraded colour and a screen-reader label in every sidebar variant.
- [A union leaks into unrelated badges] -> Only the `decisions-open` entry
  creates or interprets `DecisionsBadgeState`; QA and approvals keep numbers.
- [Stale compatibility fixtures omit `meta`] -> The count variant remains
  quiet, preserving the existing sidebar safety behaviour for incomplete test
  fixtures and legacy responses.

## Migration Plan

1. Add focused failing hook and sidebar tests for the availability matrix.
2. Implement the narrow state mapping and marker rendering.
3. Run focused frontend tests, lint, typecheck/build, and the repository's
   final quality gates.
4. Roll back by reverting the frontend-only change; no persisted data or API
   contract changes need migration.

## Open Questions

None. The API already supplies the availability signal required for this
navigation-only correction.
