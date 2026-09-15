## Why

Butlers' highest-stakes entries — approval decisions, secrets re-authorization — are minted as
deep links into Telegram and are overwhelmingly opened on a phone, yet
`openspec/specs/dashboard-design-language/spec.md` has zero viewport, touch, or pointer-modality
content: no device bands, no minimum touch-target size, no rule against hover-only facts. The
implementation mirrors the gap — the codebase has zero coarse-pointer/safe-area/dvh occurrences
and CI runs a single desktop Playwright project — because there is no binding spec text to hold
either to.

This is slice 1 of a 4-slice plan (Jarvis pursuit run 11, ranked move 13). Slices 2-4 —
`useClientLink()` + the `LiveIndicator` client-vs-fleet honesty fix, the `Page` archetype's gutter
ramp with `dvh`/safe-area, and the phone entry-route registry with a phone Playwright CI project —
are separate, later beads and are explicitly out of scope here. This change lands only the spec
amendment: the three canonical device bands, the 44px coarse-pointer touch-target floor, and the
rule that no fact essential to understanding a surface may be conveyed only via hover.

## What Changes

- Add a new `Viewport and Modality Contract` requirement to `dashboard-design-language`:
  - Three canonical device bands — desktop (≥1024px), tablet (768–1023px), phone (<768px) —
    aligned to the `lg`/`md` Tailwind breakpoints already in use across `frontend/src` (140/115/57
    `sm`/`lg`/`md` usages respectively), so pages and components branch on these bands rather than
    ad hoc breakpoints.
  - A 44×44px minimum hit area for every interactive target on any surface reachable under
    `(pointer: coarse)` or the phone band, achieved via padding/hit-area expansion rather than
    inflating the visual glyph.
  - A no-hover-only-facts rule: any fact needed to understand a surface's current state that is
    exposed via `:hover` on desktop (tooltip-only labels, hover-reveal deltas, hover-only
    truncation) must have a non-hover-dependent path to the same fact on coarse-pointer/phone.

No application code changes in this slice — the design amendment is the deliverable. No PWA/offline
shell, no gesture/Revealable lane, no ESLint viewport guard (all explicitly out of scope for the
whole run, per the source dossier).

## Impact

- Affected spec: `dashboard-design-language`
- Affected code: none in this slice. `useClientLink()`, the `LiveIndicator` fix, the `Page` gutter
  ramp, and the phone entry-route registry/CI project land in later beads (bu-8cdl1's S2-S4) against
  the contract this change establishes.
