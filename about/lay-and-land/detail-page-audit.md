# Detail Page Audit: Picking the Canonical Drilldown Layout

> Status: **maintainability decision**. Inventories the seven (currently
> shipping) detail pages in the dashboard, scores them against Butlers'
> own engineering bar, names a winner, calls out its gaps, and proposes
> the contract for a future `<DetailPage>` shell.
>
> Source doctrine: [`about/heart-and-soul/design-language.md`](../heart-and-soul/design-language.md)
> ("the `Page` is a primitive; one token system or none; time is a typed
> primitive"). Topology context:
> [`frontend.md`](frontend.md) Page Archetype C, "Detail / drilldown".
> Engineering bar: [`about/craft-and-care/engineering-bar.md`](../craft-and-care/engineering-bar.md).

The seven pages do the same job (render one record) and they do it
seven different ways. This document picks one, justifies it, and lists
what the others have to absorb before they can be migrated to it.

---

## 1. The Seven Detail Pages

Each entry: which butler / domain it serves, its layout shape, what
its body composes, what actions it offers, whether it uses tabs.

### 1.1 `ButlerDetailPage.tsx` (502 lines)

`frontend/src/pages/ButlerDetailPage.tsx`

- **Header:** breadcrumbs (line 393), then a flex row with a bare
  `<h1 className="text-2xl ...">{name}</h1>` and a `<ChatPanel />`
  pinned right (lines 394–397). No description, no metadata strip, no
  status badge.
- **Body:** shadcn `Tabs` with eleven base tabs (overview, sessions,
  config, skills, schedules, state, trigger, mcp, crm, memory, models)
  plus dynamically-injected `health`, `routing-log`, `registry` tabs
  for specific butlers (lines 399–419). Each tab body is its own
  component under `components/butler-detail/`, eight of them
  `lazy()`-loaded with a per-tab `<TabFallback />` (lines 29–58, 107–113).
- **Actions:** `ChatPanel` (chat with this butler) is the only top-level
  action; everything else lives inside individual tabs.
- **Loading / error / empty states:** there is no top-level loading
  guard; each tab handles its own. Sub-components (e.g.
  `ButlerSessionsTab`) build their own pagination, drawer, and empty
  states inline (lines 119–193).
- **Tabs:** yes: central to the layout.

### 1.2 `ContactDetailPage.tsx` (44 lines)

`frontend/src/pages/ContactDetailPage.tsx`

- **Header:** breadcrumbs only (lines 19–24). The page itself owns
  *no* H1 or status; it delegates entirely to `ContactDetailView` in
  `components/relationship/ContactDetailView.tsx`.
- **Body:** `<ContactDetailView contact={contact} />` (line 41), a
  952-line component that renders one `Card` with the contact's full
  name as `CardTitle`, info rows, role badges, preferred-channel row,
  and the entity-link row.
- **Actions:** the inline edit button and the destructive delete sit
  inside `ContactDetailView`'s header `flex items-start justify-between`
  (lines 864–898 of the view).
- **Loading / error:** page-level, with the standard three-stack of
  `Skeleton` heights (lines 27–33) and a `text-destructive` failure
  block (lines 35–39).
- **Tabs:** no.

This is the **thinnest** page in the seven, and the one that does the
most by composition. Its only original sin is that the delegated view
has hex literals (`#7c3aed`, `#b45309`, `#0369a1`, plus an eight-color
hash palette) at lines 53–62 and 69–77 of `ContactDetailView.tsx`.

### 1.3 `EntityDetailPage.tsx` (2,240 lines)

`frontend/src/pages/EntityDetailPage.tsx`

- **Header:** breadcrumbs (lines 1929–1934), then an "identity hero"
  `<section>` containing an inline-editable H1, an entity-type
  `Select` styled to look like a pill, owner / unidentified badges, a
  promote button, and an aliases-and-roles row (lines 1953–2191).
- **Body:** ten regions stacked: `PulseStrip`, `ActivityTimeline`,
  Gifts/Loans grid, `MessageThreadsSection`, `LinkedContactsList`,
  `FactsSection`, `PracticalDrawer` (collapsed-by-default with
  `OwnerSetupBanner`, `LinkedContactSection`, `EntityInfoSection`,
  `TelegramSessionSetup`) (lines 2189–2235).
- **Actions:** inline edit on name / aliases / roles / type; a
  conditional `Mark confirmed` button on unidentified entities;
  delete-info, reveal-secret, and full Telegram-auth flows live in
  child components.
- **Loading / error:** page-level skeleton (3 blocks) + destructive
  text block (lines 1936–1948).
- **Tabs:** no: chosen over tabs deliberately. Sections are
  vertical and stateful, with `PracticalDrawer` providing the only
  fold.

The hex-literal smell flagged in the brief at "lines 313/316" no
longer exists in this file (the page has been refactored to use
Tailwind tokens; lines 312–316 are now `handleSubmit` logic). This
is now the most semantically clean of the heavyweights.

### 1.4 `EpisodeDetailPage.tsx` (147 lines)

`frontend/src/pages/EpisodeDetailPage.tsx`

- **Header:** breadcrumbs (lines 21–27). No H1. The "title" is
  literally `<CardTitle className="text-2xl">Episode</CardTitle>`:
  generic, not the record's own name.
- **Body:** one `Card` containing content, status row (Importance /
  Consolidated), details row (session id / refs / expires), metadata
  JSON, timestamps (lines 43–142).
- **Actions:** none. Read-only.
- **Loading / error:** page-level three-skeleton stack and
  destructive text (lines 29–41), **identical shape** to
  ContactDetailPage and FactDetailPage.
- **Tabs:** no.

### 1.5 `FactDetailPage.tsx` (285 lines)

`frontend/src/pages/FactDetailPage.tsx`

- **Header:** breadcrumbs (lines 78–84). No H1; the `CardTitle` at
  line 105 *does* render the record's identity (entity link / subject)
  with the predicate as a sub-line, closer to a real header than
  Episode's.
- **Body:** one `Card` with sections for Content, Status row
  (Confidence / Permanence / Validity / Scope), Metrics, Provenance,
  Tags, Metadata JSON, Timestamps.
- **Actions:** none (all links are navigation).
- **Loading / error:** identical three-skeleton + destructive text
  block.
- **Tabs:** no.

The flagged inline `style={{ width: \`${pct}%\` }}` is real and
load-bearing at line 64 (the confidence progress bar). This is
unavoidable in stock Tailwind without an arbitrary value or a
`<Progress>` primitive.

### 1.6 `RuleDetailPage.tsx` (257 lines)

`frontend/src/pages/RuleDetailPage.tsx`

- **Header:** breadcrumbs (lines 81–87). No H1; `CardTitle` reads
  literally `Rule` (line 107): same generic-title problem as Episode.
- **Body:** one `Card` with Content, Status row (Maturity / Scope /
  Permanence), Effectiveness row (with progress bar + applied /
  successes / harmful counts), Confidence row, Provenance, Tags,
  Metadata, Timestamps.
- **Actions:** none.
- **Loading / error:** identical three-skeleton + destructive text
  block.
- **Tabs:** no.

The flagged inline `style={{ width }}` at line 65 is real, same
progress-bar pattern as FactDetailPage. The `permanenceBadge` helper
(lines 37–54) is **duplicated verbatim** between this file and
`FactDetailPage.tsx` (lines 14–31). Two names, one shape, two code
paths, exactly the smell the interfaces-and-dependencies rule names.

### 1.7 `ConnectorDetailPage.tsx` (569 lines)

`frontend/src/pages/ConnectorDetailPage.tsx`

- **Header:** an explicit `<Button asChild>` "Back to Connectors"
  link (lines 172–179) **instead of** breadcrumbs, and an
  ad-hoc title block at lines 181–200 with `connector_type` as H1 and
  `endpoint_identity` as a font-mono sub-line. The only one of the
  seven without breadcrumbs.
- **Body:** five-plus stacked cards: Status (with embedded liveness
  badge, error banner, and a `<Table>` of metadata rows including
  inline-editable cursor), Lifetime Counters, Discretion Settings,
  optional `BatchSettingsCard`, Period Summary, `VolumeTrendChart`,
  `ConnectorRulesSection`, and an `AlertDialog` confirmation modal.
- **Actions:** inline cursor edit with confirmation dialog (lines
  280–319, 549–566); inline number edits for two discretion thresholds
  with per-input save buttons (lines 396–478); period selector lives
  inside the chart component.
- **Loading / error:** card-level skeletons (line 212), inline empty
  string for cursor (line 302), and a one-liner not-found message
  (line 322). Mixed.
- **Tabs:** no.

---

## 2. Scoring

Scale: 1 (worst) – 5 (best). Five axes, weighted equally.

| Page | Readable & explicit | Component reuse / no duplication | Token & primitive discipline | L/E/E states | Extensibility | **Total** |
|---|---|---|---|---|---|---|
| ButlerDetailPage | 3 | 3 | 4 | 2 | 4 | **16** |
| ContactDetailPage | 4 | 4 | 2 | 4 | 3 | **17** |
| EntityDetailPage | 2 | 4 | 4 | 4 | 5 | **19** |
| EpisodeDetailPage | 5 | 2 | 5 | 4 | 3 | **19** |
| FactDetailPage | 4 | 1 | 3 | 4 | 3 | **15** |
| RuleDetailPage | 4 | 1 | 3 | 4 | 3 | **15** |
| ConnectorDetailPage | 3 | 3 | 5 | 2 | 4 | **17** |

Comparison-only (not scored, but noted): **`QaInvestigationDetailPage`
(score-equivalent ~21)** invented something better than any of the
seven (see §3.5 for why it almost won).

### Axis-by-axis evidence

#### Readable & explicit

- **EpisodeDetailPage (5).** 147 lines. The control-flow is one
  ternary cascade: loading skeleton → error block → record card. Every
  rendered region is a literal block that maps 1:1 to a model field.
  Zero hidden state. Zero local helper components.
- **EntityDetailPage (2).** 2,240 lines. 23 internal components, 11
  `useState` calls in the top-level function alone (lines 1823–1856),
  three concurrent inline-edit modes (name, alias, role) each with
  their own draft state. *Excellent product*; *not readable*.
- **ButlerDetailPage (3).** 502 lines that split themselves cleanly
  into "tab dispatcher" + per-tab subcomponent. The top-level is
  legible; what's hidden is the divergent shape of the eleven tabs.

#### Component reuse / no duplication

- **FactDetailPage / RuleDetailPage (1 each).** `permanenceBadge`
  is duplicated verbatim (FactDetailPage lines 14–31, RuleDetailPage
  lines 37–54). The progress-bar inner JSX is duplicated verbatim
  (FactDetailPage lines 56–69, RuleDetailPage lines 56–72). The
  three-skeleton loading block, the destructive text block, the
  metadata `<pre>` block, and the timestamps row are all duplicated
  across both files and EpisodeDetailPage. **This is the worst
  cluster of "two names, two shapes, two code paths" in the
  detail-page family.**
- **ContactDetailPage (4).** Delegates to a single named view; no
  inline duplication of badge or layout helpers at the page layer.
- **EntityDetailPage (4).** Inside its 2,240-line file, sub-components
  are reused thoroughly. *Outside* the file, `SecuredInfoEntry` is
  near-identically replicated in `ContactDetailView.tsx` lines 84–144:
  but that is a contact-vs-entity duplication, not a detail-page-shape
  one.
- **EpisodeDetailPage (2).** Doesn't duplicate code itself, but it
  *is* one of the pages being duplicated by Fact and Rule.

#### Token & primitive discipline

- **EpisodeDetailPage (5).** Zero hex literals, zero inline
  `style={{...}}`, all colors via semantic tokens
  (`text-muted-foreground`, `bg-muted/30`, `bg-emerald-600`). The lone
  named-color violation is `bg-emerald-600 text-white` on the
  Consolidated badge (line 80): a Tailwind palette name, not a hex.
- **ConnectorDetailPage (5).** Likewise zero hex / zero inline style.
  All status color comes from a delegated `LivenessBadge` component
  and `bg-destructive/10`.
- **EntityDetailPage (4).** Zero hex literals in the page itself;
  the 2026-05-03 refactor cleaned up the `#7c3aed`/`#f59e0b`
  call-outs that the brief flagged. The page does still rely on
  `bg-emerald-600`-style Tailwind palette names in child components.
- **FactDetailPage / RuleDetailPage (3).** Real inline
  `style={{ width: \`${pct}%\` }}` for progress bars, the only
  inline-style violations in the seven. `bg-blue-600` /
  `bg-amber-500` / etc. live inside their badge helpers.
- **ContactDetailPage (2).** The delegated view has the worst leak:
  `ContactDetailView.tsx` lines 53–61 keep an eight-element hex
  array used as a hash-bucketed label palette, and lines 69–77 keep
  three more hex codes for role badges, applied via inline
  `style={{ backgroundColor: ... }}`. This is exactly the leak
  flagged in `frontend.md` topology §"Token leaks".
- **ButlerDetailPage (4).** No hex / inline style at the page layer.
  Children vary.

#### Loading / error / empty states

- **EpisodeDetailPage / FactDetailPage / RuleDetailPage /
  ContactDetailPage (4 each).** All four use the *same three-line
  skeleton stack* and the *same destructive-text-center error block*,
  verbatim. This is the most consistent pattern in the
  detail-page family, and any canonical shell should adopt it
  literally:

  ```tsx
  // Loading (Fact/Rule/Episode/Contact)
  <div className="space-y-4">
    <Skeleton className="h-8 w-64" />
    <Skeleton className="h-48 w-full" />
    <Skeleton className="h-64 w-full" />
  </div>

  // Error
  <div className="text-destructive py-12 text-center text-sm">
    Failed to load {noun}. {(error as Error).message}
  </div>
  ```

- **EntityDetailPage (4).** Same three-skeleton shape with one
  block sized differently (`h-24` instead of `h-48`).
- **ButlerDetailPage (2).** No top-level guard. Each lazy tab
  shows a different `TabFallback` and each sub-tab handles its own
  errors. The user can land on a 404 butler with no global feedback.
- **ConnectorDetailPage (2).** Mixed: the Status card has its own
  `<Skeleton className="h-32 w-full" />`; the Counters card silently
  vanishes if `connector?.counters` is missing; "not found" is a
  one-liner inside the Status card body (line 322) instead of a
  page-level state. Three behaviors for three regions of the same
  page.

#### Extensibility

- **EntityDetailPage (5).** Adding a new section is "drop a
  `<NewSection entityId={...} />` between two existing siblings, and
  optionally add it to the `PracticalDrawer`." The composition is
  flat and additive.
- **ButlerDetailPage (4).** Adding a tab is "add an entry to
  `BASE_TABS`, add a `TabsTrigger`, add a `TabsContent`, optionally
  lazy-load the body." Mechanical, slightly verbose.
- **ConnectorDetailPage (4).** Same additive-card pattern: drop a
  new `<Card>` in the stack.
- **EpisodeDetailPage / FactDetailPage / RuleDetailPage (3 each).**
  Single-card structure means a "new section" *inside* the record is a
  new sibling div in `CardContent`, fine for one or two more rows,
  but the card grows unwieldy fast and there is no boundary between
  "metadata" and "actions".
- **ContactDetailPage (3).** The page itself is trivially extensible
  (add a section to `ContactDetailView`). The delegated view, at 952
  lines, has already grown past the point where a new section is
  cheap.

---

## 3. The Winner: `EntityDetailPage.tsx`

Tied with `EpisodeDetailPage` on raw score (19 each), but the tiebreak
is **doctrine fit** plus **what the canonical needs to do**, not
"smallest file."

### 3.1 Why Entity over Episode

The doctrine in `design-language.md` says (lines 197–212):

> 2. **The `Page` is a primitive.** Every route renders inside a
>    `<Page>` shell that owns title, description, breadcrumbs, action
>    bar, loading state, error state, and empty state. Pages compose
>    sections inside it; they do not reinvent the chrome.
>
> 3. **Information density is a deliberate dial, not an accident.**
>    Each page declares its archetype.

EpisodeDetailPage scores well only because it is *trivial*. It has no
record-specific title (`<CardTitle>Episode</CardTitle>` line 48, the
title is the *type*, not the *record*), no mutation, no progressive
disclosure, no actions, no nested sections. Propagating that shape
would force every drilldown back to a single-card flatland. That
contradicts the "deliberate dial" doctrine.

EntityDetailPage is the only page in the seven that:

1. **Has a real record-identity hero.** The H1 is the entity's
   canonical name, inline-editable, with the type rendered as a
   pill (`Select` styled as a badge, lines 2012–2022) and identity
   badges (Owner / Unidentified) directly adjacent. This is what a
   "you are looking at one specific record" header is supposed to
   look like.
2. **Distinguishes hero, primary content, supporting panels, and
   practical drawer.** Lines 1952–2235 walk the three densities
   declaratively: identity hero → primary timeline → supporting grid →
   collapsed practical drawer. That maps onto the "information density
   is a dial" doctrine cleanly.
3. **Composes sections by name and dependency, not by inline JSX.**
   Each region (`PulseStrip`, `ActivityTimeline`, `GiftsPanel`,
   `LoansPanel`, `MessageThreadsSection`, `LinkedContactsList`,
   `FactsSection`, `PracticalDrawer`) is a named component that owns
   its own data hook. The page itself, lines 1927–2238, reads top-to-
   bottom as a layout outline. *That* is the readable, explicit page.
4. **Hides power, not policy.** The hex-literal call-outs the brief
   flagged at lines 313/316 are gone; the file now has zero hex
   literals and zero inline `style={{...}}` (verified by `grep -nE
   "#[0-9a-fA-F]{6}"` returning nothing). It is the only large detail
   page that has earned this.

### 3.2 The justifying lines

The strongest single block in the codebase for the canonical pattern
is **`EntityDetailPage.tsx` lines 1950–2238**, the post-`{entity &&
entityId && (` body. Read it as a contract:

```tsx
{entity && entityId && (
  <>
    {/* Identity hero — name, type, badges, aliases, roles */}
    <section className="space-y-4">
      ...                                           // lines 1953–2191
    </section>

    {/* Primary content */}
    <ActivityTimeline entityId={entityId} />         // line 2194

    {/* Supporting grid */}
    <div className="grid gap-6 sm:grid-cols-2">      // lines 2197–2200
      <GiftsPanel entityId={entityId} />
      <LoansPanel entityId={entityId} />
    </div>

    {/* Auxiliary sections, conditional by data */}
    <MessageThreadsSection entityId={entityId} />    // line 2203
    <LinkedContactsList entityId={entityId} />       // line 2206
    <FactsSection ... />                             // lines 2209–2218

    {/* Practical drawer — collapsed by default */}
    <PracticalDrawer ...>                            // lines 2221–2235
      <OwnerSetupBanner entity={entity} />
      <LinkedContactSection entityId={entity.id} entity={entity} />
      <EntityInfoSection ... />
      ...
    </PracticalDrawer>
  </>
)}
```

This is the shape the canonical `<DetailPage>` should expose:
**hero → primary → supporting grid → conditional auxiliaries →
collapsed drawer**, with each named region a component, not inline
JSX.

### 3.3 What Entity does that the others don't

- **Inline-editable identity** without leaving the page (lines
  1955–1996). Name edit is `Pencil`-toggle → `Input` + Save/Cancel
  via `Check`/`X` icon-buttons, exactly the pattern duplicated by
  ConnectorDetailPage's cursor-edit flow. Owning this once is what
  the canonical should do.
- **Status pills as a row, not a column.** Owner / Unidentified /
  Mark-confirmed sit on the same H1 line, not in a separate metadata
  card. EpisodeDetailPage hides them inside `CardContent`;
  QaInvestigationDetailPage already does it correctly (lines
  481–485, with `<StatusBadge>` and `<SeverityBadge>` adjacent to the
  H1).
- **Conditional auxiliaries.** `MessageThreadsSection` and
  `LinkedContactsList` *render nothing* if their data is empty; they
  internalize the empty check (verify via `grep`). This is the
  "hide when empty" rule that QA pages also follow (e.g.
  `TriggeringSessionsCard` returns `null` at line 231 of
  `QaInvestigationDetailPage.tsx`). Fact / Rule / Episode pages keep
  empty rows visible, leaving "No provenance data." stub copy
  (FactDetailPage line 226): chatty, not honest.
- **Practical drawer.** A collapsed-by-default fold for credentials
  and integrations is the cleanest answer to "I have settings-y
  fields that don't belong on the primary read surface". The
  canonical should expose this slot; Connector's discretion-thresholds
  card belongs there, not stacked at primary-content level.

### 3.4 What it costs to win

EntityDetailPage is **2,240 lines.** It loses on "Readable & explicit"
because the file is huge and crammed with feature-specific code
(Telegram auth at lines 494–928, gift / loan tracking, fact grouping,
provenance, message threads). The *layout pattern* is excellent, but
the *file* is doing too much.

The fix is not to redesign; it is to **extract the layout pattern**.
Lines 1927–2238 are the canonical shell; the rest of the file is
"things one entity page happens to need." A `<DetailPage>` shell that
owns lines 1929–1948 (breadcrumbs + L/E states) and exposes slots for
`hero`, `primary`, `supporting`, `auxiliaries`, `drawer` would let the
remaining 2,000 lines collapse into named child components.

### 3.5 The one that almost won: `QaInvestigationDetailPage`

`QaInvestigationDetailPage.tsx` (611 lines) is the cleanest "stack of
cards" detail page in the dashboard. Its strengths:

- **Page-level loading** via `<PageSkeleton />` (lines 401–417,
  invoked at line 448): a *named* skeleton, not an inline three-stack.
- **Status badges adjacent to H1** (lines 481–485):
  `<h1 className="text-2xl font-bold tracking-tight">Investigation
  Detail</h1> <StatusBadge> <SeverityBadge>`.
- **Metadata `<dl>` instead of CardContent boilerplate.** The
  `grid grid-cols-[auto_1fr] gap-x-6 gap-y-3 text-sm` `<dl>` pattern
  (lines 496–525, 548–565) is the cleanest "metadata strip" treatment
  in the codebase. Every Fact/Rule/Episode page is reinventing this in
  inline `<div>` form.
- **`*Card` sub-components return `null` when their data is missing**
  (e.g. `TriggeringSessionsCard` line 231, `PrCard` line 264). Honest
  empty handling.
- **Page skeleton sized to match the page.** Two cards in the
  skeleton matches three cards in the page, close enough that the
  layout doesn't shift.

It loses to Entity on **two narrow but load-bearing axes**:

1. The H1 reads "Investigation Detail," not the record's identity.
   Every page in the codebase that does this (Episode, Rule,
   Investigation, Patrol, Session) is failing the doctrine's
   "owner-direct" voice rule. The H1 must be the *thing*, not the
   *type-of-thing*.
2. No real composition primitive: it is a hand-rolled stack of
   cards. Adding a section is "paste another `<Card>` block." Entity
   has named child sections that compose; QA Investigation has
   inline JSX that doesn't.

**Recommendation: borrow QaInvestigationDetailPage's metadata `<dl>`
pattern and named PageSkeleton; keep EntityDetailPage's hero +
section composition as the canonical body.**

---

## 4. Canonical Gaps: What Entity Is Missing

Entity wins, but it is missing real things the other pages do better:

### 4.1 Named, page-shaped skeleton

Entity's loading block (lines 1936–1942) is the same hand-rolled
three-`<Skeleton>` stack as Fact/Rule/Episode/Contact. **QA
Investigation's `<PageSkeleton />`** (lines 401–417) is named and
matches the page's actual card layout. The canonical
`<DetailPage>` should accept (or default) a `skeleton` slot whose
shape mirrors the body.

### 4.2 Metadata as `<dl>`, not stacked `<div>`s

Entity stacks identity rows in `flex flex-wrap`. Fact/Rule/Episode
do the same. **QA Investigation / QA Patrol / Session** all use
the cleaner `dl grid grid-cols-[auto_1fr] gap-x-6 gap-y-3 text-sm`
shape (e.g. SessionDetailPage lines 173–215, QaInvestigationDetailPage
lines 496–525). Entity's "Aliases / Roles" treatment is *more*
expressive (inline-editable badge chips) but its core metadata
strip should default to the `<dl>` form for consistency.

### 4.3 Action-bar position

Entity has no top-level action bar; actions are scattered (rename
on the H1, "Mark confirmed" mid-row, delete-info inside child
sections). **ContactDetailPage's delegated view** puts edit + delete
adjacent to the title in the header card (`ContactDetailView.tsx`
lines 864–898). **ConnectorDetailPage** has a dedicated back-link
above the H1 (lines 172–179). The canonical needs an explicit
`actions` slot rendered to the *right* of the H1 row, owner-direct
and primary-secondary-destructive ordered.

### 4.4 Status-badge slot adjacent to H1

Entity has Owner / Unidentified badges adjacent to the H1 but
**doesn't expose this as a slot**; it's hand-built in the identity
hero. **QA pages** standardize the `flex items-center gap-4`
H1 + StatusBadge + SeverityBadge pattern
(QaInvestigationDetailPage line 481, QaPatrolDetailPage line 272,
SessionDetailPage line 159). The canonical should expose a
first-class `status` slot.

### 4.5 Breadcrumbs

Entity uses `<Breadcrumbs>` correctly (lines 1929–1934). **Connector
does not**; it uses a back-link button instead (lines 172–179). The
back-link is friendlier for one-off cases but is inconsistent with
the rest of the dashboard. Canonical should default to breadcrumbs;
the back-link can be a configuration of breadcrumbs.

### 4.6 Time as a typed primitive

Entity uses `formatDistanceToNow` from `date-fns` (line 15) for some
fields and bare `format(...)` for others; Connector mixes
`formatDistanceToNow` (line 156) with `Date.toLocaleDateString` with
hardcoded `"en-US"` (line 162); Episode/Fact/Rule use bare
`new Date(x).toLocaleString()` (e.g. EpisodeDetailPage lines 111,
132, 137). **No detail page is doctrine-compliant on time.** The
canonical should not solve this directly, but it should *not entrench
the inconsistency by accepting raw strings*. Force consumers to
pass typed `Date` or ISO strings; render via a future `<Time>`
primitive that doesn't exist yet. (Frontend topology §"Token leaks"
confirms this is a known debt.)

