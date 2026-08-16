# 07 · Finder — app-wide spotlight (Ctrl/Cmd-K or /)

## Shipped component mapping

The "Finder" described in this prompt is shipped as **`EntityFinder`**
(`frontend/src/components/layout/EntityFinder.tsx`), the cmdk-based
entity-first Cmd-K surface (bead bu-xfjwk). It is mounted in
`RootLayout` alongside — but separately from — the legacy
`CommandPalette` (`frontend/src/components/layout/CommandPalette.tsx`),
which is a shadcn-based global search surface still in use for non-entity
navigation (butlers, pages, settings). The two components are distinct:

- **`EntityFinder`** — Ctrl/Cmd-K or `/` shortcut; entity-first; implements the
  design in this prompt; registered via `dispatchOpenEntityFinder()`.
- **`CommandPalette`** (legacy) — also mounted in `RootLayout`; handles
  general navigation; registered via `dispatchOpenCommandPalette()`.
  It is not replaced by this prompt — do not remove it.

When wiring keyboard shortcuts, `Ctrl/Cmd-K` or `/` dispatches to `EntityFinder`.
The existing `CommandPalette` key binding is separate and must remain.

---

**A new app-wide surface, not under `/entities`.** Press `Ctrl/Cmd-K`
or `/` anywhere in Butlers. A modal overlay takes the page. Type — fuzzy
match across names, aliases, contact-facts, predicates. Arrows step;
`Enter` opens; `Tab` hops without leaving the Finder open.

Think PowerToys Run + Spotlight + Linear's command bar. The Finder
becomes the primary jump-to anywhere in the app, but its richest result
type is entities, so we build that first.

## Hypothesis

> The keyboard is the fastest way to walk the graph. Make typing the
> primary verb; make hopping a `Tab` away; make every other surface
> reachable from the same input.

## Why this design

- **One input. No tabs.** A single string field that fuzzy-matches
  everything. No "search entities" mode; the result rows are typed and
  carry their own glyphs.
- **Right pane is a live preview.** While the cursor moves through
  results, the right side renders a preview of the highlighted entity
  (or rule, episode, approval). Never leaves the Finder.
- **Two keys for two intents.** `Enter` = open the row's canonical page.
  `Tab` = "hop into" — re-centre the most recent view on this entity
  *and dismiss the Finder*. This is the move that makes the Finder feel
  alive: you can type, tab, type, tab, and walk the graph at typing
  speed.
- **Cmd-K from anywhere.** Not just `/entities`. Result types beyond
  entities (approvals, memory rules, episodes, butlers, settings) light
  up as the app grows.
- **Keyboard footer documents itself.** A row of mono captions at the
  bottom shows the keys: `↑↓ step · ↵ open · ⇥ hop · esc close`.

See `reference/prototype/exp-command.jsx` for the working version (the
entity case only).

## Featureset

### 7.1 — Activation

- Global keydown listener on `window`, `Ctrl+K` (Win/Linux) or `Cmd+K`
  (Mac), and `/` (when no input is focused). Opens the overlay.
- Initial state: input empty + focus, results show owner-pinned set
  (`me` excluded), sorted by direct-edge weight desc.
- `Esc` closes. Click the page outside the overlay closes.

### 7.2 — Overlay structure

Full-viewport overlay; semitransparent `--bg-deep` backdrop at 60%
opacity. Inside, a panel sized to `min(1100px, 90vw) × 70vh`, centred,
with hairline border. Two columns: 1.4fr / 1fr.

### 7.3 — Left column — finder

```
EYEBROW · finder · press / anywhere in butlers      esc to close
─────────────────────────────────────────────────────────────────
/   [input, 22 px sans, transparent]                ↑↓ step  ↵ open  ⇥ hop
─────────────────────────────────────────────────────────────────
[mark, 20px]  Name                            ×W    ↗ hop      ← active row
              type · tier · matched on "x"
[mark]        Name                            ×W
              type · tier · matched on "x"
…
─────────────────────────────────────────────────────────────────
KEYBOARD FOOTER, mono 10 px, --mfg uppercase:
· tier T  · type P/O/L  · last R  · merge M  · forget ⇧⌫    N of TOTAL
```

