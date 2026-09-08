## Why

The `/secrets` Passport spine currently groups rows `needs-hand` → `stale` → `CLI runtimes` →
`System` → `User`: two state-first buckets bolted onto three family-first buckets. `bu-5r9hy`
surfaced the seam this produces — user rows carry a fixed `lastTouchOrder: 800` because nothing
ever tracked real per-credential usage time, so the "recency" sort mode they feed is a fixed
constant wearing a label that implies live activity data. The owner closed `bu-5r9hy` with a wider
ruling (`bu-5r9hy` close reason, 2026-09-02): "Owner selected the separately shaped state-first
universal Passport model. Narrow user-row index framing is superseded and routed to `bu-k87os`; no
frontend, telemetry, inventory, API, deployment, PR, or merge action authorized."

This draft is that separately shaped model: a spine whose five top-level groups are all states —
`Needs hand`, `In progress`, `Stale`, `Ready`, `Not set` — so family is never a group boundary and
the synthetic Recency control is retired rather than dressed up. It changes no code; `bu-k87os`
acceptance criterion 7 requires separate owner approval of this exact artifact before any handler,
component, or test file is touched.

## What Changes

- Amend the `butler-secrets` requirement `Passport-Book Information Architecture`: replace the
  family-first `needs-hand, stale, CLI runtimes, System, User` spine order with five state-first
  groups — `needs-hand`, `in-progress`, `stale`, `ready`, `not-set` — and map every current
  `CredentialState` value to exactly one of them.
- Reclassify `rotating` from `needs-hand` to `in-progress`: an in-flight rotation the owner
  initiated is a transient operation, not a broken credential demanding attention. Name
  `authorization_needed`'s existing (but previously unstated) `needs-hand` membership explicitly.
- Define a deterministic within-group ordering — severity rank, then a fixed `cli` / `system` /
  `user` family rank, then label, then focus key — so two rows are never left in an undefined
  relative order, and generalize the existing "omit an empty group's stub" rule from
  `needs-hand`/`stale` to all five groups.
- Add scenarios for mixed-family groups, transient-state rendering, search/keyboard traversal
  order, identity-switch re-projection within a group, and an accessible per-row family qualifier
  now that group headings no longer name a family.
- Add a new requirement, `Spine Sort Control And Recency Resolution`: the `?sort=` control narrows
  to `severity` (default) and `alpha`; `recency` is REMOVED — not renamed or reskinned — because its
  `lastTouchOrder` backing value was a fixed per-family constant, never a real last-used signal. A
  legacy `?sort=recency` URL falls back to `severity` without erroring or rewriting the URL.
- Define a content-blind, ephemeral real-inventory comparison method for verifying the group
  mapping and ordering against a live, non-degraded inventory response without persisting any
  value: it retains only `family`, `state`, the row's already-published label, and a per-run
  ephemeral correlation token.
- No new last-used/activity field, telemetry, or inventory persistence is introduced anywhere in
  this delta — the non-goal `bu-k87os` states explicitly.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `butler-secrets`: amend `Passport-Book Information Architecture` (spine grouping order,
  CredentialState-to-group mapping, within-group determinism, empty-group generalization, mixed-
  family/transient-state/search/keyboard/identity-switch/accessibility scenarios) and add
  `Spine Sort Control And Recency Resolution` (recency removal, legacy `?sort=` fallback,
  content-blind real-inventory comparison method).

## Impact

- Affected spec: `butler-secrets` (`openspec/specs/butler-secrets/spec.md`).
- Supersedes: `bu-5r9hy` (closed 2026-09-02, close reason routes its narrower user-row
  `lastTouchOrder` question to this bead).
- Future bounded implementation (blocked on owner approval of this exact artifact, not part of
  this change): `frontend/src/components/secrets/passport/spine-builder.ts` (`buildSpineEntries`,
  `lastTouchOrder` removal), `frontend/src/components/secrets/passport/constants.ts`
  (`NEEDS_HAND_STATES` / a new `IN_PROGRESS_STATES` set), `frontend/src/components/secrets/
  passport/types.ts` (`SpineSortMode` narrows to `"severity" | "alpha"`, `SpineEntry.lastTouchOrder`
  removed), `frontend/src/components/secrets/passport/Spine.tsx` (`SORTERS`, `SortPicker` options,
  group rendering), and `frontend/src/components/secrets/passport/DirectionPassport.tsx`
  (`?sort=` fallback for a removed mode).
- Out of scope: any handler, component, test, telemetry, migration, deployment, PR, or merge
  action — `bu-k87os` acceptance criterion 7 requires separate owner approval of this exact
  artifact first.
- Coexistence note: two other unarchived changes carry `## MODIFIED Requirements` blocks against
  this same `butler-secrets` spec file today —
  `openspec/changes/make-operator-status-readers-distinguish-unavailable` (requirement
  `One Row Template Across All Three Families`) and
  `openspec/changes/repair-secrets-authority-projections` (requirement
  `Connector Status Drives Spotify Passport State`). Neither touches
  `Passport-Book Information Architecture` or defines a `Spine Sort Control` requirement, so there
  is no same-requirement collision today; per `AGENTS.md`'s two-unarchived-changes-same-requirement
  hazard, re-run the `rg -l '^### Requirement: <Name>$'` grep before archiving any of the three.

**[owner-review-ready] This is a spec-only draft pending separate owner approval of the exact spec
artifact (`bu-k87os` acceptance criterion 7). No frontend, telemetry, inventory persistence, secret
access, API expansion, deployment, PR merge, or other implementation is included or authorized by
this change.**