### 4.7 `permanenceBadge` deduplication

The `permanenceBadge` and `maturityBadge` helpers in `RuleDetailPage`
(lines 14–54) and `permanenceBadge` in `FactDetailPage` (lines 14–31)
are duplicated verbatim and should be extracted to
`components/memory/badges.tsx` or similar. This is not a `<DetailPage>`
concern: it is a "delete one of the duplicates before migrating"
concern. List it in the migration order, §6.

---

## 5. Proposed `<DetailPage>` Contract

**Not** an implementation. A contract: the props that must exist for
the canonical pattern to be reused without each consumer reinventing it.

```tsx
import type { ReactNode } from "react";

interface BreadcrumbItem {
  label: string;
  href?: string;
}

export interface DetailPageProps {
  // ----- Loading / error / data gating -----
  isLoading?: boolean;
  error?: Error | null;
  /**
   * Whether the record has been resolved. When false, the page renders
   * the empty state. Required so consumers don't have to author
   * `{record && (...)}` themselves.
   */
  ready: boolean;

  // ----- Chrome -----
  /**
   * Page-level breadcrumbs. Always rendered if present, including
   * during loading and error.
   */
  breadcrumbs: BreadcrumbItem[];

  /**
   * The record's *identity* — owner-direct, sentence case, no
   * "Detail" suffix. Required: a Detail page without an identity
   * is a list page.
   */
  title: ReactNode;

  /**
   * Optional sub-line: typically the record's predicate, type, or
   * monospace ID. Rendered immediately under the H1 in muted color.
   */
  subtitle?: ReactNode;

  /**
   * Status / type pills rendered adjacent to the title on the same
   * row. Use shadcn Badge. Owner first, severity / state second,
   * tertiary chips last.
   */
  status?: ReactNode;

  /**
   * Action buttons rendered to the *right* of the title row.
   * Convention: primary (default Button), secondary (outline),
   * destructive (variant="destructive") in that order.
   */
  actions?: ReactNode;

  // ----- Body slots, in render order -----

  /**
   * Optional. Identity hero block — alias chips, role chips,
   * inline-editable fields. Used when the H1 + status + subtitle
   * are not sufficient. EntityDetailPage uses this; the
   * Memory/QA detail pages do not.
   */
  hero?: ReactNode;

  /**
   * Required. The dominant read surface — timeline, transcript,
   * primary table. The thing the operator opened the page for.
   */
  primary: ReactNode;

  /**
   * Optional. A side-by-side grid of supporting panels. Defaults
   * to `grid gap-6 sm:grid-cols-2` when present.
   */
  supporting?: ReactNode;

  /**
   * Optional. Auxiliary sections, rendered as a vertical stack.
   * Each section is responsible for hiding itself when empty
   * (return null). The canonical does *not* paper over this.
   */
  auxiliaries?: ReactNode;

  /**
   * Optional. Collapsed-by-default fold for "settings" / "secrets" /
   * "advanced" content. EntityDetailPage's `<PracticalDrawer>` is
   * the reference implementation; the canonical exposes the slot
   * and lets the consumer pass any collapsing primitive.
   */
  drawer?: ReactNode;

  // ----- Slot overrides for L/E states -----

  /**
   * Custom skeleton rendered when isLoading. Defaults to a named
   * `<DetailPageSkeleton archetype="card-stack" />` (or
   * `"hero-and-stack"` for hero-bearing pages).
   */
  skeleton?: ReactNode;

  /**
   * Custom error renderer. Receives the `error` prop. Defaults to
   * the existing `text-destructive py-12 text-center text-sm`
   * block.
   */
  renderError?: (error: Error) => ReactNode;

  /**
   * Custom not-found renderer (when `!isLoading && !error && !ready`).
   * Defaults to `<EmptyState title="Not found" ... />` from
   * `components/ui/empty-state.tsx`.
   */
  renderEmpty?: () => ReactNode;
}

export function DetailPage(props: DetailPageProps): JSX.Element;
```

