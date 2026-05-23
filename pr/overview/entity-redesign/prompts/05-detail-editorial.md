# 05 · Detail · Editorial — /entities/:id · the dispatch (default)

**Replaces the existing `/entities/:id` page.** This is the
default detail page. Reads top to bottom as a dispatch about the entity.

## Hypothesis

> The detail page is a sheet of paper the house wrote about this
> person. You can read it, put it down, come back tomorrow.

The existing detail page is functional but treats the entity as a
record: header, fields, a tab of relations, an actions kebab. The
Editorial redesign reframes it as content. A two-column editorial
layout with hero, narrative, and index. Curation actions are present
but quiet — a rail at the bottom of the right column.

## Why this design

- **Two-column editorial scaffold (Dispatch §3.b).** 1.4fr left, 1fr
  right, 56 px gap. Left is narrative; right is index.
- **Hero is the entity, not the page title.** Big display headline,
  serif voice paragraph one line below, then a horizontal strip of
  badges (tier, role, state) and first-seen/last-seen in mono.
- **A 90-day touch sparkline lives in the top-right.** Single SVG, no
  axes, no chartjunk. Daily buckets as vertical sticks; absent days are
  the same height but rendered at 4% white. This gives a glanceable
  "are they near?" signal without a full timeline.
- **Contacts are grouped by predicate in the index column.** Each
  predicate becomes a small section with its own eyebrow. Multi-valued
  is the default; primary first; unverified marked with a small amber
  dot.
- **Provenance gets its own section in the index column.** Mono-only,
  one row per butler, count tabular. Useful for "why does the system
  think this".
- **Curation is the *bottom* of the index column.** Six small links in
  a 2×3 grid, separated by hairlines top and bottom. The list is:
  merge, promote tier, demote tier, archive, forget, edit aliases.
  `forget` is `--red`. A serif italic gloss below: "Forgetting also
  tombstones the source. Aliases stay."

See `reference/prototype/exp-detail.jsx` `DetailEditorial`.

## Featureset

### 5.1 — Page chrome

Sub-page tab strip + breadcrumb strip:

```
Index  •  Hop  •  Columns  •  Concentration  •  Social map      /entities
─────────────────────────────────────────────────────────────────────────
/entities  ›  Lin Tan                          prev k · next j · close esc
```

The detail page is reached from any other view; the back link returns to
`/entities`. `k` / `j` step through siblings (next/previous entity in
the most recent list scope — Index by default).

### 5.2 — Hero (top of the editorial scaffold)

Left (1.4fr):

```
EYEBROW · person · entity · {id}
[mark, 40px]   Display 44px name
Serif voice gloss — one paragraph, max 64ch.
[tier badge]   [role badge]   [state pill if any]
mono small:   first seen · YYYY-MM-DD  ·  last seen · YYYY-MM-DD
```

Right (1fr) — separated from left by a vertical 1-px `--border`:

```
EYEBROW · last 90 days · touches
[sparkline svg, 100% wide, 56 px tall]
mono caption:   -90d ……………………………………………… today
```

The voice gloss is one of a handful of canned lines selected by the
backend or the frontend (the prototype's `gloss(e)` function shows the
pattern — pick by tier, by state, by category).

### 5.3 — Two-column body

```
LEFT                                  RIGHT
─────────────────────────────────────────────────
relations · N                         contacts · N
[predicate · entity · weight · src]   [predicate group]
…                                       value · primary? · verified?
                                      …
recent activity                       aliases · N
[date · kind · summary · via]         [alias chip] [alias chip] …
…                                     provenance
                                      [butler · count]
                                      …
                                      ─── CURATION ───────────────
                                      merge into…       promote tier
                                      demote tier       archive
                                      forget            edit aliases
                                      Serif gloss: "Forgetting also…"
```

### 5.4 — Relations list

A grid of 4 columns: `predicate · name · weight · src`.

- Predicate: `kind-tag` style, mono 10 px, with `→ / ←` directional
  glyph.
- Name: 14 px, hairline-underlined link, click navigates to that
  entity.
- Weight: mono tnum right-aligned.
- Source: dim mono uppercase butler id.

Cap at top 8 by weight; "see all (N)" link at the bottom of the
section if there are more.

### 5.5 — Activity feed

A grid of 4 columns: `date · kind · summary · via`.

