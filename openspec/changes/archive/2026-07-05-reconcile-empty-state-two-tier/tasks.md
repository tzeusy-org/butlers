# Tasks

## 1. Spec

- [x] 1.1 Amend `Interface Copy` requirement in
      `specs/dashboard-design-language/spec.md` to the two-tier empty-state
      model (page-level vs. Voice-surface-inline), matching
      `about/heart-and-soul/design-language.md` § Empty states.
- [x] 1.2 Amend `Page Conformance` criterion (8) to the same two-tier model.
- [x] 1.3 `openspec validate reconcile-empty-state-two-tier --strict`.

## 2. Implementation (bu-eyo56, landed in the same PR)

- [x] 2.1 Add `variant?: "page" | "voice"` to `EmptyState`
      (`frontend/src/components/ui/empty-state.tsx`), default `"page"`.
- [x] 2.2 Add sibling `ErrorState` component
      (`frontend/src/components/ui/error-state.tsx`) for call sites that were
      using `EmptyState` to render a genuine fetch failure.
- [x] 2.3 Reclassify all ~44 `EmptyState` call sites: page-level empty states
      get `variant="page"` (the default; also named explicitly), Voice-surface
      call sites would get `variant="voice"` (audit found zero current
      consumers of the shared component are actually Voice-surface-inline —
      those already use bespoke `<Voice variant="italic">` markup directly).
- [x] 2.4 Migrate the discovered error-flavored call sites (ConcentrationPage,
      IssuesPanel, TimelineLedger, AuditLogTable, EntitiesIndexPage) to
      `ErrorState` (`role="alert"`, destructive color) instead of `EmptyState`.
