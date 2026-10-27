## Context

This is a proposed, not adopted, extension of the Dispatch design language. At the base commit `99a5f3083865b19c87664f915fe1cd53cf351696`, `frontend/src/test/axe/route-page-cases.tsx` names 30 real static pages, `route-sweep.a11y.test.tsx` records four independently covered paths, and `skip-manifest.ts` holds two skips. `route-pages.a11y.test.tsx` deliberately leaves fetch pending and disables axe's color-contrast rule. `ALL_ROUTES` is a static projection of `SHELL_CAPABILITIES`; `router-config.tsx` also declares 16 parameterized paths. A static-only passing sweep therefore does not prove loaded or dynamic accessibility.

The accepted Dispatch spec already fixes WCAG contrast and the 2px `--focus` outline. Merged PR #3716 owns typed visual roles. Merged PR #4194 added the important native-control focus floor and its emitted-CSS regression; `MemorySearch`'s local `focus:outline-none` cannot erase that floor. Neither is new implementation scope here. `contrast.test.ts` checks opaque token pairs, not rendered alpha composition. On current `--mfg` and page `--bg` literals, 70% muted text is approximately 3.36:1 in light and 4.24:1 in dark; `IngestionFiltersPage` uses that class on ordinary small labels. This is a source-derived measurement, not a private-data or live-browser probe.

## Goals / Non-Goals

**Goals:** Make the accessibility claim exhaustive across the actual navigable route set, honest about unfinished states, and measurable for keyboard and composited contrast while preserving Dispatch's current role/focus vocabulary.

**Non-goals:** No new navigation or page design, no screenshot or live private-data inspection in this spec PR, no WCAG waiver, no token-role rewrite, no second focus system, no canonical adoption, and no implementation release of tze-owned `bu-6jv4m.12`.

## Decisions

### D1: Derive the inventory above the static command projection

The implementation leaf derives navigable destinations from `SHELL_CAPABILITIES` and checks parity with `router-config.tsx`, including parameterized paths and redirect aliases. `ALL_ROUTES` remains the valid static command/menu projection, but cannot be the accessibility denominator because it filters `dynamic`. The scenario identity is `(route pattern, rendered state, consequential interaction)`; query variants are separate only when they change an accessibility contract. Redirect aliases point to a canonical rendered scenario rather than duplicating it. A structural gate fails a new destination or interaction with no exact scenario or bounded debt entry.

For a source-qualified example, `/entities/:entityId` is declared as `dynamic: "search-backed"` in `frontend/src/lib/shell-capability.ts` and mounted in `frontend/src/router-config.tsx`; it is absent from `ALL_ROUTES` by design. Its fixture uses a synthetic entity ID and mocked hook results for the real `EntityDetailPage` loaded, degraded/empty, and consequential drawer states. A separate existing `BeadDetailPage.a11y.test.tsx` illustrates useful dedicated coverage but does not make the rest of the dynamic inventory complete.

Alternative rejected: manually extending `ROUTE_AXE_CASES` alone. It has no enforceable relationship to dynamic routes or failure/overlay states and would repeat the loading-shell false-completion problem.

### D2: Exercise real components with synthetic, non-private state

The route/keyboard leaf mocks data-hook or API boundaries, not the page component. It uses synthetic IDs and synthetic values for parameterized routes, with representative loaded, degraded, empty, and consequential overlay fixtures. It runs axe against those states and reusable keyboard choreography against the actual interactive control: trigger focus, open, traverse/activate, Escape where allowed, focus return, and perceivable completion/failure. First-wave cases are QA's Butler menu, Memory Search, Ingestion filters/timeline, and dynamic Entity/Bead/QA dossiers. Existing dedicated Butler/Timeline/Decisions/Bead axe tests are reused rather than cloned. A route's loading shell remains a useful separate smoke case, not its completion receipt.

Alternative rejected: end-to-end tests against owner data or a fixture page standing in for a production page. Neither demonstrates the intended boundary safely.

### D3: Debt is an explicit ratchet, not a pass

The initial inventory must classify the current two static skip entries and every uncovered dynamic or route-state case. Each temporary debt record has an exact scenario identity, reason, owner, replacement test, and review deadline within 30 days. A code-reviewable change may renew a still-needed entry with new evidence, but time alone does not renew it; expiration fails the gate. Wildcards and one entry covering an entire page family are refused. This proposed 30-day bound is an owner adoption choice, not silently effective policy. Required flows and WCAG floor violations cannot be declared passing by debt metadata.

