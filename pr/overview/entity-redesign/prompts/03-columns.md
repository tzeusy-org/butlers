# 03 · Columns — /entities/columns · the Finder cascade

**A new sub-route.** Drill the graph left-to-right. Each column is one
entity's outgoing relations grouped by predicate. Selecting an item
opens the next column to its right.

## Hypothesis

> A keyboard person navigates the graph faster by reading text than by
> chasing nodes. Columns is text from start to finish, with the trail
> represented as the columns themselves.

This is Finder for entities. Same mental model: parent on the left,
selection in the middle, child on the right. Stepping deeper grows the
horizontal scroll; stepping back collapses it. No node-link drawing,
no animation.

## Why this design

- **The columns *are* the breadcrumb.** No separate trail UI is needed.
  Where you are is what you see.
- **Predicate as section heading.** Within a column, items are grouped
  by predicate; each group has a mono eyebrow with the predicate label
  and an item count. This gives you "what are all the ways Northwind
  relates to other things" at a glance.
- **One column = one entity's view of the world.** The column header
  is the entity's mark + name + type + tier. The body is everything
  this entity has outgoing relations to, sorted by weight within group.
- **Click-to-deepen, click-the-other-column-to-narrow.** Selecting a
  different item in column N truncates and replaces columns > N.

See `reference/prototype/exp-columns.jsx` for the working version.

## Featureset

### 3.1 — Column

- Width: 280 px. Flex-shrink: 0.
- Right border: 1 px `--border`.
- Internal layout: header + scrollable body + footer hint.

### 3.2 — Column header

```
EYEBROW · owner / hop N            ← top
[mark]  Name                       ← 18-px mark + 14-px sans 500
        type · tier
```

The first column has eyebrow `owner` and a faint elevated background
(`--bg-elev`). Subsequent columns have eyebrow `hop N`.

### 3.3 — Column body

For each predicate the entity has outgoing relations under, render a
section:

```
PREDICATE LABEL                     N
─────────────────────────────────────
[mark]  Name                        ×W ›
        tier hint
[mark]  Name                        ×W ›
…
```

Section header: mono eyebrow + count, hairline-separated from body.
Section bodies cap at the top 6 entries by weight; if there are more,
add a "+N more" row at the bottom of the section that, when clicked,
opens a side sheet listing all of them (out of scope for v1 — for v1,
just show the top 6 and let the user use the Index for the full list).

Row:

- 16-px `EntityMark`
- Name + tier hint (`t1` etc) in a vertical mini-column
- `×W` mono tnum + `›` chevron on the right

Padding: 8 px vertical. Hairline below.

### 3.4 — Selection

When an item is clicked, the column to its right is replaced with a
column centred on that item. Any columns further right are dropped.

Selected row: 6% white background + `→` chevron in `--fg`. Persists
until selection moves.

### 3.5 — Footer hint

Last column (the rightmost one) shows a 1-line mono eyebrow at the
bottom:

```
STEP DEEPER →
```

Earlier columns have no footer.

### 3.6 — Horizontal scroll

The page itself scrolls horizontally as columns are added. The active
column should auto-scroll into view (Dispatch forbids `scrollIntoView`
elsewhere, but for **horizontal** programmatic scroll inside this
single component it's fine — implement via direct `scrollLeft`
assignment, never via `scrollIntoView({ behavior: 'smooth' })`).

### 3.7 — Keyboard

- `↑ ↓` — move cursor within the current column
- `→` — step into the cursored item, opening a new column
- `←` — pop the rightmost column
- `Enter` — open `/entities/:id` for the cursored item

### 3.8 — Sub-page tab strip

As with every `/entities/*` page. Active tab: `Columns`.

## API

- `GET /api/entities/:id/neighbours` (same endpoint as Hop)

That's it. Columns is a pure remix of the Hop primitive.

## Visual reference

`reference/prototype/exp-columns.jsx`.

## Acceptance criteria

- [ ] `/entities/columns` opens with one column centred on the owner.
- [ ] Clicking an item adds a new column to the right.
- [ ] Clicking a different item in column N truncates columns > N.
- [ ] Predicate groups within a column are sorted by `max(weight)`
      descending; rows within a group sorted by weight desc.
- [ ] Top-6 cap per predicate group enforced.
- [ ] Sub-page tab strip is present; "Columns" is the active tab.
- [ ] Keyboard map per §3.7.
- [ ] Horizontal scroll auto-keeps the latest column visible.

## Anti-patterns to avoid

- **Breadcrumb on top.** The columns are the breadcrumb. Don't add
  another one.
- **Per-row kebab menus.** Click the row to deepen; the chevron is the
  affordance.
- **Animations on column open/close.** Instant. Dispatch §6.
- **Lazy-loading the next column.** Prefetch on hover of a row; render
  instantly on click. The neighbours endpoint is cheap enough.

## Stretch

- A "pin" affordance on a column so it stays even if you step back
  past it. Out of scope for v1.
