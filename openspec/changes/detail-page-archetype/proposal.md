## Why

The Butlers dashboard has seven detail pages that do the same job — render one record
— in seven different ways. `about/lay-and-land/detail-page-audit.md` scored all seven
against the engineering bar and named `EntityDetailPage` the winner. That page already
maps clearly to the four-tier density (hero → primary → supporting grid → collapsed
drawer) the design-language doctrine demands.

`frontend/src/components/ui/page.tsx` ships a `<Page archetype="detail">` shell that
owns breadcrumbs, loading states, error states, empty states, and the `max-w-5xl`
container. Three spec sections still describe their pages in the old model — hand-rolled
breadcrumbs, inline three-skeleton stacks, and the legacy `/butlers/relationship/contacts/:id`
route — without referencing the archetype. This change closes that documentation debt
before the remaining implementation PRs (#1392, #1393) merge.

## What Changes

- **New capability**: `detail-page-archetype` — defines the `<Page archetype="detail">`
  shell contract, the four-tier body slot layout (hero, primary, supporting, auxiliaries,
  drawer), and the canonical loading / error / empty patterns that replace hand-rolled
  skeletons.
- **Modified capability**: `dashboard-domain-pages` — Fact, Rule, and Episode detail pages
  (§Requirement sections at lines ~470, ~496, ~518) gain conformance requirements against
  the archetype: `<Page archetype="detail">`, named breadcrumbs via `breadcrumbs` prop,
  loading/error state ownership by the shell, and title lifted from `<CardTitle>` to the
  `title` prop (no more generic "Rule" / "Episode" page titles).
- **Modified capability**: `dashboard-relationship` — Contact detail page route is
  canonicalized. The spec at line 54 specifies `/butlers/relationship/contacts/:id`;
  the router registers `/contacts/:contactId`. The delta removes the stale route,
  installs `/contacts/:id` as the single normative route, adds a permanent redirect
  from the legacy path, and requires `<Page archetype="detail">` conformance.
- **Modified capability**: `dashboard-butler-management` — Butler detail page tabs
  become the archetype's `primary` slot. The breadcrumb requirement (currently
  "Overview > Butlers > {name}") is updated to require the `breadcrumbs` prop on
  `<Page>` rather than a standalone `<Breadcrumbs>` component.

## Capabilities

### New Capabilities

- `detail-page-archetype`: Normative definition of the four-tier detail page layout
  (header-hero, hero, primary, supporting, auxiliaries, practical drawer) as a
  reusable spec contract grounded in the `<Page archetype="detail">` shell and the
  `about/lay-and-land/detail-page-audit.md` design source.

### Modified Capabilities

- `dashboard-domain-pages`: Fact, Rule, and Episode detail pages conform to the
  detail-page archetype. Generic `<CardTitle>` page titles are replaced with
  record-identity titles. Loading and error states are owned by the `<Page>` shell.
- `dashboard-relationship`: Contact detail page route duplication resolved.
  Canonical route is `/contacts/:id`; `/butlers/relationship/contacts/:id` becomes
  a permanent redirect. Page conforms to the detail-page archetype.
- `dashboard-butler-management`: Butler detail page adopts `<Page archetype="detail">`
  breadcrumbs prop. Tabs body is documented as the `primary` slot in the archetype
  vocabulary.

## Impact

- **New spec**: `openspec/specs/detail-page-archetype/spec.md` created by this change.
- **Delta specs**: `dashboard-domain-pages`, `dashboard-relationship`,
  `dashboard-butler-management` — requirement-level additions/corrections only.
- **Frontend implementation beads**: bu-rqfil (parent epic). Detail page migrations
  are implemented in the corresponding implementation PRs.
- **No database changes. No new API endpoints.**
- **Router change**: `/butlers/relationship/contacts/:id` permanent redirect added
  (or confirmed present) alongside the canonical `/contacts/:id` route.

## Source References

- `about/lay-and-land/detail-page-audit.md` — design source; §5 "Proposed `<DetailPage>`
  Contract" and §3.2 "The justifying lines" define the four-tier body layout.
- `about/heart-and-soul/design-language.md` — Non-Negotiable Rule 2 ("The Page is a
  primitive"); Settled Direction #3 (information density is a deliberate dial).
- `about/craft-and-care/engineering-bar.md` — Explicitness over hidden magic; same-change
  documentation when behavior changes.