Thirty days is a proposed review bound, not a claim about WCAG or the owner's existing policy: the two current static skips have remained in the manifest since at least its July 18 coverage commit, and their string-only reasons provide no revisit trigger. One short, explicit window gives an independently owned fixture leaf time to replace a skip while preventing another indefinite loading-only label. The owner may hold this proposal instead of accepting that operational debt policy; the current gate then remains narrower and must be described honestly.

Alternative rejected: a string-only skip list with a minimum reason length. It is honest about missing static routes but does not constrain age, scope, dynamic routes, or what remains untested.

### D4: Keep visual roles; measure the rendered pair

The contrast leaf uses the existing `contrast.ts` conversion as a unit-math seam, then measures actual computed foreground/background combinations in a deterministic, synthetic both-theme browser fixture for alpha and nested surfaces. A source guard rejects new semantic text opacity without an exact measured large-text exception. It migrates current small-text alpha uses to opaque AA-safe roles; it does not change Butler identity/category/chart token ownership. The large-text exception must show qualifying rendered size and weight and 3:1 in both themes. Disabled controls retain the existing Dispatch opacity rule and are classified separately; the attention-row alpha background tint is also unaffected, but any text on it is still measured against the composited surface.

Alternative rejected: globally tuning `--mfg` until every alpha use happens to pass. That would distort the settled role and still miss a nested background or a later opacity modifier.

## Implementation Leaves After Separate Owner Adoption

| Leaf | Primary owned paths | Evidence and rollback boundary |
| --- | --- | --- |
| Route/state inventory and keyboard | `frontend/src/lib/shell-capability.ts`, `frontend/src/router-config.tsx` only if needed for parity; `frontend/src/test/axe/*`; nearest existing page/component a11y tests and QA/Memory/Ingestion/dossier components only where a real failure is proven | Structural manifest parity, no silent static/dynamic/redirect gap, real loaded/degraded/empty/overlay axe and keyboard tests, synthetic fixtures; rollback reverts the new gate and any local UI fixes together without claiming coverage remains complete. |
| Composited contrast and semantic opacity | `frontend/src/index.css`, `frontend/src/lib/contrast*`, frontend lint/guard configuration, and current alpha-muted text consumers | Both-theme computed pairs and source guard, below-floor fixtures red-to-green, role-matrix parity, current Ingestion text pass; rollback must not claim AA for unmeasured alpha text. |

The coordinator may subdivide the route leaf by non-overlapping route families after the owner releases `bu-6jv4m.12`; this proposal does not file or assign those Beads. Every implementation PR states its net `Tests: +a ~b -c`, uses the test planner, targeted production-component tests, frontend lint/knip/build/test, guards, independent review, and exact-head hosted CI.

## Risks / Trade-offs

- **[Risk] Scenario explosion across routes and states** -> Reuse fixture builders and one gate species per behavior; require each applicable state, not arbitrary cross-products of unrelated widgets. Record truly unavailable states as exact debt, never pretend all permutations were tested.
- **[Risk] jsdom reports no contrast violation** -> Keep axe for semantics and keyboard; use separate actual-style both-theme measurement for color. A disabled axe contrast rule is not a passing contrast receipt.
- **[Risk] New guard conflicts with existing disabled or attention tint rules** -> Scope the text-opacity ban to informational/operable semantic text, retain the existing disabled-control and background-tint clauses, and test text over a tinted surface separately.
- **[Risk] Another active design-language delta archives later** -> Add only new requirements here, with IDs after the existing active `001` and `002`; do not restate `Type System`, `Voice Surface`, `Interaction Affordances`, or `Viewport and Modality Contract`.

## Migration Plan

This spec PR only proposes the contract. After independent review, the owner chooses adoption or explicit hold and separately releases the original tze assignment before implementation. The route inventory and debt ratchet land with its first set of fixtures so a new gate does not silently label old missing states complete. The contrast guard lands with migration of existing below-floor semantic text and both-theme receipts. Neither leaf needs API, database, provider, credential, or deployment changes. No runtime rollback occurs in this proposal; an implementation rollback must state which accessibility claims are again unproven.