**Defaults the contract bakes in.** Outer `space-y-6`. Breadcrumbs
mounted first, *always*. Title row is `flex items-center gap-4`.
Title typography is `text-2xl font-bold tracking-tight` (the modal
H1 size in the codebase: DashboardPage and Costs use `text-2xl`,
ButlersPage and Chronicles use `text-3xl`; pick the smaller, more
operator-toned one). Skeleton is a named, page-shaped block, not a
trio of arbitrary sizes. Error and empty states route through shared
components; consumers do not paste destructive-text divs.

**Slots the contract deliberately omits.**

- *Tabs.* Not mandatory for detail pages. ButlerDetailPage has
  eleven tabs because butlers are a workspace-grade record; that
  page should keep its tabs and treat them as `primary`. Other detail
  pages should not be encouraged to grow tabs through a slot.
- *Filter / search bar.* That's a list-page concern.
- *Pagination.* Belongs inside whichever section is paginating
  (e.g. `FactsSection` already owns its `onLoadMore`).

---

## 6. Migration Order: Easiest First

Six pages to migrate (Entity itself becomes the canonical, so the
shell wraps but does not change its body).

### 6.1 `EpisodeDetailPage` (easiest)

- Single card, no actions, no nested sections. Lift the H1 from
  `<CardTitle>Episode</CardTitle>` (line 48) to a real
  `title={\`Episode \${episode.id.slice(0, 8)}\`}` *or*, better, name
  it by content (`title={episode.session_id ?? "Episode"}`).
