# Vision — entity redesign

## Problem being solved

Today the Butlers app fragments the "people and things I care about" surface across four pages: `/entities` (flat list), `/entities/social-map` (Dunbar circles), `/entities/:id` (detail folded back from `/butlers/relationship/...`), and a separate `/contacts` page. Contacts are stored as a different noun than entities even though, semantically, a phone number is just a multi-valued predicate on a person. The list page is not the home for any actual workflow — exploration (re-centre on someone, drill through cascades, see weight rollup) all require leaving it. Curation (unidentified rows, duplicate candidates, stale entities) has no surface at all; the owner cannot see what needs their attention. This redesign collapses the surface into one home, folds contacts into predicates, and makes curation visible.

## Primary audience

**Owner (v1).** Single-user power tool. The owner is the only consumer of `/entities`. No multi-tenant or external-user accommodations.

## Deliberate design moves

- **The list is the home.** Hop, Columns, Concentration are alternate _views_ of the same population, not separate products. `/entities` (tabular index + curation queue) is the landing.
  - _Why:_ Mode-switching is cheap; navigating between products is expensive. The owner stays anchored in one place.
- **Contacts are predicates, not a noun.** `/contacts` becomes a 301 to `/entities?has=contact`. Emails/phones/handles/addresses are `has-email`, `has-phone`, etc. — multi-valued literal predicates on an entity.
  - _Why:_ The historical separation was a storage artifact, not a model truth. Folding them collapses one whole product surface.
- **Curation queue lives in the right rail of the Index.** Single endpoint (`GET /api/entities/queue`) returns `unidentified`, `duplicate-candidate`, `stale`. State colour appears only in this rail; index rows stay neutral.
  - _Why:_ The owner needs to see what's broken without leaving home. Colour leaking into rows = overdesigned.
- **Every fact carries provenance.** `src` (butler that wrote it), `conf` (0..1), `lastSeen`, `weight`, `verified`, `primary`. The model never drops these even when the Editorial view hides them. Workbench surfaces them; Finder ranks by them.
  - _Why:_ Honesty in the data layer; flexibility in presentation. Two detail views (Editorial default, Workbench toggle) read the same record.
- **Editorial + Workbench as one page, two affordances.** Default detail page is editorial (calm, hides provenance). A toggle (icon in header or `?view=workbench`) surfaces every metadata column.
  - _Why:_ 90% of detail visits are reading, not editing. The 10% power user gets the dense form without burdening the 90%.
- **App-wide Cmd-K Finder.** Single entry point that searches across entity names, aliases, contact-facts, predicate labels. Resolves to entities first; eventually any record.
  - _Why:_ Direct lookup beats navigation. The Finder is the only surface that hits `/api/search`; everything else uses typed endpoints.
- **Dispatch design language.** Five inviolable rules: composure is the brand; type is the system; surfaces, not cards; every element earns its place against state; one affordance per signal.
  - _Why:_ The current Butlers app already uses shadcn + Tailwind tokens. Dispatch is the disciplined application of those tokens, not a new framework.

## What we are deliberately NOT doing

- **No cards.** Rule-rows with hairlines; never `padding: 24px` on a list item.
- **No gradients, glassmorphism, drop shadows.**
- **No emoji anywhere** — even on empty states.
- **No italic-serif headlines as branding** ("Welcome, *Tze*"). Serif italic is reserved for empty-state glosses and LLM voice lines.
- **No number-animating-from-zero on load.** Tabular-nums always.
- **No onboarding tooltips** on familiar pages.
- **No decorative SVG illustrations** — placeholders only, ask for assets if needed.
- **No hue from entity type.** The entity-mark glyph (`P / O / L / X / @ / E / G`) carries type; hue stays neutral.
- **No hardcoded predicate IDs** outside `entity-model.ts` and the predicate-catalog UI.
- **Never collapse multi-valued contact-facts.** Three phones = three rows, primary first. Never "the email" when there are two.
- **Never bury "forget" in a kebab.** Forgetting is first-class with a serif gloss explaining what tombstones vs. what stays.
- **No Social Map changes in this pass.** Dunbar circles untouched; if stale, propose a refresh as separate work.
- **No bulk merge UX** (e.g. "I imported 800 contacts, now have 200 unidentified"). Out of scope; flagged for later.

## Success criteria

- **Owner can clear the curation queue without leaving `/entities`.** Unidentified → promote/dismiss/merge inline; duplicate-candidate → merge inline; stale → archive or refresh inline.
- **`/contacts` removal causes zero functional regression.** The `has=contact` filter chip on the Index covers every prior `/contacts` workflow; 301 redirects keep old links alive.
- **A person with three emails shows three rows everywhere** — Editorial detail, Workbench detail, Finder previews. Never collapsed.
- **Forget is one click from any entity detail page**, with a one-sentence serif gloss before confirm. Not buried in a menu.
- **Cmd-K opens from any page, returns ranked entity results in <300ms** for the local dataset; type-to-search resolves names, aliases, and contact-fact values.
- **The Index right rail never shows a count of zero** — when the queue is empty, the rail collapses to a single serif-italic line ("Nothing waiting.").
- **No state colour leaks into Index rows.** Amber appears only in the queue rail; entity rows stay neutral hairline-on-neutral.
- **Hop, Columns, Concentration are reachable from `/entities` in one click** (tab or pill), and re-centre on any entity returns to `/entities/hop` not a different product surface.
