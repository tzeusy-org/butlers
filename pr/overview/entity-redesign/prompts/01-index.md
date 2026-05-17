# 01 · Index — /entities · the tabular landing

**Replaces the existing `/entities` page.** This is the home for the
entire entity system. Everything else under `/entities/*` is an alternate
view of the same population.

## Hypothesis

> The list is the home. Curation is the primary verb. Navigation is the
> secondary verb. Everything is one keypress away.

Today's `/entities` is a fine list but doesn't elevate the four jobs the
user actually visits it to do: promote unconfirmed entities, archive
stale ones, delete wrong ones, merge duplicates. This redesign lifts
those four jobs into a permanent right-rail queue, and reshapes the
table itself to be a dense, scannable rule-list that scales to thousands
of rows without going noisy.

## Why this design

- **A right-rail queue, not a modal.** The queue is the most important
  thing on the page on any day there's a single item in it, so it lives
  in the layout. On the rare day the queue is empty, it disappears its
  borders and renders one serif-italic line. (Dispatch §4.g; §8.)
- **State colour is allowed *only* in the queue.** The main table is
  scan-mode; if amber leaks into the rows, the user can't tell what's
  asking for attention. A neutral table + a coloured queue rail does the
  signal work without the noise.
- **Bulk select promotes the gutter, not a toolbar.** When ≥1 row is
  selected, the row above the table grows a 10-px action band with
  archive / merge / forget. When the selection clears, it disappears.
  One affordance per signal. (Dispatch §0.5.)
- **No card chrome anywhere.** Just rules. The rail is a column with a
  left border; the rows are grid + hairlines.
- **Type filter chips, state filter chips, search input — in one row.**
  Mono pills, all reaching down to the same baseline. The Pill primitive
  in the prototype is the source of truth.

See `reference/prototype/exp-index.jsx` for the working version.

## Featureset

### 1.1 — Toolbar

```
[ search (transparent input with hairline-underline) ]   [ type chips ]   |   [ state chips ]
```

- **Search**: filters `name` and `aliases` substring-insensitive.
  Live; no submit.
- **Type chips**: `all · person · org · place · group`. Single-select.
  Default `all`.
- **State chips**: `unconfirmed · stale`. Toggle (independent).
  `unconfirmed` = `unidentified ∪ duplicate-candidate ∪
  has-unverified-contact`. Each chip shows its count next to its label.

### 1.2 — Header row

A 7-column grid (the gutter columns are intentional):

```
[ select • mark • name • type • tier • last-seen • chevron ]
   32        22     1fr   90      70      110          26
```

All eyebrows are 10-px mono uppercase 0.14-em tracking, `--mfg` colour.

### 1.3 — Row

- **Select gutter**: checkbox-style tick rendered as a 14-px hairline
  square. Clicking the square toggles selection; the rest of the row
  navigates to `/entities/:id`.
- **Mark**: 18-px `EntityMark`. Initials for persons, type glyph for
  everything else.
- **Name**: 13-px sans 500. After the name, inline pills (only if the
  entity is in an attention state):
  - `unidentified` (amber outline pill)
  - `likely dupe of <name>` (amber outline pill, with target name)
  - `stale` (neutral outline pill)
  - "+N aliases" (dim mono text, no pill)
- **Type**: 10-px mono uppercase.
- **Tier**: `TierBadge` (coloured 6-px square + `t1` etc) or em-dash.
- **Last-seen**: relative ("today", "12d", "8mo", "2y"), 11-px mono
  tabular tnum, right-aligned, `--mfg`.
- **Chevron**: `›`, `--dim`.

Row vertical padding: 10–12 px. Row hover: 4% white tint. Selected: 4%
white tint that doesn't change on hover.

### 1.4 — Bulk action gutter (only when selection > 0)

Renders between the toolbar and the header row. 10-px vertical padding,
4% white background, mono caption:

```
N selected   archive   merge…   forget                            clear
```