- Move the metadata blocks (Importance / Consolidated / refs / etc.)
  into a `<dl>` matching QaInvestigationDetailPage lines 496–525.
- Move metadata-JSON `<pre>` and timestamps into the auxiliary
  slot.
- Drop the inline three-skeleton; let the shell render
  `<DetailPageSkeleton>`.
- Estimated change: ~50 net lines deleted.

### 6.2 `FactDetailPage`

- Same shape as Episode, plus a Provenance block and a confidence
  progress bar.
- **Before migration:** delete `permanenceBadge` from this file
  (lines 14–31) and import the shared one (must be created; see
  §4.7). Same for the `confidenceBar` helper (lines 56–69); extract
  to a shared `<ConfidenceBar>` primitive.
- Use the `subtitle` slot for `fact.predicate`.
- Use the `actions` slot for the entity-link / object-link Links.
- Estimated change: ~80 net lines deleted between this and
  RuleDetailPage if the helpers are shared properly.

### 6.3 `RuleDetailPage`

- Migration is mechanical *after* FactDetailPage, because the
  shared helpers (`permanenceBadge`, progress bar) are already
  extracted.
- The "Effectiveness" + "Confidence" rows become a `<MetricsRow>`
  helper or stay inline. Either is fine.
- Estimated change: ~60 net lines deleted (after sharing the
  helpers).

