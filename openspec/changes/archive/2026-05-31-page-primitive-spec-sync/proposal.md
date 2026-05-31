## Why

The `dashboard-shell` spec's "Page Header with Breadcrumbs" requirement (lines
154–171) was written before the `<Page>` primitive shipped. It describes two
behaviors that the codebase no longer supports:

1. **Auto-generated breadcrumbs in `PageHeader`.** The spec says that when no
   explicit `breadcrumbs` prop is supplied, `PageHeader` auto-generates crumbs
   from the URL pathname. The shipped code retains this fallback, but any page
   that has adopted `<Page breadcrumbs=...>` suppresses the auto-builder via a
   `BreadcrumbsControlProvider` / `useBreadcrumbsControl`
   (`frontend/src/components/ui/breadcrumbs-control.tsx`). The normative path is
   now page-owned breadcrumbs; the URL-segment auto-builder is a legacy fallback
   for un-migrated pages only.

2. **`PageHeader.title` and per-page H1 size.** The spec says an explicit
   `title` prop renders an `<h1>` with `text-lg font-semibold`. That slot is
   dead code — `RootLayout` mounts `<PageHeader />` with no props. Per-page H1s
   are now owned by `<Page>` and rendered as `text-3xl font-bold tracking-tight`
   (as shipped in `frontend/src/components/ui/page.tsx`).

Leaving these stale requirements in the spec creates silent drift: a future
implementer reading the spec would build a `PageHeader.title` slot and an
auto-generated breadcrumb path as first-class features — both of which the
current design has intentionally rejected.

This change aligns the `dashboard-shell` spec with what Vertical A (bu-yo4bt)
actually shipped.

## What Changes

- **Modified capability**: `dashboard-shell` — the "Page Header with
  Breadcrumbs" requirement is updated to reflect the shipped `<Page>` primitive:
  - The H1 size is `text-3xl font-bold tracking-tight`, owned by `<Page>`, not
    by `PageHeader`.
  - Breadcrumbs are owned by individual pages via `<Page breadcrumbs=...>`.
    `<Page>` renders them inside `<main>` and signals `BreadcrumbsControlProvider`
    so `PageHeader` suppresses its URL-segment auto-builder. `PageHeader` retains
    the auto-builder as a legacy fallback for un-migrated pages only.
  - `PageHeader` scope is narrowed to: breadcrumbs strip + command palette
    trigger + theme toggle. The `title` prop and H1 rendering are removed from
    its contract.

## Capabilities

### Modified Capabilities

- `dashboard-shell`: "Page Header with Breadcrumbs" requirement updated.

### New Capabilities

None. This is a spec-alignment delta only. No new code is required.

## Impact

- **Delta spec**: `openspec/changes/page-primitive-spec-sync/specs/dashboard-shell/spec.md`
  — replaces the stale "Page Header with Breadcrumbs" requirement.
- **Implementation reference**: `about/lay-and-land/frontend.md` `<Page>`
  Primitive Contract section (bu-yo4bt.3, closed).
- **No code changes.** The implementation already shipped. This change updates
  the spec record to match it.
- **No database changes.**
- **No API changes.**

## Source References

- Non-Negotiable Rule 2: "The `Page` is a primitive." (`about/heart-and-soul/design-language.md`)
- `about/lay-and-land/frontend.md` — `<Page>` Primitive Contract section
- Epic bu-yo4bt (closed) — Vertical A: `<Page>` primitive and EntityDetailPage migration