`archive` / `merge…` are neutral; `forget` is `--red`. Underlined like
all links. Each action requires a confirm step before firing.

### 1.5 — Right-rail queue

300-px wide column. Top-level eyebrow `needs you · N` with a serif
italic gloss. Below: stacked queue cards, hairline-separated.

Three card types:

**Unidentified** — `unknown@swiftpost.co` etc.

```
─────────────────────────────────────────
unidentified                    2026-04-19   ← eyebrow + mono date
unknown@swiftpost.co                        ← 13-px sans
Seen on a memory thread; no                 ← serif italic 12-px voice
contact match yet.
[merge into…]  new person  dismiss          ← commit + 2 pills
```

**Duplicate candidate** — two entities side by side with `≈`.

```
duplicate candidate             2024-07-01
[TT] Tan Tanvir  ≈  [TA] Tanvir Ahmed
Shared email · same employer.
[merge]  keep both
```

**Stale** — single entity with a "N months since last touch" line.

```
stale                            2024-09-12
[CP] Carla Pugh
No touch in 20 months.
[archive]  keep
```

Each card uses one commit button (`--fg` background, mono uppercase
11 px) and two pills. Never two commit buttons on the same card.

### 1.6 — Footer strip

```
N of TOTAL   ·   select · click row gutter         n · new · ⌘k · finder · ↑↓ to step
```

Mono 10-px, `--mfg`, uppercase, 0.06 em tracking. The Finder hint here
is intentional cross-promotion to prompt 07.

### 1.7 — Keyboard

- `↑ ↓` — move row cursor
- `Enter` — open the row's `/entities/:id`
- `Space` — toggle row selection
- `Shift+↑/↓` — extend selection
- `Esc` — clear selection
- `n` — focus the search input, prefilled empty (placeholder cycles
  hints, see Dispatch §8 voice rules)
- `⌘K` / `/` — open the Finder (see prompt 07)

### 1.8 — Sub-page tab strip

Above the toolbar, a tab strip with five tabs:

```
Index  •  Hop  •  Columns  •  Concentration  •  Social map      /entities
```

This strip lives on **every** `/entities/*` page. Underline = active.
Right-aligned mono `/entities` is the breadcrumb.

## API

- `GET /api/entities?type=&state=&q=&has=contact&cursor=`
- `GET /api/entities/queue` (prompt 00 §0.6)
- `POST /api/entities/:id/merge`
- `POST /api/entities/:id/archive`
- `POST /api/entities/queue/dismiss`
- `DELETE /api/entities/:id`
- `POST /api/entities` (used by "new person" action on unidentified)

Pagination via cursor; the table can be 10k rows.

## Visual reference

`reference/prototype/exp-index.jsx` is the canonical version. Open
`reference/prototype/Entities.html` and scroll to "00 · index" to see
it live.

## Acceptance criteria

- [ ] `/entities` renders the redesigned Index with toolbar + tab strip
      + table + right-rail queue.
- [ ] Selecting a row reveals the bulk gutter; clearing hides it.
- [ ] State colour appears nowhere in the main table. Inline state
      pills are confined to the name cell.
- [ ] Queue cards each commit one primary action plus 1–2 pills.
      Buttons disable + show a 200-ms inline "…" while the request is
      in flight. Errors surface as a serif-italic line below the card.
- [ ] Empty queue renders one serif-italic sentence ("Nothing
      waiting.") and disappears its borders.
- [ ] Keyboard map per §1.7 is wired.
- [ ] Sub-page tabs are present, active state is correct.

## Anti-patterns to avoid

- Don't add an illustration to the empty state. One serif-italic
  sentence. (Dispatch §8.)
- Don't add a per-row kebab. The chevron is the affordance; the row
  navigates.
- Don't promote queue actions to a top banner. They live in the rail.
- Don't introduce a third button style on queue cards. Commit + Pill,
  full stop.
- Don't animate row changes when the queue updates. Items appear and
  disappear instantly. (Dispatch §6.)
