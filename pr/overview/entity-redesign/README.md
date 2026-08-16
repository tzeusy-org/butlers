# Entities redesign — Claude Code implementation pack

This folder is a complete handoff package for implementing a redesigned
`/entities` surface in the Butlers app. Read this README first, then work
through the prompts under `prompts/` in numerical order.

The reference prototype under `reference/prototype/` is **the working
visual spec**. Open `Entities.html` in a browser to see all surfaces
side-by-side. Everything is built in Dispatch-language-pure React; the
existing app uses the same idiom (shadcn + Tailwind tokens defined in
`frontend/src/index.css`), so the visual translation should be
near-lossless.

---

## What we're building

Today, the Butlers app has three entity-adjacent pages:

```
/entities                    → flat list of entities
/entities/social-map         → Dunbar concentric circles
/entities/:id                → per-entity detail (folded back from
                                /butlers/relationship/entities/:id)
/contacts                    → separate page (to be folded in)
```

After this work, the surface looks like this:

```
/entities                    → tabular index + curation queue (the home)
/entities/hop                → re-centre on any entity; predicate-grouped fan-out
/entities/columns            → Finder-style cascading drill
/entities/concentration      → balance-sheet of weight by predicate
/entities/social-map         → kept, unchanged in this pass
/entities/:id                → editorial detail (default) + workbench toggle
/contacts                    → 301 → /entities?has=contact
                                (or removed from sidebar; same query lives in Index filter)

App-wide:
  Ctrl/Cmd-K (or `/`)        → Finder spotlight, resolves to entities first
                                but eventually any record
```

The big shift: **the list is the home.** Hop, Columns, Concentration are
alternate _views_ of the same population, not separate products. Contact
information is just predicates on an entity, not a separate noun.

---

## How to use this pack

### Order of work

```
prompts/
  00-foundation.md         ← READ FIRST. Data model + API + the
                              contact-predicate fold-in. Everything else
                              depends on this landing cleanly.
  01-index.md              ← /entities tabular landing + curation queue
  02-hop.md                ← /entities/hop re-centre exploration
  03-columns.md            ← /entities/columns Finder cascade
  04-concentration.md      ← /entities/concentration balance sheet
  05-detail-editorial.md   ← /entities/:id default detail page
  06-detail-workbench.md   ← /entities/:id power-user toggle
  07-finder.md             ← app-wide spotlight (depends on 00 only)
```

Each prompt is self-contained: hypothesis, featureset, API touch-points,
visual cues from the prototype, acceptance criteria. Don't skip the
"why" sections — Dispatch is a discipline more than an aesthetic, and
the rationale is what keeps small decisions from drifting.

### Where to look in the prototype

- `reference/prototype/Entities.html` — wire-up; open to view everything
- `reference/prototype/data.jsx` — **canonical sample data**. The entity
  shape, predicate list, contact-fact schema, and adjacency builder
  here are the design's contract with the backend.
- `reference/prototype/atoms.jsx` — Dispatch primitives (Eyebrow, Voice,
  Title, EntityMark, TierBadge, Row, Pill, Section, Artboard)
- `reference/prototype/exp-*.jsx` — one file per surface; each maps 1:1
  to a prompt under `prompts/`
- `reference/DESIGN_LANGUAGE.md` — the canonical Dispatch reference.
  **Read this end-to-end before writing UI code.**

### Where to look in the real codebase

| What | Where |
|---|---|
| Existing list | `frontend/src/pages/EntitiesPage.tsx` |
| Existing detail | `frontend/src/pages/EntityDetailPage.tsx` |
| Existing social map | `frontend/src/pages/SocialMapPage.tsx` |
| Existing contacts (to fold in) | `frontend/src/pages/ContactsPage.tsx` (if present) |
| Router | `frontend/src/router.tsx` |
| Sidebar nav | `frontend/src/components/layout/Sidebar.tsx` and `nav-config.ts` |
| Design tokens | `frontend/src/index.css` |
| Page primitives | `frontend/src/components/ui/page.tsx` |

---

## Design language summary — Dispatch

