# 02 · Hop — /entities/hop · re-centre on anything

**A new sub-route.** Default empty-state centres on the owner; clicking
any node re-centres on it and rebuilds the fan-out around the new
centre. A breadcrumb at the top tracks the chain of hops so you can
step back.

## Hypothesis

> Re-centring is the primitive. Hop until you're somewhere you didn't
> expect to be, then return.

The Index is a tabular catalog; Hop is a graph traversal. The same
population, two reading modes. Hop is the alternate-view to build
first because:

1. The primitive (a node and its predicate-grouped neighbours) composes
   into the other views — Columns is a horizontal cascade of Hop, the
   Editorial detail page's relations block is one Hop centre's
   neighbours rendered as a list.
2. It directly answers the questions the user can't get at via the
   Index: "who does Ravi know that I also know", "what other vendors am
   I associated with through Northwind", "where does this lead".
3. It's the most visual of the alternate views without ever needing a
   force simulation.

## Why this design

- **No physics.** Layout is deterministic. Each predicate gets a wedge
  slice; neighbours fan along an arc. Same centre → same picture, every
  load. (Dispatch §6.)
- **Predicate labels on the wedge, not the edge.** Edges stay thin and
  unlabelled; labels go on the wedge ring, sized at the same mono-eyebrow
  scale used everywhere else. This avoids the classic graph-vis problem
  of edge text overlapping itself.
- **Right pane is the canonical detail.** The page is split 1fr : 280 px.
  The right column shows the centre entity's name, type, aliases,
  first/last-seen, and a sorted list of relations. Clicking a relation
  re-centres on it; the page never opens a modal.
- **Breadcrumb is the only chrome on top.** The trail of centres so far,
  clickable. The last segment is the active centre, in `--fg`; earlier
  segments are underlined hairline links. A `reset` pill on the right.
- **Predicate filter chips at the bottom.** If the centre has multiple
  outgoing predicate types, chips appear (`all · knows · purchased-from
  · …`) to narrow the wedges. Stays out of the way until needed.

See `reference/prototype/exp-hop.jsx` for the working version.

## Featureset

### 2.1 — Centre node

- 40-px circle, `--fg` fill, owner initials or type glyph in `--bg` ink.
- Below: name (sans 500, 12 px) and type (mono eyebrow, 10 px).

### 2.2 — Neighbour nodes

- Radius scales as `8 + sqrt(weight) * 0.6`, capped at 16 px.
- `--bg-deep` fill, 1-px `--fg` stroke. Hover/active inverts to `--fg`
  fill, `--bg` ink.
- Name label below the circle, sans 11 px.
- Click → re-centre on this entity. Trail appends.

### 2.3 — Predicate wedges

Group neighbours by predicate. Each predicate gets a wedge slice equal
to `2π / N`. Within the wedge, neighbours fan along an arc of width
`min(wedgeAngle * 0.7, count * 0.18)`.

Per-predicate label sits on the ring at radius `R + 50` from centre,
mono uppercase 10 px, with a dim count suffix:

```
KNOWS · 12
```

### 2.4 — Edges

`stroke: var(--border)`, `stroke-width: 1`. On hover of the neighbour:
`stroke: var(--fg)`, `stroke-width: 1.4`. No arrowheads, no curves; the
direction is read from the predicate label.

### 2.5 — Breadcrumb

Top strip:

```
TRAIL  me  ›  Northwind Dlm  ›  Yuki Sato                            [reset]
```

- Past segments: underlined, click to pop back to that point.
- Active segment: `--fg`, no underline.
- `reset` pill on the right, only visible when trail length > 1.

### 2.6 — Predicate filter chips

Bottom strip. Only visible when the centre has ≥2 distinct outgoing
predicates. `all` chip first, then one chip per predicate. Pressing a
chip filters the wedges to that predicate; pressing `all` clears.

### 2.7 — Right pane (centre detail)

Static layout, top to bottom:

```
EYEBROW · centre
[mark] Name                          ← 17-px sans 500
       type · tier
─────────────────────────────────────
EYEBROW · first seen   EYEBROW · last seen
yyyy-mm-dd             yyyy-mm-dd
─────────────────────────────────────
EYEBROW · aliases
[chip] [chip] [chip]
─────────────────────────────────────
EYEBROW · relations · N
─ → knows           Ravi Mehta    ×96    ↑ click to hop
─ → purchased-from  Bunda Coffee  ×144
…
─────────────────────────────────────
"Click any node to make it the centre."   ← serif italic, --mfg
```

Right-pane rows are clickable; they re-centre and append to the trail.

### 2.8 — Keyboard

- `Esc` — pop the trail one step
- `r` — reset to owner
- `↑ ↓` — move cursor through the right-pane relation list
- `Enter` — re-centre on the cursored relation
- `1..9` — re-centre on the i-th visible neighbour (optional;
  keyboard-power-user nicety)

### 2.9 — Animation

**None.** When the centre changes, the new centre and its neighbours
render instantly. The breadcrumb appends without transition. The previous
centre vanishes. This is Dispatch §6 — calm is the feature.

## API

- `GET /api/entities/:id/neighbours` — primary call. Returns
  ```ts
  {
    centre: Entity,
    neighbours: Array<{
      pred: string;
      dir: 'out' | 'in';
      other: EntityId;
      meta: { conf, src, weight, lastSeen? };
    }>
  }
  ```
- `GET /api/entities/:id` — for the right-pane fields (firstSeen,
  lastSeen, aliases) when not included in `/neighbours`.

The frontend never assembles adjacencies from raw triples.

## Visual reference

`reference/prototype/exp-hop.jsx`. The prototype uses an SVG viewBox of
540×460 and renders deterministically — match that.

## Acceptance criteria

- [ ] `/entities/hop` renders with the owner as initial centre.
- [ ] Clicking any neighbour re-centres in <100 ms; trail appends.
- [ ] Trail segments are clickable and pop the trail back to that
      depth.
- [ ] Predicate wedges arrange neighbours without overlapping; labels
      sit on the outer ring without overlapping each other.
- [ ] Predicate chips appear only when there are ≥2 predicates;
      filtering re-renders the wedges immediately.
- [ ] Sub-page tab strip is present; "Hop" is the active tab.
- [ ] No animation on centre change. No spring physics. No drag.

## Anti-patterns to avoid

- **Force simulation.** Don't reach for d3-force. The layout is
  deterministic by predicate-group; that's the design.
- **Edge labels.** Predicate text on each edge will explode visual
  noise. Use the wedge ring.
- **Modal-on-click.** A click re-centres; it does not open a dialog.
- **Auto-zoom-to-fit.** Static viewBox. If a centre has too many
  neighbours, the wedges compress; the design tolerates that.
- **Showing inbound and outbound as different node shapes.** Use the `→
  / ←` glyph on the right-pane predicate label only.

## Stretch (don't ship in phase 1)

- Multi-centre comparison ("show Ravi and Yuki at the same time, both
  centred"). Useful for "what do they have in common" queries. Out of
  scope until v2.
- 2-hop visibility (faint ghost ring of friends-of-friends around the
  fringe). Tempting but adds visual noise that the design has worked
  hard to suppress.