- Date: mono `mm-dd` left-aligned.
- Kind: kind-tag pill — `receipt · thread · event · note · call`.
- Summary: 13-px sans body.
- Via: dim mono uppercase "via {butler}".

Show 8 most recent; "see all" link otherwise.

### 5.6 — Contacts index (right column)

For each contact predicate the entity has facts under:

```
EYEBROW · email                       ← predicate label, mono uppercase
lin.tan@northwinddlm.io   PRIMARY      ← value (mono if email/url, else sans)
                                       PRIMARY tag, mono small
lin@hotmail.com                        ← second value, no PRIMARY
…
```

Per-row affordances:

- `primary` mono pill if `meta.primary`.
- Small amber dot before the value if `!meta.verified`.
- Right-side icon-button to copy. (Optional — match design system if
  copy affordances exist elsewhere; otherwise leave out.)

### 5.7 — Aliases

Inline chip strip. Each chip is hairline + 10 px mono.

### 5.8 — Provenance

Grid of 2 columns: `butler · count`. Mono everywhere.

Source for the count: `GET /api/entities/:id/provenance`.

### 5.9 — Curation rail

Three rows × two columns of `CurationLink`s. Each is an underlined sans
13-px link with a `→` arrow on the right.

```
merge into…           promote tier
demote tier           archive
forget                edit aliases
```

`forget` colour: `--red`. Below the grid:

```
Serif italic 12 px, --mfg:
"Forgetting also tombstones the source. Aliases stay."
```

### 5.10 — Workbench toggle

Top right of the page (next to the breadcrumb's right side), a pill:

```
[ workbench → ]
```

Switches to the Workbench layout (prompt 06). Query param: `?mode=workbench`.

### 5.11 — Keyboard

- `k` — previous entity in the most recent list scope
- `j` — next entity
- `Esc` — back to `/entities`
- `e` — open edit-aliases inline
- `m` — start merge flow
- `Shift+Backspace` — start forget flow

## API

```
GET    /api/entities/:id
GET    /api/entities/:id/contacts
GET    /api/entities/:id/neighbours
GET    /api/entities/:id/activity?limit=8
GET    /api/entities/:id/provenance

POST   /api/entities/:id/contacts
POST   /api/entities/:id/contacts/:hash/verify
POST   /api/entities/:id/merge
POST   /api/entities/:id/promote-tier
POST   /api/entities/:id/archive
DELETE /api/entities/:id

GET    /api/entities/:id/spark?days=90    sparkline data; daily buckets
```

Sparkline data shape: `{ days: number[]; max: number }`.

## Visual reference

`reference/prototype/exp-detail.jsx` — `DetailEditorial`. Anchor person
is Lin Tan (`p-lin` in the sample data) — a tier-1 partner with multiple
contact-facts, multiple aliases, and dense activity. Use her to test the
layout.

## Acceptance criteria

- [ ] `/entities/:id` renders the editorial layout by default.
- [ ] `?mode=workbench` switches to the Workbench layout (prompt 06).
- [ ] Sparkline renders 90 daily buckets; absent days render as a faint
      4% rect of full bucket height (do not collapse to zero — that
      would lose the timeline shape).
- [ ] Contact predicates render multi-valued; `primary` shown first;
      unverified marked.
- [ ] Curation rail's `forget` is the *only* `--red` thing on the page.
- [ ] Empty contact predicates render no section at all (no "None yet."
      empty state per-predicate; the whole contacts section shows
      one serif-italic line if there are zero contact-facts overall).
- [ ] Activity is 8 most recent; "see all" link otherwise.
- [ ] Keyboard map per §5.11.

## Anti-patterns to avoid

- **A kebab menu containing all curation actions.** Curation is visible
  and quiet, not hidden.
- **Tabs on the detail page itself.** Relations / Activity / Contacts
  / Provenance are sections, not tabs. The page is meant to be read.
- **A floating action button on the bottom right.** This is not a
  social app.
- **Putting the sparkline in the hero on the left.** Right column.
  Always.
- **Showing a "0 verified" badge next to a contact section.** Render
  one row per fact; the verified state is per fact, not per section.

## Stretch

- Inline edit of `aliases`, `contacts`, `tier` without navigating away.
  v1 can open a side-sheet; v2 can do inline.
- A "show on social map" link in the curation rail that opens
  `/entities/social-map?focus={id}`. Easy win.
