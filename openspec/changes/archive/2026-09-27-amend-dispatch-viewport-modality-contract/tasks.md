# Tasks

## 1. Spec

- [x] 1.1 Add `Viewport and Modality Contract` requirement to
      `specs/dashboard-design-language/spec.md`: three device bands (desktop
      ≥1024px, tablet 768–1023px, phone <768px).
- [x] 1.2 Same requirement: 44×44px minimum touch-target floor under
      `(pointer: coarse)` or the phone band.
- [x] 1.3 Same requirement: no-hover-only-facts rule with a non-hover-dependent
      path required on coarse-pointer/phone.
- [x] 1.4 `openspec validate amend-dispatch-viewport-modality-contract --strict`.

## 2. Follow-on (separate beads, out of scope here)

- [x] 2.1 (bu-8cdl1 S2) `useClientLink()` three-state hook + `LiveIndicator`
      client-vs-fleet honesty fix.
- [x] 2.2 (bu-8cdl1 S3) `Page` archetype gutter ramp with `dvh` + safe-area,
      keyed to the device bands this change establishes.
- [x] 2.3 Deferred: Phone entry-route registry + 375px Playwright CI project (tracked in bu-60pwv6.19)