The full reference is in `reference/DESIGN_LANGUAGE.md`. Internalise the
five inviolable rules before doing anything:

1. **Composure is the brand.** Calm even when broken. Colour and motion
   appear only when state demands.
2. **Type is the system.** Hierarchy comes from type and rules, not
   shadows or fills.
3. **Surfaces, not cards.** One elevation. Structure is rules and rhythm.
4. **Every element earns its place against state.** Empty section
   disappears its borders or shows a single serif-italic line.
5. **One affordance per signal.** Status is one of: dot, sliver,
   numeral, colour. Never two.

### Quick reference — the tokens you will actually use

```css
/* Surfaces (dark canonical, light is paper-warm) */
--bg              oklch(0.145 0 0)
--bg-elev         oklch(0.205 0 0)
--bg-deep         oklch(0.115 0 0)
--fg              oklch(0.985 0 0)
--mfg             oklch(0.708 0 0)
--dim             oklch(0.55 0 0)
--border          oklch(1 0 0 / 0.10)
--border-soft     oklch(1 0 0 / 0.06)
--border-strong   oklch(1 0 0 / 0.18)

/* State — sparingly, foreground/border only, never as a fill */
--red    oklch(0.685 0.250 29.2)    /* blocker, reauth, forget */
--amber  oklch(0.810 0.185 84.0)    /* unidentified, duplicate, unverified */
--green  oklch(0.790 0.195 148.2)   /* healthy, positive delta */

/* Type */
font-sans:  'Inter Tight'          /* UI: display, body, labels, numbers */
font-serif: 'Source Serif 4'       /* voice: LLM lines, empty states, glosses */
font-mono:  'JetBrains Mono'       /* times, IDs, deltas, KPI numerals, eyebrows */

/* Numerals — every numeric value, always */
font-variant-numeric: tabular-nums;

/* Role / state badges already in index.css */
--role-owner          /* purple/violet — authority signal */
--role-admin          /* warm amber — elevated access */
--state-unidentified  /* orange — pending merge */
--tier-1 .. --tier-6  /* dunbar tier ramp (5/15/50/150/500/1500) */
```

### What this design system says NO to, hard

- Cards. We use rule-rows. Hairlines, not boxes.
- Gradients, glassmorphism, drop shadows.
- Emoji anywhere. Even on empty states.
- Italic-serif headlines as branding. ("Welcome, *Tze*")
- Animating numbers from zero on load.
- Onboarding tour tooltips on familiar pages.
- Decorative SVG illustrations. Use placeholders and ask for assets.

### What it says YES to

- Type and hairlines as hierarchy.
- Mono eyebrows (10 px, uppercase, 0.14 em tracking).
- Serif italic, one-sentence empty states.
- Tabular-nums on every number.
- Commit buttons (filled, used at most once per surface) for primary
  actions; pill buttons (hairline, mono) for everything else.

---

## Backend / API — what exists and what to add

Working assumption: there is an RDF-style triple store backing both
entities and memory. Many of the endpoints below probably already exist;
where I'm uncertain, the prompt for the relevant page will spell out
what's needed. Treat this as a checklist to confirm against the real
backend before writing UI.

### Likely existing

```
GET  /api/entities                       list, paginated, ?type=&state=&q=
GET  /api/entities/:id                   one entity + immediate relations
GET  /api/entities/:id/activity          time-ordered events touching the entity
GET  /api/social-map                     Dunbar tiers around owner
POST /api/entities/:id/merge             merge two entities
DELETE /api/entities/:id                 forget (with tombstone)
```

### Likely needed for this redesign