### 6.4 `ContactDetailPage`

- The page itself is already 44 lines and barely needs a shell:
  but the *delegated* `ContactDetailView` is the real migration
  target. The view must:
  - **Delete the hex-color palettes** at `ContactDetailView.tsx`
    lines 51–62 and 69–77. Replace with semantic tokens or
    shadcn `Badge` variants. This is the single highest-priority
    token leak in the detail-page family.
  - Move the `<CardHeader>` + edit/delete buttons (lines 824–901)
    into the canonical `actions` slot.
  - Keep the rest of the view as `primary`.
- Estimated change: net zero lines, but a real cleanup of leaked
  tokens.

### 6.5 `ConnectorDetailPage`

- Replace the back-link button (lines 172–179) with proper
  `<Breadcrumbs>` (consistent with the other six).
- Hoist the cursor-edit flow (lines 270–319) into a generic
  `<InlineEditField>` (the same pattern is in EntityDetailPage's
  name-edit block (lines 1955–1996). One implementation, two
  consumers.
- Move "Discretion Settings" + "Batch Settings" into the `drawer`
  slot. They are operator-mode tuning, not primary read content.
- Move the `AlertDialog` confirmation (lines 549–566) into a
  per-action concern, not page chrome.
- Estimated change: ~100 net lines deleted across the page +
  shared inline-edit primitive.

### 6.6 `EntityDetailPage`: last (the canonical itself)

- This page becomes the **reference consumer** of `<DetailPage>`,
  not a migration. The body is already correct.
- Required cleanup: extract `PracticalDrawer` from the file
  (currently lines 1726–1766) into
  `components/ui/practical-drawer.tsx` so other pages (Connector,
  Butler) can adopt it.
- Required cleanup: extract `PulseStrip` and `PulseTile` (lines
  1072–1213); they are entity-specific in name, but the pattern
  ("at-a-glance status row") is exactly what a Connector / Butler
  detail page wants too.
- Estimated change: file shrinks from 2,240 to ~1,400 lines after
  extractions, with no behavior change.

### 6.7 Out of scope for this consolidation

`ButlerDetailPage` keeps its tabs. It is the only detail page in
the seven that legitimately needs a workspace shape: butlers have
truly orthogonal sub-domains (config vs. sessions vs. memory vs.
state vs. routing-log) and the Tabs primitive is already the right
answer. Migrating its outer chrome to `<DetailPage>` would be a
clean win: `title={name}`, `actions={<ChatPanel ... />}`,
`primary={<Tabs ... />}`, but the *internal* tab pattern is its
own consolidation problem and belongs in a separate audit of the
butler-detail tab family.

---

## 7. Summary

The canonical detail page is **`EntityDetailPage.tsx`**, body lines
1950–2238. It alone composes the four-tier density (hero → primary
→ supporting grid → drawer) the doctrine demands, and it has already
paid the token-discipline cost the other pages owe. Wrap it in a
`<DetailPage>` shell that owns chrome and L/E states. Borrow
QaInvestigationDetailPage's named `<PageSkeleton>` and `<dl>`
metadata pattern. Migrate Episode → Fact → Rule → Contact →
Connector in that order, deleting duplicated badge helpers along the
way. Leave ButlerDetailPage's tab interior for a separate audit.

The cleanup opens with **two named extractions** before any page is
touched:

1. `components/ui/practical-drawer.tsx` (from EntityDetailPage).
2. `components/memory/badges.tsx` for `permanenceBadge` /
   `maturityBadge` (delete from FactDetailPage *and* RuleDetailPage).

Once those land, the shell is implementable in one PR and the six
migrations are mechanical.
