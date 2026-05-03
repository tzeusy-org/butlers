# Dashboard Shell — Delta (page-primitive-spec-sync)

This delta replaces the stale "Page Header with Breadcrumbs" requirement that
was written before the `<Page>` primitive shipped (Vertical A, bu-yo4bt.3). The
auto-generated breadcrumb path and `PageHeader.title` H1 slot described in the
original requirement are no longer normative behavior; they have been superseded
by the `<Page>` primitive contract documented in
`about/lay-and-land/frontend.md`.

## MODIFIED Requirements

### Requirement: Page Header

The `PageHeader` component SHALL render inside the shell's header bar and
provide the breadcrumbs strip (populated by the active page), a command palette
trigger, and a theme toggle. `PageHeader` is shell chrome only — it does not own
page titles or generate breadcrumbs from the URL.

#### Scenario: Breadcrumbs strip

- **WHEN** the active page supplies breadcrumbs via `<Page breadcrumbs=...>`
- **THEN** `PageHeader` renders the supplied breadcrumb trail using the shared
  `<Breadcrumbs>` component (`components/ui/breadcrumbs.tsx`)
- **AND** the breadcrumbs strip appears in the shell header bar, above the
  page's `<h1>`
- **AND** crumbs are separated by `/` characters
- **AND** the final crumb (current page) renders as plain text without a link
- **WHEN** the active page does not supply breadcrumbs (un-migrated pages)
- **THEN** `PageHeader` renders an empty or minimal breadcrumb strip; no
  auto-generation from the URL pathname is performed as a normative behavior

#### Scenario: Page title ownership

- **WHEN** any page within the application renders its primary heading
- **THEN** the `<h1>` is rendered by `<Page>`, not by `PageHeader`
- **AND** the `<h1>` uses `text-2xl font-bold tracking-tight` — this is the
  canonical operator-tool H1 size for all pages, settled by the detail-page
  audit and confirmed in `about/heart-and-soul/design-language.md`
  non-negotiable #2
- **AND** `PageHeader` does NOT render an `<h1>` or accept a `title` prop as
  a live contract; the `PageHeader.title` slot is removed from the normative
  interface

#### Scenario: Header action buttons

- **WHEN** the page header renders
- **THEN** a search icon button (magnifying glass) appears on the right side,
  triggering the command palette on click
- **AND** a theme toggle button appears next to the search button
- **AND** both buttons use the `ghost` variant at `sm` size with 32x32px
  dimensions

## REMOVED Scenarios

The following scenarios from the original "Page Header with Breadcrumbs"
requirement are superseded and removed:

- **"Auto-generated breadcrumbs"** — `PageHeader` SHALL NOT auto-generate
  breadcrumbs from the URL pathname as a normative path. That behavior was a
  placeholder prior to `<Page>` adoption and is now a legacy fallback only.
  Normative breadcrumb generation is the responsibility of individual pages
  via `<Page breadcrumbs=...>`.

- **"Custom breadcrumbs and title"** — The `title` prop on `PageHeader` and its
  `text-lg font-semibold` `<h1>` rendering are removed from the contract.
  Per-page titles are owned by `<Page title=...>` with `text-2xl font-bold
  tracking-tight`. `PageHeader` is chrome, not a title host.

## Source References

- Non-Negotiable Rule 2: "The `Page` is a primitive." (`about/heart-and-soul/design-language.md`)
- `about/lay-and-land/frontend.md` — `<Page>` Primitive Contract section
  (implementation reference for H1 size, breadcrumb ownership, and PageHeader
  scope)
- Epic bu-yo4bt (closed) — Vertical A: `<Page>` primitive shipped via PR #1348
  (bu-yo4bt.3)