```
GET  /api/entities/queue                 union of {unidentified, duplicate-candidate, stale};
                                          sorted by recency + severity. Right-rail data.
GET  /api/entities/:id/contacts          all contact-facts for one entity, grouped by predicate.
                                          Multi-valued; each row has {value, conf, verified,
                                          primary?, src, lastSeen}.
POST /api/entities/:id/contacts          add / verify / unverify a contact-fact.
                                          Body: {pred, value, primary?, verified?}
DELETE /api/entities/:id/contacts/:pred/:valueHash

GET  /api/entities/:id/neighbours        what /entities/hop renders. Returns the entity plus
                                          its direct adjacencies grouped by predicate, with
                                          weight + last-seen + provenance. The frontend never
                                          builds an adjacency list from raw triples — it asks.
GET  /api/entities/:id/columns?path=…    cascading-drill helper; returns the next-column
                                          payload given the path of (entityId, predicate?) hops.
                                          Optional: the frontend can also do this client-side
                                          by chaining /neighbours calls.

GET  /api/concentration?pred=…           bipartite weight rollup for one predicate, anchored
                                          to the owner. Returns rows of {entityId, weight,
                                          share, lastSeen}, sorted weight-desc, with total
                                          and top-3-share precomputed.

GET  /api/search?q=…&kinds=…             app-wide finder backend. Fuzzy across entity names,
                                          aliases, contact-facts, predicate labels. Returns
                                          {kind, id, label, score, matchedOn, preview}. The
                                          Finder is the only surface that hits this; everything
                                          else queries the typed endpoints above.

POST /api/entities                       create a new entity (used by "promote" action on an
                                          unidentified row).
POST /api/entities/:id/promote-tier      manual Dunbar override → +1 / -1
POST /api/entities/:id/archive           soft-archive (drop from default lists; keep triples)
POST /api/entities/queue/dismiss         mark a queue item as ignored (for unidentified) without
                                          merging or creating.
```

### Provenance contract

Every relation and every contact-fact carries:

- `src`: which butler wrote it (`relationship | household | calendar |
  memory | chronicler | qa | contact`)
- `conf`: 0..1 — confidence the system has in this fact
- `lastSeen`: most recent evidence timestamp
- `weight`: number of supporting facts (touches, receipts, mentions)
- `verified`: bool — owner has confirmed (contact-facts only)
- `primary`: bool — display preference among multi-valued contact-facts

This contract is what makes the workbench detail view honest. Don't drop
fields silently in the UI even when the design hides them by default.

---

## What ships in what order

```
phase 1   00 foundation       data model + API confirmation + types
          01 index            replaces existing /entities, with queue rail
          07 finder           app-wide Cmd-K / spotlight

phase 2   05 detail editorial replaces existing /entities/:id

phase 3   02 hop              new sub-route
          03 columns          new sub-route
          06 detail workbench toggle on detail

phase 4   04 concentration    new sub-route
```

Hop is the alternate-view that pays back fastest; build it before the
others if you have time in phase 1. Concentration last because it shares
no primitives — it's a chart, not a graph navigator.

---

## Five things I (the designer) would push you on in code review

1. **Empty states are one serif-italic sentence, no period of
   explanation.** "Nothing waiting." — not "You don't have any pending
   merges at this time. Items will appear here when…".

2. **The right rail's queue is the only place state colour appears on
   `/entities`.** If you find amber leaking into the index rows, you've
   overdesigned. The pill exists; the row stays neutral.

3. **Multi-valued contact-facts must show the count.** Never collapse to
   "the email" when there are two. A person with three phones has three
   rows, with the primary first.

4. **Forgetting is a first-class action.** Surface it on every entity
   detail page, with a serif gloss explaining what tombstones and what
   stays. Don't bury it in a kebab.

5. **No `padding: 24px` on a list item.** That's card thinking. Vertical
   row padding is 8–18 px depending on importance.

---

## Open questions for the designer (i.e. for me, in a follow-up turn)

These came up while writing this pack and are worth confirming before
production code:

- **Social Map** is left untouched in this redesign. If the existing
  page is in good shape, leave it. If it's stale, propose a refresh as
  a separate piece of work — the Dunbar circle is canonically about
  intimacy, and Hop is canonically about exploration.
- **The Workbench detail view** is a power-user toggle, not a replacement
  for Editorial. The toggle pattern (icon button in the page header, or
  a query param `?mode=workbench`) is open.
- **Bulk merge UX** for "I just imported 800 contacts and now have 200
  unidentified rows" — the Index covers single-row merge; a bulk flow
  may need its own surface. Out of scope for this pack, worth noting.