Row anatomy:

- Gutter: 20-px `EntityMark`. Active row tone: fill; otherwise neutral.
- Title block: 15-px sans for the name, 10-px mono caption beneath with
  `type · tier · matched on "<token>"`.
- Right: tnum touch weight + "↗ hop" mono caption (only on the active
  row).
- Active row: 2-px `--fg` left border, 4% white tint.

### 7.4 — Right column — preview

For the highlighted result, render a vertical-flow preview:

```
EYEBROW · preview · {id}
[mark, 32px]  Display 20px name
              type · tier · N aliases
serif italic gloss

─── relations ─────────────────────
PREDICATE  · Name   ×W
…  (5 rows max)
```

The preview always uses the same predicate-list primitive as the
Editorial detail page's relations block. Never link to anywhere from
the preview; the user uses keys to advance.

### 7.5 — Fuzzy matching

Score each entity against the query string by:

- Prefix match on `name` or any `alias`: score 100
- Substring match on `name` or any `alias`: score 50
- Substring match on a predicate label that the entity has an edge
  under: score 30 (e.g. searching "vendor" matches anyone purchased-from)
- Substring match on a contact-fact value: score 70 (matching `lin@`
  finds Lin)

Top 8 results. Ties broken by edge weight desc.

For the empty query, score by direct-edge weight from owner, descending;
unrelated entities are excluded.

### 7.6 — Keyboard

- `↑ ↓` — step through results
- `Enter` — open `/entities/:id`. Closes the Finder.
- `Tab` — "hop into": navigate the page underneath to centre on this
  entity (`/entities/hop?centre=:id`), close the Finder.
- `Esc` — close, restore prior focus.

Mode keys (stretch — leave as captions in the footer for now, wire
later):

- `t` — filter to tier N (type a number after)
- `p / o / l` — filter to person / org / place
- `r` — sort by recency
- `m` — open merge flow for the highlighted entity
- `Shift+Backspace` — open forget flow for the highlighted entity

### 7.7 — Result-type extensibility

The Finder must be ready to take more result types. A `Result` is:

```ts
type Result =
  | { kind: 'entity';   entity: Entity; matchedOn: string; weight: number }
  | { kind: 'rule';     rule: Rule;     matchedOn: string }
  | { kind: 'episode';  episode: Episode; matchedOn: string }
  | { kind: 'approval'; approval: Approval; matchedOn: string }
  | { kind: 'butler';   butler: Butler;   matchedOn: string };
```

For phase 1, only `kind: 'entity'` is wired. The data shape is open for
the rest. Render each row using a type-specific glyph in place of the
EntityMark.

## API

```
GET /api/search?q=<string>&limit=8 → Result[]
```

Server-side fuzzy is correct in the long run; for phase 1 it's fine to
do client-side fuzzy on a recent `/api/entities` page snapshot. The
endpoint shape should be agreed early so the swap is invisible.

## Visual reference

`reference/prototype/exp-command.jsx`.

## Acceptance criteria

- [ ] `Ctrl/Cmd-K` and `/` (when not in an input) open the Finder
      overlay.
- [ ] Empty query shows owner-pinned set; typing filters.
- [ ] `↑ ↓` move cursor; `Enter` opens; `Tab` hops; `Esc` closes.
- [ ] Right-pane preview updates with the cursor.
- [ ] Result row shows the matched token in its caption when there is
      a query.
- [ ] The Finder is wired on every page in the app, not just
      `/entities`.
- [ ] Closing the Finder restores focus to whatever was focused before
      it opened.

## Anti-patterns to avoid

- **Tabs across the top of the Finder.** ("Entities · Memory ·
  Approvals.") The result types live in the row, not in a chrome row.
- **A search button.** This is a keyboard surface. There is no button.
- **Live preview that auto-navigates after a delay.** The preview is
  inert. Navigation is always an explicit key.
- **Confetti / spring on the open animation.** The overlay appears
  instantly. Dispatch §6.

## Stretch

- A second pane on the right for "recent" jumps — last 5 entities the
  user opened from the Finder.
- A history-back behaviour: closing the Finder returns to the entity
  you came from rather than to the page underneath.
