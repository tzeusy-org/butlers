# Detail Page Archetype

## Purpose

Defines the normative contract for every dashboard page that renders a single domain record. The `<Page archetype="detail">` shell (exported from `frontend/src/components/ui/page.tsx`) owns all chrome and state management — breadcrumbs, loading state, error state, not-found state, the `max-w-5xl` container, and the `<h1>` title row. Individual detail pages own body slots only.

This spec is the authoritative reference for all seven detail-page-archetype adopters: Fact, Rule, Episode, Contact, Butler, Entity, and Connector detail pages. Design source: `about/lay-and-land/detail-page-audit.md` §5 "Proposed `<DetailPage>` Contract".

---

## Requirements

### Requirement: Detail-page archetype shell

Every dashboard page that renders a single domain record SHALL use the `<Page archetype="detail">` shell exported from `frontend/src/components/ui/page.tsx`. Pages (one fact, one rule, one episode, one butler, one entity, one connector) own body slots only; the shell owns all chrome and state management. (Contacts are no longer a distinct detail page; they are rendered through the entity detail page.)

**Design source:** `about/lay-and-land/detail-page-audit.md` §5 "Proposed
`<DetailPage>` Contract" and §3.2 "The justifying lines". The shell is the
implementation of that contract as the `Page` component with `archetype="detail"`.

#### Scenario: Shell owns breadcrumbs

- **WHEN** a detail page renders
- **THEN** breadcrumbs MUST be passed via the `breadcrumbs` prop on `<Page>`
- **AND** the page MUST NOT render a standalone `<Breadcrumbs>` component at the
  page layer

#### Scenario: Shell owns loading state

- **WHEN** a detail page is fetching its primary record
- **THEN** the `loading` prop on `<Page>` MUST be set to `true`
- **AND** the page MUST NOT render an inline `<Skeleton>` block at the page layer
- **AND** the shell MUST display its `DetailSkeleton` (a card skeleton + two block
  skeletons, defined in `page.tsx`)

#### Scenario: Shell owns error state

- **WHEN** a detail page data fetch fails
- **THEN** the `error` prop on `<Page>` MUST be set to the error value
- **AND** the page MUST NOT render an inline `text-destructive text-center` block
  at the page layer
- **AND** the shell MUST display a destructive card with the error message

#### Scenario: Shell owns not-found state

- **WHEN** a detail page data fetch succeeds with a null / undefined record
- **THEN** the `empty` prop on `<Page>` MUST be set with an appropriate title and
  description
- **AND** the page MUST NOT render an inline "not found" message at the page layer

---

### Requirement: Detail-page six-tier body layout

The body of a detail page SHALL be organized into the following named tiers in render order. Tier 1 is owned by the `<Page>` shell and is supplied via props. Tiers 2–6 are passed as `children` inside `<Page archetype="detail">`. Not all tiers are required on every page; required tiers are marked.

**Tier 1 — Header-hero (required, owned by `<Page>`):**
Breadcrumbs, `<h1>` title, optional subtitle, status pills, and action buttons.
Consumers supply these via the `breadcrumbs`, `title`, `description`, `status`, and
`actions` props. The shell renders the layout; pages do not build this tier inline.

**Tier 2 — Hero (optional body slot):**
Alias chips, role chips, inline-editable fields, and any content that extends the
record's identity beyond what fits on the title row. Passed as body children before
the primary tier. Only EntityDetailPage currently uses this tier.

**Tier 3 — Primary (required body slot):**
The dominant read surface — the thing the operator opened the page for. For most
detail pages this is a `<Card>` with sections or a component tree. For ButlerDetailPage
the `<Tabs>` block is the primary slot.

**Tier 4 — Supporting (optional body slot):**
A side-by-side grid of secondary panels. Default layout: `grid gap-6 sm:grid-cols-2`.
Rendered below the primary tier.

**Tier 5 — Auxiliaries (optional body slot):**
A vertical stack of conditional sections. Each section MUST return `null` when its
data is empty; the page does not paper over empty auxiliaries with placeholder copy.

**Tier 6 — Drawer (optional body slot):**
A collapsed-by-default fold for settings, credentials, and advanced configuration.
Uses `<PracticalDrawer>` (currently defined inline in `EntityDetailPage.tsx`; target
extraction: `components/ui/practical-drawer.tsx`). Only shown when the page has
operator-mode content that does not belong at primary content level.

