## Why

The dashboard's accessibility gate currently exercises real pages mainly as loading shells, excludes parameterized destinations from its route inventory, and cannot measure composited text contrast. The owner-approved Run 08 accessibility outcome remains open even though its earlier token-role and native-focus prerequisites have since landed.

## What Changes

- Propose a router-derived accessibility inventory for static and dynamic destinations. Loaded, degraded or empty, and consequential overlay states become explicit, non-private fixture obligations rather than being inferred from a loading-only axe pass.
- Require production-component axe and keyboard evidence for the inventory, including focus entry, operation, escape or dismissal, return focus, and perceivable results where applicable. QA's Butler menu, dynamic dossiers, Memory Search, and Ingestion surfaces are first-wave targets.
- Make loading-only and skipped cases visible, bounded debt with an exact route/state, reason, owner, review deadline, and replacement test. Neither a skip nor an axe pass with contrast disabled counts as complete accessibility evidence.
- Require text contrast to be measured after alpha compositing against actual supported surfaces in both themes. Semantic text uses opaque AA-safe roles unless a qualifying large-text use is explicitly measured and recorded; add a guard and migrate current alpha-muted uses without inventing a new visual role.
- Preserve the typed role matrix from merged PR #3716 and the native-control focus-visible floor from merged PR #4194. This proposal does not redo either change or waive the Dispatch WCAG floor.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `dashboard-design-language`: add route-state accessibility evidence, bounded coverage-debt governance, keyboard operation, and composited contrast requirements without replacing existing requirement bodies.

## Impact

The future route/keyboard leaf will touch the shell capability/router projection, `frontend/src/test/axe/`, focused page tests, and only the production controls that those tests reveal as deficient. The separate contrast leaf will touch `frontend/src/index.css`, semantic text consumers, the contrast math/tests, and the frontend lint or guard seam. This PR changes only proposed OpenSpec artifacts: no canonical spec, UI, runtime, private fixture, browser session, or deployment changes.

The original P1 `bu-6jv4m.12` remains assigned to tze. After independent review the exact owner choice is **A - adopt** this proposed contract, including the 30-day exact debt review bound, or **B - hold** it and explicitly retain the narrower current coverage without claiming route-state or composited-AA completion. The owner must separately release or reassign the original before any implementation dispatch. Silence chooses neither; adoption does not authorize merge, live/private-data exercise, or operational action.

Tests: +0 ~0 -0.
