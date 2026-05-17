# 04 · Concentration — /entities/concentration · the balance sheet

**A new sub-route.** Read the world as weights, by predicate. Tabs flip
the predicate; rows render as bipartite weight bars; the headline number
is **top-3 share** — what fraction of your total touches go to the top
three entities under this predicate.

## Hypothesis

> Some questions about the graph are best answered with a chart, not a
> graph. "Where am I concentrated?" is one of them.

This view is what you reach for to answer:

- Which vendors do I actually buy from?
- Which subscriptions am I paying for vs using?
- Who do I co-attend events with most?
- Which colleagues touch most of my work threads?

It's deliberately uniform: same layout for every tab. The shape of the
distribution is the takeaway, not the individual entities.

## Why this design

- **Tabs flip the *predicate*, not the page.** Same render, different
  query. The user learns the shape once and reuses it.
- **The bar lives inline with the row.** No separate chart pane. The
  row is `mark · name · bar · count · last-seen` — the bar is column
  three of a five-column grid.
- **No axis labels, no gridlines.** The percentage label floats just
  past the right end of the bar. Eyebrow above the column reads `share`.
  (Dispatch §1c — anti-chartjunk.)
- **One headline metric.** Top-3 share, mono 22 px, in the toolbar.
  Everything else is incidental.
- **Tail count is meta, not central.** The footer shows the number of
  entities with <1% share. Useful for vendor-cleanup intuition, but not
  the main signal.

See `reference/prototype/exp-concentration.jsx`.

## Featureset

### 4.1 — Tab strip

Four pills, mono uppercase:

```
vendors · subscriptions · co-attended · colleagues
```

Plus a counter suffix on each tab showing the population size for that
predicate.

Each tab maps to a predicate:

- `vendors`       → `purchased-from`
- `subscriptions` → `subscribed-to`
- `co-attended`   → `co-attended`
- `colleagues`    → `colleague-of`

Future tabs can extend this list; the page is generic over a single
predicate.

### 4.2 — Toolbar

Left: tab strip. Right: `EYEBROW · top 3 share` + the percentage
rendered as 22 px sans 500 tabular tnum.

### 4.3 — Header row

5-column grid:

```
[ org · share · touches · last ]
 140   1fr     80         60
```

All eyebrows, mono 10 px.

### 4.4 — Data row

```
[mark]  Name                        ▓▓▓▓▓▓▓▓░ 42.7%      ×144       05-15
```

- Bar: `width: weight / top * 100%`, height 6 px (vertical centre of an
  18-px row), `--fg` colour, opacity 0.92.
- Percentage label: mono 10 px, `--mfg`, positioned at `left:
  calc(bar% + 8px)`.
- Touches: mono tnum `×N` right-aligned.
- Last: mono `mm-dd`, dim.

Rows separated by 1-px hairline. No row hover state (this is a
*read* view, not a *click* view).

### 4.5 — Footer KPI strip

4-column grid, hairline-separated:

```
total touches   |   orgs       |   top                |   tail (<1%)
  TOTAL              N              top entity name        N entities
```

Mono eyebrows, 18-px sans tabular numbers.

### 4.6 — Sub-page tab strip

Above all of the above. Active tab: `Concentration`.

### 4.7 — Empty state

If there are zero rows for the selected predicate (rare), render one
serif-italic line in the body: "Nothing here."

## API

```
GET /api/concentration?pred=purchased-from
→ {
    pred: 'purchased-from',
    total: number,
    top3Share: number,            // 0..1
    rows: Array<{
      entityId: EntityId,
      name: string,
      weight: number,
      share: number,              // 0..1
      lastSeen?: string,
    }>;
  }
```

Compute server-side so the frontend doesn't need the full triple set.

If the endpoint isn't available immediately, fall back to building it
client-side from the population already available via `/api/entities` +
per-entity `/neighbours`. That's a stopgap, not the goal.

## Visual reference

`reference/prototype/exp-concentration.jsx`.

## Acceptance criteria

- [ ] `/entities/concentration` renders the redesigned page with the
      tab strip + toolbar + table + footer KPIs.
- [ ] Switching tabs swaps the data without losing scroll position.
- [ ] Bars are visually anchored to the bar column's left edge; the
      longest bar in the visible set fills it.
- [ ] Top-3 share matches the server-computed value (cross-check at
      least one tab manually).
- [ ] Sub-page tab strip is present; "Concentration" is the active tab.
- [ ] No row hover state; no click-to-navigate. (This is a read view.)

## Anti-patterns to avoid

- **Adding a row click that opens a detail page.** Concentration is a
  scan-mode view. If the user wants to drill, they take the entity name
  to the Index or Hop.
- **Animating the bars on tab switch.** Snap instantly. Dispatch §6.
- **Stacked-bar layout.** Don't try to render multiple predicates as
  stacks on one row. The tabs do that work.
- **Sorting controls.** Always sorted by weight desc. If the user wants
  alphabetical, they're on the wrong page.

## Stretch

- Time-of-quarter comparison. ("Top-3 share this Q vs last Q.") Useful;
  out of scope for v1.
- A "concentration index" metric beyond top-3 — Gini, HHI, etc. Skip
  unless requested.
