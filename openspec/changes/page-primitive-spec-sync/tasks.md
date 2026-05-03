## 1. Spec Delta

- [x] 1.1 Write `specs/dashboard-shell/spec.md` delta replacing the stale
      "Page Header with Breadcrumbs" requirement with normative `<Page>`-aligned
      statements.
- [x] 1.2 Confirm the delta covers all three stale items:
      - H1 size is `text-3xl font-bold tracking-tight` (as shipped in `page.tsx`)
      - Breadcrumbs are owned by individual pages via `<Page breadcrumbs=...>`
      - `PageHeader` scope is breadcrumbs strip + command palette + theme toggle only
- [x] 1.3 Confirm `PageHeader.title` and `buildBreadcrumbs()` are marked as
      removed/superseded in the delta (not silently dropped).

## 2. Validation

- [x] 2.1 Run `openspec validate page-primitive-spec-sync` and confirm pass.

## 3. Sync (owner step — do NOT run)

- [ ] 3.1 Owner runs `openspec archive page-primitive-spec-sync` after PR merges
      to promote the delta into `openspec/specs/dashboard-shell/spec.md`.