#### Scenario: Primary tier is present

- **WHEN** any detail page renders a resolved record
- **THEN** a primary body tier MUST be rendered inside the `<Page>` shell children
- **AND** the primary tier MUST be the first child after any hero tier content

#### Scenario: Auxiliaries hide when empty

- **WHEN** a page includes an auxiliary section and that section's data is empty
- **THEN** the section MUST return `null` (not render empty placeholder copy)
- **AND** the page layout MUST NOT show an empty-looking block where the section
  would be

#### Scenario: Drawer is collapsed by default

- **WHEN** a detail page includes a practical drawer tier
- **THEN** the drawer MUST be closed / collapsed on first render
- **AND** the operator MUST be able to expand it with a single interaction
- **AND** the drawer label MUST clearly communicate that it contains settings or
  credentials

---

### Requirement: Detail-page title is record-identity

The `title` prop passed to `<Page archetype="detail">` MUST be the record's own
identity, not the record's type.

- "Contact not found" is acceptable for an error/empty state title.
- "Alice Johnson" is the correct title for a contact record.
- "Contact" is NOT an acceptable title. "Contact Detail" is NOT an acceptable title.

For records without a natural name (rules, episodes, raw facts), the title MUST
be derived from the record's most identifying field — the content summary, the
session reference, the subject line — truncated to 80 characters if needed.

#### Scenario: Named record has identity title

- **WHEN** a user navigates to a detail page for a record with a natural name
  (e.g., a contact with `first_name="Alice"` and `last_name="Johnson"`)
- **THEN** the `<h1>` rendered by the `<Page>` shell MUST read "Alice Johnson"
- **AND** it MUST NOT read "Contact", "Contact Detail", or any type-of-thing string

#### Scenario: Content-only record derives title from content

- **WHEN** a user navigates to a Rule detail page for a rule with `content="Always
  acknowledge messages within 24 hours"`
- **THEN** the `<h1>` rendered by the `<Page>` shell MUST begin with "Always
  acknowledge messages within 24 hours" (truncated if longer than 80 characters)
- **AND** it MUST NOT read "Rule"

#### Scenario: Session-scoped record derives title from session reference

- **WHEN** a user navigates to an Episode detail page for an episode with
  `session_id="sess-abc123"`
- **THEN** the `<h1>` rendered by the `<Page>` shell MUST read "sess-abc123" or
  similar session-derived identifier
- **AND** it MUST NOT read "Episode"

---

### Requirement: Status pills on the title row

Meaningful status, type, or ownership indicators on a detail page SHALL be rendered adjacent to the title on the same row via the `status` prop on `<Page>`, not in a separate metadata card below the title.

**Implementation note:** `PageProps` in `frontend/src/components/ui/page.tsx` already
includes a `status?: React.ReactNode` slot, rendered inline with the `<h1>` in
`HeadingBlock`. The shell-side support for this requirement is shipped; an adopting page
needs only to pass the `status` prop.

The convention for ordering pills: ownership first (e.g., "Owner"), severity /
state second (e.g., "Established", "Fading"), tertiary chips last.

#### Scenario: Status pill adjacent to title

- **WHEN** a fact has `validity = "fading"`
- **THEN** a "Fading" badge MUST appear on the same row as the fact's title in the
  page header, not in a `<CardContent>` section below the title

---

### Requirement: Action buttons on the title row

Primary mutation actions on a detail page (edit, delete, trigger) SHALL be rendered to the right of the title via the `actions` prop on `<Page>`. Secondary and destructive actions follow in that order. Pages MUST NOT place their primary action inside `<CardContent>` when an `actions` slot is available.

#### Scenario: Primary action is visible without scrolling

- **WHEN** a detail page with an edit action loads
- **THEN** the edit button MUST be visible in the page header row without the user
  scrolling down
- **AND** it MUST be the first action rendered in the `actions` slot

## Source References

- Non-Negotiable Rule 2 (The Page is a primitive — `about/heart-and-soul/design-language.md`)
- Non-Negotiable Rule 3 (Information density is a deliberate dial)
- `about/lay-and-land/detail-page-audit.md` §3, §4, §5 — design source and contract basis
- `frontend/src/components/ui/page.tsx` — `<Page archetype="detail">` implementation
- `about/craft-and-care/engineering-bar.md` — Explicitness over hidden magic
