# entity redesign — integration brief

**Date:** 2026-05-17
**Version:** v1
**Bundle path:** `pr/overview/entity-redesign/`
**Mode:** fresh
**Phase D verdict:** clear (proceed with two anti-temptation guardrails embedded in spec)
**Prior brief (if any):** None
**Bundle structure note:** Non-canonical layout. `README.md` replaces `IMPLEMENTATION.md`; per-page recipes live under `prompts/00-foundation.md` … `prompts/07-finder.md`; `DESIGN_LANGUAGE.md` lives at `reference/DESIGN_LANGUAGE.md`, not the bundle root; fixtures at `reference/prototype/data.jsx`. Treat README + prompts/00 as the porting recipe.

---

## 0. Design intent

> Captured from the bundle's own articulation (README + prompts/00-foundation.md) and confirmed with the user during Phase 0.5. **This section is binding — every spec section, every component decision, every backend contract must trace back to it.** Phase D treats violations of intent as automatic red regardless of cost math.
>
> Persisted to `pr/overview/entity-redesign/VISION.md` so the next iteration skips Phase 0.5.

### Problem being solved

Today the Butlers app fragments the "people and things I care about" surface across four pages: `/entities` (flat list), `/entities/social-map` (Dunbar circles), `/entities/:id` (detail folded back from `/butlers/relationship/...`), and a separate `/contacts` page. Contacts are stored as a different noun than entities even though, semantically, a phone number is just a multi-valued predicate on a person. The list page is not the home for any actual workflow — exploration (re-centre on someone, drill through cascades, see weight rollup) all require leaving it. Curation (unidentified rows, duplicate candidates, stale entities) has no surface at all; the owner cannot see what needs their attention. This redesign collapses the surface into one home, folds contacts into predicates, and makes curation visible.

### Primary audience

**Owner (v1).** Single-user power tool. The owner is the only consumer of `/entities`. No multi-tenant or external-user accommodations.

### Deliberate design moves

- **The list is the home.** Hop, Columns, Concentration are alternate _views_ of the same population, not separate products. `/entities` (tabular index + curation queue) is the landing.
  - _Why:_ Mode-switching is cheap; navigating between products is expensive. The owner stays anchored in one place.
- **Contacts are predicates, not a noun.** `/contacts` becomes a 301 to `/entities?has=contact`. Emails/phones/handles/addresses are `has-email`, `has-phone`, etc. — multi-valued literal predicates on an entity.
  - _Why:_ The historical separation was a storage artifact, not a model truth. Folding them collapses one whole product surface.
- **Curation queue lives in the right rail of the Index.** Single endpoint (`GET /api/entities/queue`) returns `unidentified`, `duplicate-candidate`, `stale`. State colour appears only in this rail; index rows stay neutral.
  - _Why:_ The owner needs to see what's broken without leaving home. Colour leaking into rows = overdesigned.
- **Every fact carries provenance.** `src` (butler that wrote it), `conf` (0..1), `lastSeen`, `weight`, `verified`, `primary`. The model never drops these even when the Editorial view hides them. Workbench surfaces them; Finder ranks by them.
  - _Why:_ Honesty in the data layer; flexibility in presentation. Two detail views (Editorial default, Workbench toggle) read the same record.
- **Editorial + Workbench as one page, two affordances.** Default detail page is editorial (calm, hides provenance). A toggle (icon in header or `?mode=workbench`) surfaces every metadata column.
  - _Why:_ 90% of detail visits are reading, not editing. The 10% power user gets the dense form without burdening the 90%.
- **App-wide Cmd-K Finder.** Single entry point that searches across entity names, aliases, contact-facts, predicate labels. Resolves to entities first; eventually any record.
  - _Why:_ Direct lookup beats navigation. The Finder is the only surface that hits `/api/search`; everything else uses typed endpoints.
- **Dispatch design language.** Five inviolable rules: composure is the brand; type is the system; surfaces, not cards; every element earns its place against state; one affordance per signal.
  - _Why:_ The current Butlers app already uses shadcn + Tailwind tokens. Dispatch is the disciplined application of those tokens, not a new framework.

### What we are deliberately NOT doing

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

### Success criteria

- **Owner can clear the curation queue without leaving `/entities`.** Unidentified → promote/dismiss/merge inline; duplicate-candidate → merge inline; stale → archive or refresh inline.
- **`/contacts` removal causes zero functional regression.** The `has=contact` filter chip on the Index covers every prior `/contacts` workflow; 301 redirects keep old links alive.
- **A person with three emails shows three rows everywhere** — Editorial detail, Workbench detail, Finder previews. Never collapsed.
- **Forget is one click from any entity detail page**, with a one-sentence serif gloss before confirm. Not buried in a menu.
- **Cmd-K opens from any page, returns ranked entity results in <300ms** for the local dataset; type-to-search resolves names, aliases, and contact-fact values.
- **The Index right rail never shows a count of zero** — when the queue is empty, the rail collapses to a single serif-italic line ("Nothing waiting.").
- **No state colour leaks into Index rows.** Amber appears only in the queue rail; entity rows stay neutral hairline-on-neutral.
- **Hop, Columns, Concentration are reachable from `/entities` in one click** (tab or pill), and re-centre on any entity returns to `/entities/hop` not a different product surface.

---

## 1. Scope

This redesign restructures the entity surface area of the Butlers dashboard: it replaces `/entities` (flat list), `/entities/:id` (detail), and `/contacts` (separate index + detail) with a single `/entities` home that hosts a tabular index + curation queue, three new sub-routes (`hop` / `columns` / `concentration`), an Editorial+Workbench detail page, and an app-wide Cmd-K Finder. `/entities/social-map` is preserved unchanged in this pass. The design language is **Dispatch** — a disciplined application of the existing shadcn + Tailwind token set in `frontend/src/index.css` (hairlines not cards; type as hierarchy; one affordance per signal). The integration target is the live React+TypeScript dashboard at `/home/tze/gt/butlers/frontend/`.

### Sub-pages

| Route | Source file(s) | Purpose (one sentence) | Sticky-nav parent? |
|-------|---|---|---|
| `/entities` | `exp-index.jsx`, `SubpageTabs` in exp-index.jsx | Tabular list with curation queue; default landing for all entity surfaces | Yes (in SubpageTabs) |
| `/entities/hop` | `exp-hop.jsx`, `SubpageTabs` in exp-hop.jsx | Re-centre graph explorer with predicate-grouped neighbour fan-out | Yes (in SubpageTabs) |
| `/entities/columns` | `exp-columns.jsx`, `SubpageTabs` in exp-columns.jsx | Finder-style cascading column drill, predicate-grouped | Yes (in SubpageTabs) |
| `/entities/concentration` | `exp-concentration.jsx`, `SubpageTabs` in exp-concentration.jsx | Balance-sheet view of weight by predicate (tabs flip predicate) | Yes (in SubpageTabs) |
| `/entities/social-map` | referenced in SubpageTabs, app.jsx line 87; exp not provided | Existing Dunbar concentric circles; kept unchanged | Yes (in SubpageTabs) |
| `/entities/:id` | `exp-detail.jsx` (lines 109–245 Editorial + Workbench) | Per-entity detail page; two toggles: Editorial (narrative dispatch) vs Workbench (power-user console) | No |
| `(global) /` or `Cmd-K` | `exp-command.jsx` | App-wide spotlight finder; fuzzy-search entities, aliases, predicates; keyboard-driven | No |

**Open:** Social Map's `exp-*.jsx` is not in the bundle; only referenced in README line 35 and SubpageTabs.

### Design tokens (binding)

#### Color

**Surfaces** (dark canonical; light is oklch warm hue 85):
- `--bg` oklch(0.145 0 0) — page background
- `--bg-elev` oklch(0.205 0 0) — elevated surfaces (code blocks, tooltips, Columns owner column)
- `--bg-deep` oklch(0.115 0 0) — sidebar, sticky bars
- `--fg` oklch(0.985 0 0) — primary text, active states
- `--mfg` oklch(0.708 0 0) — muted text, eyebrows, secondary labels
- `--dim` oklch(0.55 0 0) — tertiary text, deltas, footnotes
- `--border` oklch(1 0 0 / 0.10) — standard hairline rules
- `--border-soft` oklch(1 0 0 / 0.06) — subtle separators (list rows)
- `--border-strong` oklch(1 0 0 / 0.18) — buttons, link underlines

**State** (sparingly; foreground/border only, never fill):
- `--red` oklch(0.685 0.250 29.2) — blocker, reauth, forget, error
- `--amber` oklch(0.810 0.185 84.0) — unidentified, duplicate-candidate, unverified, degraded
- `--green` oklch(0.790 0.195 148.2) — healthy, positive delta

**Butler category hues** (letter-mark only; NEVER borders/backgrounds/text): `--category-1..8` tokens map 1:1 to butlers; EntityMark uses `typeColor()` lookup.

**Tier ramp** (Dunbar): `--tier-1..6` coloured dots in TierBadge (progressively cooler; six layers: 5/15/50/150/500/1500).

#### Typography

- **Families:** `Inter Tight` (sans / UI), `Source Serif 4` (serif / voice & glosses), `JetBrains Mono` (mono / times, IDs, deltas, eyebrows).
- **Scale:** Display 44px/500/−0.025em/lh 1.08 · Title 24px/500/−0.015em/lh 1.2 · Body 14px/400 · Body small 13px/400 · Voice 16px/400 serif/lh 1.6 · Eyebrow 10px mono 0.14em uppercase · Mono inline 11px/lh 1.4.
- **Numerals:** every numeric value, always `font-variant-numeric: tabular-nums`.
- **Eyebrow rule:** 10px / mono / uppercase / 0.14em / `--mfg`. Used in lieu of visual separators.

#### Spacing & rhythm

- **Base unit:** 4px (all values multiples of 4).
- **Page padding:** 48px top/bottom, 56px left/right.
- **Section gutter:** 56px.
- **Two-column editorial grid:** `1.4fr 1fr; gap 56px`.
- **Row vertical padding:** 8–18px depending on importance. **Never 24px (card thinking).** Attention rows 18px; index/queue rows 10px.
- **Line height:** body 1.5, voice 1.6, display 1.08, title 1.2.

#### Motion

- **Allowed (sparingly):** briefing fade 200ms cubic-bezier(0.22,1,0.36,1); sidebar chevron 120ms linear; theme toggle 200ms ease; row hover 80ms linear; tooltip 0ms (instant).
- **Forbidden:** spring physics, bounce, parallax, scale-in, scale-on-hover, shimmer, skeleton-pulse, count-up.

#### Hard "do not" list

Cards · gradients · glassmorphism · drop shadows · emoji · italic-serif as branding · animating numbers from zero · onboarding tour tooltips · decorative SVGs · hue on entity type (only on letter-mark) · hardcoded predicate IDs outside the catalog · collapsing multi-valued contact-facts · burying "forget" in a kebab · 24px padding on list items · two affordances per signal.

---

## 2. Component impact

### Classification table

| Component | Verdict | Reuse target | Churn | Notes |
|-----------|---------|--------------|-------|-------|
| Eyebrow | adapt | `frontend/src/index.css` className `eyebrow` or inline | S | Dispatch spec: 10px mono uppercase 0.14em. Use `font-mono text-xs uppercase tracking-widest`. |
| Voice | adapt | shadcn typography utilities | S | Serif paragraph (Source Serif 4, 16px, 1.6 lh, max-width 64ch). Add `Voice` wrapper in `frontend/src/components/ui/`. |
| Display | adapt | shadcn h1 + Tailwind | S | 44px sans 500, -0.025em, 1.08 lh. New `Display.tsx`. |
| Title | adapt | shadcn h2 + Tailwind | S | 24px sans 500, -0.015em, 1.2 lh. New `Title.tsx`. |
| EntityMark | new | Depends on Dispatch type-glyph catalog | M | atoms.jsx:73–101. Current `ButlerMark` at `frontend/src/components/ui/ButlerMark.tsx:139` is for butlers, not entities. Build new EntityMark with tone (fill/neutral), size, person initials vs. org/place/product glyph, ownership/state borders. |
| TierBadge | new | --tier-1..6 tokens + EntitiesPage.tsx:100–115 inline `dunbarTierBadgeStyle()` | S | Extract inline style into reusable `TierBadge` component. Mono 9px uppercase + 6px coloured dot. |
| StateDot | new | Could reuse toast/status structure | S | 6px coloured circle. Minimal use; build as primitive. |
| Row | adapt | shadcn table or div grid | M | Dispatch grid-row (3-col: left/mid/right, hairline bottom, padding 8–18px). Current EntitiesPage uses `<table>`; build flexible `Row` wrapper in `frontend/src/components/ui/Row.tsx` accepting slots. |
| Pill | adapt | shadcn badge | S | Mono toggle pill. Use existing `frontend/src/components/ui/badge.tsx` or add Pill variant. |
| Section | new | Dispatch layout primitive | M | Frame with eyebrow/title/lede slots. New `Section.tsx`. |
| SubpageTabs | new | shadcn tabs or custom nav | M | Horizontal nav strip (Index/Hop/Columns/Concentration/Social-map). Wraps React Router links. Build new. |
| StatePill | adapt | shadcn badge + Dispatch state colors | S | Coloured outline pill (unidentified/duplicate/stale/unverified). Use existing Badge or `StatePill` variant. Apply --amber / --red borders. |
| QueueCard | new | shadcn Card + custom content | M | Three-variant (unidentified / duplicate / stale) with action buttons. Reuse Card/CardContent shells; remove shadows; hairline border; monochrome bg. |
| CommitBtn | new | shadcn button + Dispatch styling | S | High-contrast filled (`bg-fg text-bg`); danger variant red. |
| Tick | new | shadcn Checkbox | S | 14px hairline. Check existing `frontend/src/components/ui/checkbox.tsx:21` matches. |
| ActivitySpark | new | recharts BarChart or custom SVG | M | 360×56px, 90-day touches. Monochrome. recharts already in deps (package.json:38). |
| BreadcrumbStrip | new | shadcn breadcrumbs or custom | M | Jump breadcrumb. Check `frontend/src/components/ui/breadcrumbs.tsx:19`. |
| KbMono | new | Custom span | S | Keyboard shortcut capsule; mono, small padding, hairline border. |

### Stack delta

- **No new npm dependencies.** cmdk 1.1.1, recharts 3.7.0, all shadcn primitives already installed (package.json:19–40).
- **Routing changes:** Add new sub-routes under `/entities` in `frontend/src/router.tsx`:
  - `/entities/hop` → new `HopPage` component
  - `/entities/columns` → new `ColumnsPage` component
  - `/entities/concentration` → new `ConcentrationPage` component
  - Wrap in `EntitiesLayout` for nested-route shared chrome (SubpageTabs).
  - Effort: **S** (mirror existing nested-route pattern).
- **Design tokens:** Verify Dispatch oklch values match `frontend/src/index.css` (lines 121–186). Existing tokens (`--bg`, `--fg`, `--red`, `--amber`, `--green`, `--category-*`, `--tier-*`, `--role-owner`, etc.) align. **Action:** Confirm fonts (`Inter Tight`, `Source Serif 4`, `JetBrains Mono`) are loaded — currently not visible in sampled index.css. Add via `@import` or `<link>` in `frontend/index.html` or main CSS. Effort: **S**.
- **State:** No new library. TanStack Query already in place.
- **Routing for /contacts fold-in:** Add 301 redirect `/contacts → /entities?has=contact`. Update `frontend/src/components/layout/nav-config.ts` to drop Contacts item and add filter chip. Update CommandPalette result handling. Effort: **S**.
- **No blockers.** No stack change breaks pages outside `/entities`.

---

## 3. Backend contract delta

### Affordance inventory

| Affordance | Sub-page(s) | Data needed (fields) | Source of fixture (if any) |
|---|---|---|---|
| QueueCard | `/entities` (right rail) | `state`, `reason` (duplicate), `score` (duplicate), `name`, `lastSeen` | `reference/prototype/data.jsx`: `ENTITIES[state]`, `CONTACT_FACTS` |
| Row (entity index) | `/entities` | `id`, `type`, `name`, `tier`, `lastSeen`, `aliases` | `ENTITIES[]` |
| Row (with contacts pill) | `/entities?has=contact` | contact-predicate count, `verified` count | `contactsFor()` + `CONTACT_FACTS` |
| Pill (state filter) | `/entities` | unidentified count, duplicate count, stale count | `ENTITIES[state]` filter |
| Hop graph (neighbours fan) | `/entities/hop` | `id`, `name`, `type` for center + neighbours; `predicate`, `weight`, `lastSeen` per edge | `ADJ` adjacency builder + `RELATIONS` |
| Hop detail pane | `/entities/hop` right panel | Full entity detail (contacts + relations) | Same as entity detail |
| Columns cascade header | `/entities/columns` | Entity at each level + predicate label + entity count | Cascading `neighbours` calls |
| Concentration row | `/entities/concentration?pred=X` | `entityId`, `weight`, `share`, `lastSeen`, predicate label | `RELATIONS` filtered by predicate |
| Concentration rollup | `/entities/concentration?pred=X` | `total`, `top3Share` (precomputed) | `RELATIONS` aggregation |
| Detail editorial (right pane) | `/entities/:id` | Entity + contacts (grouped by pred) + relations (grouped by pred) + `verified` flags | `ENTITY_INDEX`, `CONTACT_FACTS`, `RELATIONS` |
| Detail workbench toggle | `/entities/:id?mode=workbench` | Same as editorial + `conf`, `src`, `weight` unhidden | `ENTITY_INDEX`, `CONTACT_FACTS`, `RELATIONS` + `meta` fields |
| Finder results | App-wide Cmd-K | `kind` (entity vs other), `id`, `label`, `score`, `matchedOn` | Fuzzy match over `ENTITIES` + `CONTACT_FACTS` + `PREDICATES` |
| Finder detail preview | Cmd-K result detail | Entity summary (name, type, tier, lastSeen, top 2 contacts) | Entity + `contactsFor()` call |

### API delta

| Path | Method | Status | Existing handler | Request shape | Response shape | Evidence | Drives affordance(s) |
|---|---|---|---|---|---|---|---|
| `/api/entities` | GET | **new** | None | `?type=&state=&q=&has=contact&cursor=` | `{ items: Entity[], nextCursor?: string, total: int }` | fixture | Row (entity index); Pill filtering |
| `/api/entities/queue` | GET | **new** | None | (none) | `{ unidentified: Entity[], duplicateCandidates: Array<{a, b, reason, score}>, stale: Entity[] }` | fixture | QueueCard |
| `/api/entities/:id` | GET | **exists** | `roster/relationship/api/router.py:2309` | (none) | `EntityDetail` (current model) | live-endpoint | Detail editorial + workbench |
| `/api/entities/:id/contacts` | GET | **new** | None | (none) | `{ [predicate]: Array<{value, conf, src, verified, primary?, lastSeen?}> }` | fixture | Detail editorial contacts; Workbench |
| `/api/entities/:id/contacts` | POST | **new** | None | `{ pred, value, verified?, primary? }` | created contact-fact row | fixture | Editorial add-contact |
| `/api/entities/:id/contacts/:pred/:valueHash` | DELETE | **new** | None | (none) | 204 | fixture | Editorial delete-contact |
| `/api/entities/:id/neighbours` | GET | **new** | None | (none) | `{ entity, relations: Array<{predicate, direction, targets: Array<{entity, meta}>}> }` | fixture | Hop graph; Columns cascade |
| `/api/entities/:id/columns?path=` | GET | **unclear** | None | `?path=entity1,pred1,entity2,pred2` | next-column payload | fixture | Columns cascade navigation |
| `/api/concentration?pred=` | GET | **new** | None | `?pred=knows\|purchased-from\|...` | `{ predicate, rows: [{entityId, weight, share, lastSeen}], total, top3Share }` | fixture | Concentration rows + rollup |
| `/api/search?q=&kinds=` | GET | **new** | None | `?q=<query>&kinds=person,organization,...` | `{ results: Array<{kind, id, label, score, matchedOn, preview?}> }` (max 20) | fixture | Finder results |
| `/api/entities` | POST | **new** | None | `{ name, type, category? }` | `{ id, name, type, created_at }` | fixture | Promote unidentified |
| `/api/entities/:id/promote-tier` | POST | **new** | None | `{ delta: 1 \| -1 }` | `{ tier: 0..5, overridden: bool }` | fixture | Detail Dunbar +/- |
| `/api/entities/:id/archive` | POST | **new** | None | (none) | 204 | fixture | Index bulk archive |
| `/api/entities/:id/merge` | POST | **extend** | `roster/relationship/api/router.py:1660` (contact-level merge) | Verify entity-level shape: `{ entityA: uuid, entityB: uuid, keepAs: uuid }` | merged entity + deleted entity ID | live-endpoint (contact merge); spec clarification needed for entity-level | Index + Detail merge |
| `/api/entities/:id` | DELETE | **new** | None | (none) | 204 (tombstone) | fixture | Index forget + Detail forget |
| `/api/entities/queue/dismiss` | POST | **new** | None | `{ entityId }` | 204 | fixture | Queue dismiss |
| `/api/entities/:id/activity` | GET | **extend** | `roster/relationship/api/router.py:2838` (`/timeline`) | Verify field names; may need `src`, `confidence`, `predicate` added. **Aggregator pattern:** internally calls chronicler MCP tools (`chronicler_list_events` / `chronicler_list_episodes`) for chronicler-authored rows. Never direct SQL into `chronicler.episodes`. | `Array<{timestamp, event, src, predicate?, entities?, via?}>` | live-endpoint | Detail editorial activity |

> **Evidence column rule:** every `fixture`-only row is `status: new` or `unclear`. `/project-direction` Phase 2 must resolve all `fixture` rows against either a live endpoint or a new spec before committing the spec.

### Schema migration impact

**New tables (all under `relationship` butler schema):**

1. **`relationship.entity_state_flags`** (or extend `public.entities.metadata` JSONB)
   - For curation queue: `(entity_id, state: enum('unidentified', 'duplicate-candidate', 'stale'), last_seen, created_at)`
   - Index: `(state, last_seen DESC)` for queue ordering; `(last_seen ASC)` for stale-first sort.
   - Migration type: add columns OR extend metadata JSON schema.

2. **`relationship.contact_facts`** (new if not present)
   - Columns: `(id uuid, entity_id uuid, predicate text, value text, conf float, src text, verified bool, primary bool, last_seen timestamp, created_at, updated_at)`
   - Unique: `(entity_id, predicate, value)`.
   - Indexes: `(entity_id, predicate)`, `(last_seen DESC)`.
   - Migration type: **new table**. Verify whether contact facts currently live in `entity_info` or separate structure; redesign separates them into their own model.

3. **`relationship.relations`** (new if not present)
   - Columns: `(id uuid, subject uuid, predicate text, object uuid, conf float, src text, weight int, last_seen timestamp, direction enum, created_at, updated_at)`
   - Unique: `(subject, predicate, object)`.
   - Indexes: `(subject, predicate)`, `(object, predicate)`, `(predicate)`.
   - Migration type: **new table**. Verify whether relations are persisted today or derived on-the-fly; RDF storage unlocks Hop/Concentration efficiency.

**Pre-warming indexes:** see per-table list above. Plus `public.entities(type, last_seen DESC)`, `public.entities(last_seen DESC)` for list filtering.

**Provenance fields must exist on:** `contact_facts.conf/.src/.verified/.primary/.last_seen`; `relations.conf/.src/.weight/.last_seen`. If missing on existing fact storage, add via migration. **Do not silently omit in API responses** — Section 0 binds this.

**Cross-butler concerns (schema isolation):**
- `/api/search` fuzzy match across entities + aliases + contact-facts + predicates → all live in `relationship` schema. No cross-schema read.
- Dunbar tier calc — uses `knows` + `co-attended` + `visited` edges. If calendar/chronicler contributions are needed live, must go through MCP/Switchboard, not direct DB.
- Provenance `src` field is just a string; no enforcement needed.

**Data migration checklist:**
- [ ] Migrate existing `entity_info` → `contact_facts` if contacts live there today.
- [ ] Migrate existing relations (if any) → `relations` table with direction encoded.
- [ ] Populate `entity_state_flags` from `metadata.state` on existing entities.
- [ ] Verify `lastSeen` is populated and indexed on all migrated rows.

### Proposed backend epic

**Epic title:** `entity redesign — backend contracts`

**Rationale:** Land the foundation (tables, indexes, types) and all read/write endpoints to unblock frontend implementation of Index, Hop, Columns, Finder, Detail editorial, Detail workbench.

**Child beads:**

1. **Schema: entity_state_flags (or extend metadata)** — S. Verify current storage in `public.entities`. Depends: none. Unblocks: queue endpoint.
2. **Schema: contact_facts table** — M. Unique + grouping indexes. Depends: none. Unblocks: contacts endpoints.
3. **Schema: relations table** — M. Triple-store with provenance. Depends: none. Unblocks: neighbours, concentration, hop.
4. **Data: migrate entity_info → contact_facts** — L. Preserve verified/primary/src. Depends: 2. Unblocks: 6.
5. **Data: migrate relations** — L. Depends: 3. Unblocks: 6.
6. **Endpoint: GET /api/entities (list + filter + pagination)** — M. Depends: 1. Unblocks: frontend Index.
7. **Endpoint: GET /api/entities/queue** — M. Depends: 1, 2, 3. Unblocks: queue rail.
8. **Endpoint: GET /api/entities/:id/contacts** — S. Depends: 2. Unblocks: Editorial contacts section.
9. **Endpoints: POST/DELETE /api/entities/:id/contacts** — S. Depends: 2. Unblocks: Editorial contact actions.
10. **Endpoint: GET /api/entities/:id/neighbours** — M. Depends: 3. Unblocks: Hop view.
11. **Endpoint: GET /api/concentration?pred=** — M. Depends: 3. Unblocks: Concentration view.
12. **Endpoint: GET /api/search?q=&kinds=** — M. Fuzzy across names/aliases/contact-facts/predicates (deterministic, no LLM). Depends: 2. Unblocks: Cmd-K Finder.
13. **Endpoints: POST /api/entities, POST /api/entities/:id/promote-tier, POST /api/entities/:id/archive, DELETE /api/entities/:id** — M. Depends: 1. Unblocks: Index + Detail actions.
14. **Endpoint: POST /api/entities/:id/merge (entity-level)** — M. Tombstone source via metadata. Depends: 3. Unblocks: Merge actions.
15. **Endpoint: POST /api/entities/queue/dismiss** — S. Depends: 1. Unblocks: Queue dismiss.
16. **Verify/extend: GET /api/entities/:id/activity (aggregator)** — S. Add `src`, `confidence`, `predicate` if missing. **Internal calls go via chronicler MCP tools, not direct SQL** (manifesto-preserving). Depends: none. Unblocks: Detail timeline.
17. **Endpoint: GET /api/entities/:id/columns?path=** — M *(unclear, see Open Questions)*. Cascade helper. Depends: 3. Unblocks: Columns view.

**Critical path (MVP):** 1 → 2 → 3 → 6 (list) → 7 (queue) → 8 (contacts) → 10 (neighbours/Hop) → 12 (search). Parallel: 4–5 (data migration). Then 9, 11, 13–15.

---

## 4. Guardrails

### LLM-cost feasibility

**Pricing source:** Cached from `.claude/skills/butlers-redesign-prompt/references/llm-pricing.md` (`last_verified: 2026-01`). Live fetch from anthropic.com/pricing failed (consumer-plan redirect / 404). Today is 2026-05-17 — rates are ~4 months stale. Sonnet 4.x $3/$15 per MTok; Haiku 4.x $1/$5; Opus 4.x $15/$75. If Anthropic has cut rates, verdicts are conservative; if rates have risen materially, Phase D escalates.

| Feature | Trigger model | tokens_in | tokens_out | Model class | $/call | Freq/user/day | $/user/day (v1: users=1) | $/user/day (users=100) | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| Detail Editorial "voice gloss" (`05 §5.2`) | **canned strings** via prototype `gloss(e)` switch on `(tier, state, category)` | 0 | 0 | n/a | $0 | 10 loads | $0 | $0 | **green** |
| Curation rail "forget" gloss (`05 §5.9`) | **static** "Forgetting also tombstones the source. Aliases stay." | 0 | 0 | n/a | $0 | per-click | $0 | $0 | **green** |
| Index empty-state ("Nothing waiting.") (`01 §1.x`, Section 0) | **static** serif italic line | 0 | 0 | n/a | $0 | per-render | $0 | $0 | **green** |
| Cmd-K Finder `/api/search?q=` (`07 §7.5`, `00 §API`) | **deterministic fuzzy** (prefix=100, substring=50, predicate=30, contact-fact=70) | 0 | 0 | n/a | $0 | ~50 keystrokes | $0 | $0 | **green** |
| `/api/entities/queue` aggregation (`00 §0.6`) | **deterministic** join + sort; "stale" is a `lastSeen` threshold | 0 | 0 | n/a | $0 | per-visit | $0 | $0 | **green** |
| `/api/entities/:id/neighbours`, `/columns`, `/concentration`, `/provenance` | **deterministic** triple-store queries with weight rollups | 0 | 0 | n/a | $0 | per-nav | $0 | $0 | **green** |
| Activity feed in Editorial (`05 §5.5`) | **reads** `chronicler.episodes` via MCP + existing activity rows; no summarization | 0 | 0 | n/a | $0 | per-load | $0 | $0 | **green** |
| Duplicate-candidate `reason` string (`00 §0.6`) | **deterministic** ("shared email · same employer" computed from join predicates) | 0 | 0 | n/a | $0 | per-render | $0 | $0 | **green** |

#### Red verdicts

**None.**

#### Recommended de-scopes before spec phase

**Nothing to de-scope.** Two anti-temptation guardrails must be embedded in the spec:

1. **Detail-page voice glosses are canned strings selected by `(tier, state, category)`.** No LLM call per page load. (Counter-temptation: if an implementer reaches for "let's just have Sonnet write one sentence per entity", v1 math is ~$0.12/user/day → yellow; at users=100 → $12/day → red. And it would arguably violate Section 0's "composure is the brand".)
2. **`/api/search` ranking is rule-based per `07 §7.5`.** No embedding service or reranker LLM in v1. (Counter-temptation: per-keystroke embedding rerank at 50 sessions × 8 keystrokes × Haiku reranker = $0.60/user/day → red.)

### Manifesto / identity preservation

> **Important correction:** Phase B's "Butlers touched" table named `memory`, `contact`, and `household` as butlers. Verification against the actual roster shows these are **modules**, not butlers:
>
> - `roster/memory/` does not exist; memory is a module loaded by relationship butler (`roster/relationship/butler.toml:116-117`).
> - `roster/contact/` does not exist; contacts is a module loaded by relationship (`butler.toml:106-114`) + shared identity tables in `public` schema (RFC 0004).
> - `roster/household/` does not exist; household functionality lives under the **home** butler (`roster/home/`), which is not touched by an entity redesign.
>
> **Real touched-butler set: `relationship` and `chronicler`.** Phase B's table propagated a five-butler fiction. The Phase D pass below reviews only the two real touched butlers.

| Butler | Manifesto file:line cited | What the redesign does that touches identity | Verdict | Specific drift (if any) |
|---|---|---|---|---|
| relationship | `roster/relationship/MANIFESTO.md:11-17` (Thoughtfulness/Richness/Connection); `:44-65` (Dunbar tiers, attention-inward, decay); `roster/relationship/CLAUDE.md:7-15` ("Data Model: Entity → Contact → Contact Details… Every known person/org is an entity") | Elevates entities as canonical noun; folds /contacts → /entities (predicates); keeps Dunbar tiers first-class (TierBadge, filter, tier-weighted concentration); surfaces curation queue; preserves "forget" as first-class with serif gloss. | **identity preserved** | None. Redesign aligns with declared "Entity → Contact → Contact Details" hierarchy (`CLAUDE.md:7-15`) and Dunbar tier philosophy (`MANIFESTO.md:46-65`). Contact-predicate fold-in matches `CLAUDE.md:11` ("Every contact MUST link to an entity. Facts MUST be stored on entities, not contacts"). |
| chronicler | `roster/chronicler/MANIFESTO.md:13-15` ("I am a butler, not a staffer. I read; I project; I preserve provenance"); `:35-37` ("I do not own the operational `/api/timeline`; I live at `/api/chronicler/*`"); `:40-42` ("I do not invoke an LLM per event. Routine projection is deterministic"); `CLAUDE.md:43-49` (Tier-2 paths bounded to day-close, drilldown, ambiguity, correction) | Uses `GET /api/entities/:id/activity` and `GET /api/entities/:id/spark?days=90` to populate Editorial activity feed and sparkline. The bundle specifies these as relationship-namespaced, not `/api/chronicler/*`. | **identity drift flagged (minor — namespace ambiguity)** | The redesign's `/api/entities/:id/activity` conflates (a) relationship-owned interactions (`interaction_log`) and (b) chronicler-owned episodes that mention the entity. If chronicler episodes are exposed via a relationship-owned route that joins chronicler tables directly, that's a soft inversion of `MANIFESTO.md:35-37`. **Reconciliation:** Spec must declare that `/api/entities/:id/activity` is a **relationship-owned aggregator** that internally calls chronicler MCP tools (`chronicler_list_events` / `chronicler_list_episodes`) with `entity_id` filter and tags each row with `src: 'chronicler'` (the bundle's `via:` column already accommodates this). No new route under `/api/chronicler/*` is needed; chronicler's existing read-tool surface is sufficient. |

#### Drift write-ups

**Chronicler — namespace ambiguity in `/api/entities/:id/activity`:**

The chronicler manifesto declares it "lives at `/api/chronicler/*`" (`MANIFESTO.md:37`). The bundle's Editorial detail page (`prompts/05-detail-editorial.md:196-211`) introduces `GET /api/entities/:id/activity` as a relationship-namespaced endpoint. If implementation has chronicler's tables joined directly into a relationship handler, that's a manifesto boundary violation. If the relationship butler calls chronicler MCP read tools and aggregates, the boundary is preserved.

**Concrete reconciliation for spec:**

```
/api/entities/:id/activity (relationship-owned aggregator) returns:
[
  { src: 'relationship', kind: 'interaction', date, summary, via: 'telegram', ... },
  { src: 'chronicler',   kind: 'episode',     date, summary, via: 'google_calendar', episode_id, ... },
  { src: 'chronicler',   kind: 'event',       date, summary, via: 'spotify', event_id, ... }
]
```

Chronicler rows MUST be fetched via chronicler butler's MCP tools, not via direct SQL into `chronicler.episodes`. The `src` field is what `05 §5.5`'s "via" column needs. Honors `chronicler/MANIFESTO.md:33` and `:40-42`.

#### Recommended manifesto updates

None required. Reconciliation is an implementation discipline, not a manifesto change.

**Documentation update worth doing (not a manifesto change):** Phase B's roster mismatch (treating modules as butlers) suggests `about/lay-and-land/` or the Phase B reference prompt should be updated to clarify the module-vs-butler distinction before the next redesign brief is written.

### Intent compliance

Cross-reference against Section 0 design intent:

- **No red LLM verdicts.** Nothing to escalate on cost grounds.
- **Chronicler drift:** Section 0's "every fact carries provenance (`src`, `conf`, `lastSeen`, `weight`, `verified`, `primary`)" is the *reason* the chronicler aggregator pattern works — the `src` field is already part of the binding model. The drift is a manifesto-preservation discipline, not a Section 0 contradiction. Verdict reinforced (not contradicted).
- **Two anti-temptation guardrails (canned glosses, deterministic Finder):** Both are tightly aligned with Section 0's "composure is the brand" / "every element earns its place against state" / "no number-from-zero animations / decorative SVGs / onboarding tooltips" temptations-list spirit. Verdict reinforced.

---

## 5. Open questions

Consolidated from Phases A–D. Numbered for `/project-direction` Phase 2 to resolve before locking specs.

1. **[Phase A · README.md:298-301]** Social Map is left untouched. If the existing `SocialMapPage.tsx` is in good shape, leave it. If stale, propose a refresh as separate work — Dunbar circle is canonically about intimacy, Hop about exploration.
2. **[Phase A · README.md:302-304]** Workbench detail view is a power-user toggle, not a replacement for Editorial. Toggle pattern open: icon button in page header vs. `?mode=workbench` query param. _(Historical note: this prompt originally used `?view=workbench`; shipped code uses `?mode=workbench`.)_
3. **[Phase A · README.md:305-307]** Bulk merge UX ("I imported 800 contacts, now have 200 unidentified") is out of scope for this pack. Worth filing as a discovered-from follow-up.
4. **[Phase A · exp-detail.jsx:123]** `BreadcrumbStrip` component referenced but not defined in the bundle. Implementation pattern open.
5. **[Phase A · README.md:35 + app.jsx:87]** Social-map `exp-social-map.jsx` missing; route kept from existing codebase.
6. **[Phase A · exp-command.jsx:141]** `KbMono` keyboard-shortcut capsule referenced but not defined; likely simple mono span.
7. **[Phase A · exp-hop.jsx:112-126]** Hop predicate filter chip behavior with 0 or 1 predicates is open.
8. **[Phase A · exp-concentration.jsx:14-19]** Concentration tabs hardcoded to 4 predicates (purchased-from, subscribed-to, co-attended, colleague-of). Generalization to arbitrary predicates open.
9. **[Phase A · app.jsx:257]** Orbit variant sketched but explicitly "not a default page"; status as third view-mode toggle unclear.
10. **[Phase A · exp-index.jsx:173-175]** "Unverified" derived state (`has-unverified-contact`) overlaps with "unconfirmed" chip definition. Gutter logic open.
11. **[Phase B · frontend/src/index.css]** Font-loading: `Inter Tight`, `Source Serif 4`, `JetBrains Mono` are specified by Dispatch but `@import`/`<link>` not visible in sampled CSS. Verify and add if missing. [Resolved 2026-05-18 by Phase 2 R2: fonts loaded at `frontend/index.html:7-9` via Google Fonts CDN. No font-loading work required.]
12. **[Phase B · frontend/src/index.css lines 19-27]** Token-namespace coexistence: shadcn uses `--foreground`, `--background`, `--primary` while Dispatch atoms use `--bg`, `--fg`. Verify layering in first build; rebase shadcn values to Dispatch oklch if conflicts emerge.
13. **[Phase B · SocialMapPage.tsx]** SubpageTabs across `/entities/*` requires unifying chrome. Refactor SocialMapPage into a `SocialMapView` component for nested-route composition.
14. **[Phase B · CommandPalette.tsx:25]** Entity-first reordering of search results; current implementation returns grouped results (pages/butlers/entities/contacts/sessions/state). Reorder so Entities group is first.
15. **[Phase C · `/api/entities/:id/columns?path=`]** `status: unclear`. The bundle says "the frontend can also do this client-side by chaining /neighbours calls" (README:208-210). Decide: ship Columns view client-side (no new endpoint) or server-side helper?
16. **[Phase C · `/api/entities/:id/merge`]** Entity-level merge needs spec clarification — only contact-level merge exists today at `roster/relationship/api/router.py:1660`. Request shape (`{entityA, entityB, keepAs}`) needs confirmation.
17. **[Phase C · contact_facts vs entity_info]** Verify whether contact facts (emails, phones, addresses) currently live in an `entity_info` table or separate structure. Decides scope of data migration in Bead 4.
18. **[Phase C · relations table]** Verify whether relations are persisted today or derived on-the-fly. Decides scope of Bead 3 + 5.
19. **[Phase C · `entity_state_flags` vs `entities.metadata`]** Decide whether to add a separate state-flags table or extend the existing `metadata` JSONB. Affects index strategy.
20. **[Phase C · contact predicate shape]** Phase 00 must clarify: is a contact a special entity with `type=contact`, or is it an entity linked to a contact record? Affects fold-in semantics.
21. **[Phase C · unverified state derivation]** Are "unidentified" and "unverified" the same state, or distinct? Affects queue + chip logic.
22. **[Phase D · `/api/entities/:id/activity` aggregator]** Spec must state explicitly that chronicler rows come via MCP tools, never direct SQL into `chronicler.episodes`. Preserves chronicler manifesto boundary.
23. **[Phase D · canned-gloss enforcement]** Spec must state explicitly that Detail-page voice glosses are canned strings selected by `(tier, state, category)`. Lock the source as a `glosses.ts` enum to prevent an implementer from reaching for an LLM call per page load.
24. **[Phase D · deterministic-Finder enforcement]** Spec must state explicitly that `/api/search` ranking is rule-based per `07 §7.5`. No embedding service or reranker LLM in v1.
25. **[Phase D · documentation drift]** Phase B's "Butlers touched" table propagated three phantom butlers (memory/contact/household). Update Phase B reference prompt or `about/lay-and-land/` to clarify the module-vs-butler distinction before the next redesign brief.

---

## 6b. Phase 1 doctrine reconciliation — binding amendments

> Added 2026-05-17 by `/project-direction` Phase 1 (doctrine reconciliation). Verdict: `proceed-with-amendments`. The ten amendments below are **binding inputs to Phase 2 (OpenSpec changeset)**. Any spec section that contradicts these amendments fails reconciliation.

**Critical context:** an existing OpenSpec change `relationship-tabs-to-entities` is the canonical precursor for the entity-detail surface. This redesign must **extend** that change, not duplicate or replace it. Routes already specced there live under `/api/relationship/entities/*`, not the top-level `/api/entities/*` the brief proposed.

### Amendment 1 — Data model rewrite: contacts as RDF triples under entities (binding architectural change)

> **User decision (2026-05-17):** Rewrite the data model. Contacts become an RDF namespace (subject-predicate-object) folded under entities. This **supersedes** RFC 0004's three-table identity model, not just works around it.

The new model:

- **Single triple store** keyed on entity. Subject = `entity_id`. Predicate = one of the contact-predicate catalog (`has-email`, `has-phone`, `has-handle`, `has-address`, `has-birthday`, `has-website`) or relational catalog (`knows`, `family-of`, `partner-of`, etc.). Object = literal string (for contact predicates) or another `entity_id` (for relational predicates).
- **`public.contacts` and `public.contact_info` are deprecated** as the canonical channel-identity registry. Their replacement is the new triple store, scoped under the relationship butler (`relationship.entity_facts` or similarly named) with the provenance contract (`src`, `conf`, `lastSeen`, `weight`, `verified`, `primary`) from brief Section 0.
- **Switchboard identity preamble + `resolve_contact_by_channel()` (currently RFC 0004:83-132) must be re-pointed** to query the new triple store. The Switchboard's reverse-lookup for ingestion routing (e.g., "incoming Telegram chat 12345 → which owner contact") becomes "SELECT subject FROM facts WHERE predicate='has-handle' AND object='telegram:12345'".
- **`public.entities` remains** as the canonical entity registry (id, type, name, tier, state, lastSeen, aliases). Only the contact registry collapses into the triple model.
- **`public.entity_info`** (per RFC 0004:64-82) holds credentials — out of scope for this redesign; leave untouched.

**Phase 2 deliverables driven by this amendment:**

1. **RFC 0004 amendment proposal** as part of the OpenSpec changeset — supersede §3 ("Contacts and Contact Info") with the triple model. The amendment must specify the migration path (`public.contacts` + `public.contact_info` → `relationship.entity_facts`).
2. **Switchboard contract amendment** — the identity preamble and `resolve_contact_by_channel()` interfaces must accept triple-store queries. Backward-compat shim period: maintain dual-write during migration window, then cut over.
3. **All consuming butlers** (relationship, household, calendar, chronicler, qa, messenger) must be inventoried for `public.contact_info` reads. Each call site re-points to the triple store via MCP/Switchboard.
4. **Migration plan** — translate existing `public.contact_info` rows into triples. Schema-level: `{ subject: entity_id (via public.contacts.entity_id), predicate: f"has-{type}", object: value, src: contact_info.source, last_seen: contact_info.last_seen, verified: contact_info.secured XOR something }`. Decisions needed: handle `contact_info.secured=true` rows (credentials? still contact facts?); handle `contact_info` rows whose contact lacks an entity_id (orphans).

**Open questions Phase 2 must answer (escalate to user if unresolvable):**

- Does the triple store live in `relationship` schema (consistent with relationship butler ownership) or `public` (cross-butler reads avoid the MCP hop)?
- Is `verified` a literal column on the triple, or is verification itself a separate triple (`(triple_id, verified-by, owner)`)? Pure RDF would say the latter.
- Reverse-lookup performance: a triple store with millions of rows needs `(predicate, object)` indexes. Quantify expected row count from current `public.contact_info` size.
- Does the brief's `relationship.relations` table (relational predicates) merge with the contact-predicate triples into one table, or stay separate? RDF purity says merge; query simplicity says merge; storage cost is identical. **Recommend merge into single `relationship.entity_facts` table.**

The UI fold-in (one `/entities` home, no `/contacts` page) is preserved. The storage fold-in is also preserved — and now requires the data-model rewrite.

### Amendment 1.1 — Migration-safety contract (user-mandated, 2026-05-17)

> **User instruction (verbatim):** "Please be extremely careful with this contacts→entities migration; 1) Do not lose data during this migration, and 2) Remember that all existing write jobs need to now be pointed to writing to the new 'backend' for storage. Create verification beads to this effect."

The contacts → triple-store migration is a high-blast-radius schema change. It must follow this protocol:

**A. Zero-loss guarantees**

1. **Pre-migration snapshot.** Before the cut-over, snapshot the entire `public.contacts` + `public.contact_info` tables to a timestamped backup table (`public.contacts_pre_migration_YYYYMMDD`, `public.contact_info_pre_migration_YYYYMMDD`). Snapshot must include all columns and all rows.
2. **Row-count parity check.** After migration, every `public.contact_info` row must correspond to exactly one triple in `relationship.entity_facts` (or be explicitly accounted for as "skipped: <reason>"). A reconciliation report at `docs/reports/entity-redesign-contact-migration-YYYY-MM-DD.md` must list: rows migrated, rows skipped (with reason), checksum of input vs. output.
3. **Orphan handling.** `public.contact_info` rows whose `public.contacts.entity_id` is NULL (orphan contacts) must be (a) escalated to the owner via notify(), (b) migrated as triples with a placeholder entity, or (c) explicitly dropped with an audit trail row. Decision required in Phase 2.
4. **Credentials carve-out.** `public.contact_info.secured=true` rows are credentials, not user-visible contact facts (per RFC 0004:64-82 distinction with `entity_info`). They either (a) remain in `public.contact_info` (a "credentials" sub-table), or (b) move to `relationship.credentials` (a separate non-triple table). They do NOT become triples in `relationship.entity_facts`.
5. **Dual-write period (mandatory).** After triples backend is live, every existing write path to `public.contact_info` must dual-write to `relationship.entity_facts` for a minimum of 7 days. Read paths cut over after dual-write verification (no diff drift in 24h). Then write paths cut over. Then `public.contact_info` becomes read-only (writes blocked, reads still allowed for one more period). Then `public.contact_info` is dropped.
6. **Rollback plan.** Every migration step is reversible until `public.contact_info` is dropped. Drop is a separate, dated decision after 30 days of triple-store-only operation.

**B. Write-path inventory + re-pointing**

Every place in the codebase that currently writes to `public.contact_info` or `public.contacts` must be:

1. **Enumerated** before migration begins (Phase 3 verification bead). Search vectors: `grep -rn "INSERT INTO contact_info\|UPDATE contact_info\|INSERT INTO contacts\|UPDATE contacts" src/ roster/`; `grep -rn "contact_info_table\|contacts_table\|ContactInfo\b\|Contact\b" src/ roster/`; SQLAlchemy/Pydantic models referencing those tables.
2. **Categorized** by butler ownership (relationship butler's own writes vs. cross-butler writes via Switchboard).
3. **Re-pointed** to write triples through a single, authoritative API surface (likely an MCP tool on the relationship butler, e.g. `relationship_assert_fact(subject, predicate, object, src, conf, …)`). No butler may write triples by direct SQL — schema isolation per RFC 0006 plus RDF integrity (predicate validation, dedup) require a central writer.
4. **Verified** post-cut-over with parity tests: for each write path, assert that issuing a write produces both (a) a triple in `relationship.entity_facts` and (b) — during dual-write — a row in `public.contact_info`. After cut-over, only (a).

Known write-path entry points to inventory (non-exhaustive — Phase 3 bead must complete the list):

- Connector ingestion (any connector that learns of an email/phone/handle/address from external service: telegram, email, calendar invite extraction, gmail import, etc.)
- Dashboard manual-entry endpoints (any form that lets the owner add a contact)
- Contact butler MCP tools (currently writes via the contacts module per `butler.toml:106-114`)
- Bootstrap path that creates the owner contact (per CLAUDE.md "owner contact is bootstrapped automatically on daemon startup")
- Switchboard's `resolve_contact_by_channel()` may also write (e.g., new-contact creation on first message)
- Manual fact entry via dashboard Editorial detail page ("add contact" action — new in this redesign)

**C. Verification beads (Phase 3 MUST create these)**

The following beads MUST exist in the Phase 3 graph as `blocked-by` upstreams of the cut-over bead:

| Bead title | Purpose | Effort |
|------------|---------|--------|
| `entity-migration: pre-migration snapshot + row-count baseline` | Capture `public.contacts` + `public.contact_info` snapshots with row counts and per-source-butler breakdown. Output: `docs/reports/contact-migration-baseline.md`. | S |
| `entity-migration: write-path inventory` | Grep + dependency-walk the codebase for every writer to `public.contact_info` / `public.contacts`. Output: a table of (file:line, butler, current write shape) for each writer. Sign-off required before any cut-over. | M |
| `entity-migration: central writer MCP tool` | Implement `relationship_assert_fact()` (or equivalent) as the single ingress to `relationship.entity_facts`. Includes predicate validation, dedup, provenance enforcement. | M |
| `entity-migration: dual-write shim per writer` | For each writer enumerated in the inventory bead, add dual-write (existing path + triple). Wraps existing call sites. Toggleable by feature flag. | L (parallelizable per writer) |
| `entity-migration: backfill triples from public.contact_info` | One-shot job: read every existing `public.contact_info` row, emit corresponding triple. Idempotent (re-runnable). Reconciliation report: rows in vs. triples out, with per-source breakdown. | L |
| `entity-migration: parity tests` | For each writer, write a test that asserts triple + row coexist after dual-write. Test the orphan-handling decision branch. Test the credentials carve-out branch. | M |
| `entity-migration: read-path cut-over` | After 24h of zero parity drift, switch read paths (relationship butler MCP read tools, Switchboard `resolve_contact_by_channel`) to query triples. `public.contact_info` reads stop being authoritative. | M |
| `entity-migration: write-path cut-over` | After read-path cut-over is stable for 7 days, remove dual-write shims; new writes hit `relationship.entity_facts` only. `public.contact_info` becomes read-only. | M |
| `entity-migration: post-cut-over verification report` | 30 days after write-path cut-over, produce final report at `docs/reports/contact-migration-postmortem-YYYY-MM-DD.md`: cumulative triple count, dropped/skipped row count, any incidents, sign-off to drop `public.contact_info`. | S |
| `entity-migration: drop public.contact_info (gated)` | Final drop, gated on the verification report's sign-off. Backups retained for 90 days. | S |

These beads block the redesign's UI-level features that depend on the triple store (Editorial detail contacts section, Workbench, curation queue, Finder ranking on contact-fact values) **only at write-path cut-over**, not at dual-write start. The frontend can read from `relationship.entity_facts` as soon as the backfill bead completes.

### Amendment 2 — RFC 0007 namespace fix

All ~15 new endpoints proposed in §3 must either:

- Live under `/api/relationship/entities/*` (consistent with `relationship-tabs-to-entities/spec.md:111-121` and `rfcs/0007:31` auto-discovery prefix), **OR**
- Be declared in a new "entities" core API package with an explicit RFC 0007 amendment akin to RFC 0007 Amendment 1 (`/api/system/*` carve-out at `:258-289`).

**Recommendation:** option A — extend `relationship-tabs-to-entities`. Re-prefix every brief §3 endpoint accordingly.

> **Spec-correction note (2026-05-27, bu-cj8om):** This amendment originally cited `/api/butlers/relationship/entities/*`. That prefix was incorrect. The shipped code uses `/api/relationship/` (see `roster/relationship/api/router.py:127`). RFC 0007:31 specifies auto-discovery uses `prefix='/api/<butler_name>'` — confirmed across the full roster: messenger, finance, chronicler, health, travel, home, general, education, and switchboard all use `/api/<butler>/`, not `/api/butlers/<butler>/`. All downstream specs have been corrected to use `/api/relationship/`.

### Amendment 3 — RFC 0007 envelope conformance

All responses use `ApiResponse<T>` / `PaginatedResponse<T>` envelopes (per `rfcs/0007:75-87`) unless explicitly listed in the existing exemption set. Brief §3 currently shows unwrapped shapes; wrap or exempt.

### Amendment 4 — `/api/search` reconciliation

`GET /api/search` already exists per `rfcs/0007:122` returning a grouped `SearchResults` shape. Brief §3 redefined the shape. **Either** extend the existing endpoint (add an `entities` group to the existing response), **or** rename the new endpoint (`/api/relationship/entities/search`). Do not silently redefine.

### Amendment 5 — Chronicler aggregator: tool surface check

`/api/entities/:id/activity` aggregator discipline (chronicler MCP tools, no direct SQL into `chronicler.*`) is preserved. **However:** the brief names `chronicler_list_events` — RFC 0014:255-258 lists `chronicler_list_episodes` / `chronicler_get_episode` / `chronicler_submit_correction`, but `chronicler_list_events` is not enumerated. Phase 2 must either (a) add `chronicler_list_events` to the chronicler MCP surface via spec amendment, or (b) rewrite the aggregator to call only existing tools.

Phase 2 must also add a guardrail test mirroring `rfcs/0014:178` ("tests MUST exercise the no-LLM invariant for every adapter") for the no-direct-SQL invariant.

### Amendment 6 — Tier promotion is a fact, not a column

`POST /api/entities/:id/promote-tier` (and the +/- buttons) **writes a `dunbar_tier_override` fact** (already a Timeline predicate in `relationship-tabs-to-entities/spec.md:121`), not a column write. This honors RFC 0013's "weight at query time" decision (`rfcs/0013:340-343`). The override surfaces on the Timeline tab.

### Amendment 7 — Editorial vs Workbench archetype declaration

EntityDetailPage **Editorial** is an editorial-archetype detail page (Display 44px headline permitted). **Workbench** is a workspace-archetype detail page (`text-2xl` H1, per `about/heart-and-soul/design-language.md:218-246` Non-Negotiable 2 + Gate A A2). This reconciles the 1.2 type-ratio doctrine with the brief's 44/24/14 Dispatch scale.

### Amendment 8 — `<Page>` primitive conformance

All six routes (`/entities`, `/entities/hop`, `/entities/columns`, `/entities/concentration`, `/entities/:id`, `/entities/social-map`) render inside `<Page>` with page-owned breadcrumbs per the in-flight `page-primitive-spec-sync` change. EntityDetailPage Editorial uses `<Page archetype="editorial">` (bu-hm0oe: additive resolution, does not modify `archetype="detail"`; the `editorial` archetype renders a Display 44px shell heading when breadcrumbs or actions are supplied). The Editorial/Workbench toggle lives in the Page shell's actions slot.

### Amendment 9 — Token discipline

The redesign adds **no new tokens outside `frontend/src/index.css`**. It reuses `--category-*` (butler hues), `--tier-*` (Dunbar ramp), `--severity-*` (per the in-flight `token-system-spec-sync` change). No hex literals outside `entity-model.ts` and the predicate-catalog UI rendering.

### Amendment 10 — Vocabulary + persistence harmonization

The Editorial/Workbench toggle uses the same `localStorage` persistence pattern as `redesign-detail-page-tab-vocabulary`'s "Resident/Operator" toggle. Key: `entities.detail.mode`. Phase 2 spec must either (a) align vocabulary to Resident/Operator for consistency, or (b) document the distinct vocabulary choice explicitly.

### Amendment 11 — `v1.md` doctrine update post-RFC 0004 Amendment 2

> Added 2026-05-18 by Phase 1 R-pass. Closes R4 coverage gap on `about/heart-and-soul/v1.md`.

`about/heart-and-soul/v1.md:64` lists "Contacts — shared identity registry with cross-channel resolution" as a v1 module. `:127-132` ("Identity System") describes "Shared contacts registry — canonical contact table with roles and entity linkage." After RFC 0004 Amendment 2 lands, the canonical noun is the **entity** and contacts are predicates on entities — the existing v1.md text becomes inaccurate.

**Phase 2/3 deliverable:** A task in tasks.md §12 (numbered 12.7) that, **at change-archive time**, edits `v1.md:64` and `v1.md:127-132` to replace "canonical contact table" with "canonical entity registry with contact predicates" and to fold the Contacts module bullet into the relationship butler entry. Owner-bootstrap and cross-channel resolution capabilities remain — only the implementation language updates.

### Amendment 12 — Owner-only authorization for entity endpoints

> Added 2026-05-18 by Phase 1 R-pass. Closes R2 critical C2 + R4 owner-only mandate gap. Three-part amendment: writes (12a), reads (12b), deploy gate (12c).

`about/heart-and-soul/security.md:18-22` + RFC 0007:309 require non-trivial identity operations to gate on owner role. The new entity endpoints introduce both mutation surfaces (which mint, merge, archive, forget entities) and read surfaces that return contact-fact `object` values (raw emails/phones/handles/addresses — PII). Both need owner-only authz; one without the other leaves a PII-leak hole.

**12a — Writes (mutations):** every `POST/PATCH/DELETE` under `/api/relationship/entities/*` MUST resolve the caller to an owner-role entity (`'owner' = ANY(e.roles)` per RFC 0007:309 pattern) and return HTTP 403 with `code='owner_required'` otherwise. Scope: `POST /entities`, `POST /entities/{id}/merge`, `POST /entities/{id}/archive`, `POST /entities/{id}/promote-tier`, `DELETE /entities/{id}`, `POST /entities/queue/dismiss`, `POST /entities/{id}/contacts`, `DELETE /entities/{id}/contacts/{pred}/{valueHash}`.

**12b — Reads (PII-bearing):** the same owner-only gate MUST apply to `GET /entities/queue`, `GET /entities/search`, `GET /entities/{id}/contacts`, `GET /entities/{id}/neighbours`, `GET /entities/{id}/activity`. These endpoints return raw contact-fact values (emails, phone numbers, handles, addresses) and aliased identity links; exposing them through the existing shared `DASHBOARD_API_KEY` would leak PII to any caller that reaches the API surface. The list-only `GET /entities` and per-entity timeline/notes/interactions endpoints (no raw contact-fact `object` values surfaced) inherit the existing dashboard session boundary.

**12c — Deploy gate:** the dev-time "no API key → auth disabled" path at `src/butlers/api/app.py:246` is incompatible with shipping the entity endpoints. Phase 2 spec MUST require: in any non-`dev` environment, daemon startup fails with a fatal error if `DASHBOARD_API_KEY` is unset. Add a guardrail test (tasks.md §10 new entry).

### Amendment 13 — Reader inventory companion to Amendment 1.1.B

> Added 2026-05-18 by Phase 1 R-pass. Closes R2 H1.

Amendment 1.1.B inventories writers of `public.contacts` / `public.contact_info`. It does NOT inventory readers. After read-path cut-over (migration bead 7), any unmigrated reader returns silently-stale data. Concrete starting readers found by grep:

- `src/butlers/identity.py` (`resolve_contact_by_channel`, `build_identity_preamble`)
- `src/butlers/modules/memory/tools/preferences.py`
- `src/butlers/modules/approvals/{_shared,gate,email_guard}.py`
- `roster/switchboard/tools/identity/inject.py`
- `roster/switchboard/tools/routing/route.py`
- `roster/home/modules/__init__.py`
- `roster/relationship/jobs/relationship_jobs.py`

**Phase 3 deliverable:** Add **Migration bead 4.5 — Reader inventory** to the §1.1.C bead list, blocking bead 7 (read-path cut-over). Each enumerated reader gets a re-pointing sub-bead.

### Amendment 14 — Dual-write reconciliation contract

> Added 2026-05-18 by Phase 1 R-pass. Closes R2 critical C3.

Amendment 1.1.A.5's dual-write protocol cannot guarantee transactional consistency across SQL (legacy `public.contact_info` write) and MCP (new `relationship_assert_fact()` call). Without a reconciliation contract, the dual-write window has silent failure modes (SQL commits, MCP fails → triple missing; SQL commits, MCP duplicates → racing supersession). Phase 2 spec MUST encode:

- **SQL is authoritative during dual-write.** Existing legacy writes commit unchanged; MCP call is best-effort post-commit.
- **Reconciler job (new).** A periodic worker (interval ≤ 1h during dual-write) sweeps `public.contact_info` rows lacking a matching active triple and emits them via the central writer. Idempotent on `(subject, predicate, object)`.
- **Parity test is eventual, not synchronous.** Migration bead 6 ("parity tests") asserts 24h-window reconciliation, not write-time synchrony.
- **Central writer must be safe inside open transactions.** `relationship_assert_fact()` MUST NOT require its own transaction wrapper or panic if called from inside an asyncpg pool connection.
- **Central writer is idempotent on `(subject, predicate, object)`.** Repeated calls with the same identity arguments produce one active row, not a duplicate; supersession semantics apply when `(src, conf, verified, lastSeen)` differ.

### Amendment 15 — Deterministic-Finder enforcement is transitive

> Added 2026-05-18 by Phase 1 R-pass. Closes R2 H3.

Tasks.md §10.8's no-LLM guardrail test currently scans the search handler file for direct imports. An implementer routes around it by adding `from relationship.utils.smart_rank import rank` where `smart_rank` calls Anthropic. Phase 2 spec MUST tighten the contract:

- **Transitive scan.** The guardrail test walks the full module-import graph reachable from the `/entities/search` handler at test time (use `importlib.util.find_spec` recursively or `modulegraph`). Any reachable module containing an import of the banned set fails the test.
- **Banned set enumerated:** `anthropic`, `openai`, `cohere`, `voyageai`, `mistralai`, `sentence_transformers`, `pgvector` distance operators (`<->`, `<=>`, `<#>`), `requests.post`/`httpx.post` to any non-localhost URL.
- **Allowed set enumerated** (so authors know what they can reach for): `rapidfuzz`, `python-Levenshtein`, plain SQL `ILIKE`, `pg_trgm` `similarity()` and `%` operator. Anything else requires a spec amendment.

### Amendment 16 — `chronicler_list_episodes` entity filter is a prereq

> Added 2026-05-18 by Phase 1 R-pass. Closes R2 M1.

Brief §3 + tasks.md §9.12 (activity aggregator at `/entities/{id}/activity`) require `chronicler_list_episodes(entity_id=...)`. RFC 0014:255-258 does not currently list `entity_id` as a filter parameter on `chronicler_list_episodes`. Tasks.md §12.5 records this as a "follow-up bead" — wrong, it's a prereq: task 9.12 cannot ship without the filter.

**Phase 2 deliverable:** Re-order tasks.md — §12.5 becomes a chronicler-side spec amendment that runs **before** §9.12. If the filter cannot be added in time, §9.12 is descoped to relationship-owned activity rows only (no chronicler join) for v1.

### Amendment 1.1.A.3 update — Orphan handling via Python script, not MCP

> Added 2026-05-18 by Phase 1 R-pass. Closes R2 H2.

Amendment 1.1.A.3 left orphan handling (rows where `public.contacts.entity_id IS NULL`) to Phase 2 with `memory_entity_create()` as a hint. That hint is unreachable: Alembic migrations run in a pre-daemon role with no event loop, no MCP client, no switchboard — calling an MCP tool is impossible.

**Binding update:** orphan resolution is a **post-migration Python script** under `src/butlers/scripts/contact_orphan_resolver.py`, gated by an explicit operator flag (`--apply` defaults to dry-run). The script:

1. Reads orphan rows from `public.contacts_pre_migration_YYYYMMDD` snapshot.
2. For each orphan, either (a) mints an entity row directly via SQL (carve-out from the "no direct SQL outside relationship butler" rule, justified by migration-time context), or (b) emits a `notify()` to the owner for manual resolution.
3. Records resolution outcome in `docs/reports/contact-migration-orphans-YYYY-MM-DD.md`.

The script is itself a migration bead — number 4.6 — sequenced between backfill (bead 5) and parity tests (bead 6).

### Amendment 7 update — Type-ratio reconciliation

> Added 2026-05-18 by Phase 1 R-pass. Closes R4 design-language type-ratio drift.

`about/heart-and-soul/design-language.md:243-246` requires a 1.2 type ratio between scale steps. Brief §1's Dispatch scale (44/24/14) has 44→24 ratio 1.83 and 24→14 ratio 1.71 — both exceed 1.2. The 1.2 doctrine is a *floor*, not a target; values above 1.2 satisfy it. The Display tier (44px) is exempt per the existing carve-out at `design-language.md:225-232` for editorial-archetype Display headlines. **No further reconciliation required.** Phase 2 spec authors must state this carve-out inline where the 44px Display appears (EntityDetailPage Editorial header).

### Phase 1 R1-R4 summary (updated 2026-05-18 post-R-pass)

- **R1 — Citation re-verification:** 16/16 citations verified. `chronicler_list_events` confirmed absent from RFC 0014 — Amendment 16 routes to `chronicler_list_episodes` with `entity_id` filter as a prereq.
- **R2 — Loophole sweep:** Closed by Amendments 12 (read-side data leak), 13 (reader inventory), 14 (dual-write reconciliation contract), 15 (transitive Finder enforcement), and the 1.1.A.3 update (orphan handling via Python script). Fold-vs-split deployability trap closed by proposal.md tightening (see proposal.md "Phase 2 extension (2026-05-17)" section).
- **R3 — Cross-spec consistency:** Phase 1 `FROM facts` ⇄ Phase 2 `relationship.entity_facts` contradiction MUST be resolved in Phase 2 (annotate Phase 1 endpoints as compatible during 10-step migration soak). Contact-detail route narrative drift fixed in Phase 2 (use canonical `/contacts/:contactId` per shipped spec). `workspace` archetype gap left to Phase 2 (either author sister spec or rewrite as `<Page archetype="overview">`).
- **R4 — Mandate coverage:** Closed by Amendments 11 (`v1.md` update task), 12 (owner-only authz writes+reads+deploy-gate), plus Amendment 7 update (type-ratio carve-out citation). Em-dash ban in canned glosses + RFC 0017 owner-gate carry-forward in `relationship_assert_fact()` flagged as Phase 2 spec deliverables (encoded as per-bead acceptance criteria in Phase 3).

**Net Phase 1 verdict: proceed-with-amendments-1-through-16.** Phase 2 begins from this baseline.

---

## 6. Handoff to `/project-direction`

This brief is the input to a `/project-direction` run with **feature evaluation focus** scoped to the entity redesign.

Concrete invocation:

```
/project-direction --focus=feature \
  --brief=docs/redesigns/2026-05-17-entity-brief.md \
  --bundle=pr/overview/entity-redesign/ \
  --binding-design-language=pr/overview/entity-redesign/reference/DESIGN_LANGUAGE.md \
  --binding-design-intent=docs/redesigns/2026-05-17-entity-brief.md#0-design-intent \
  --red-flag-policy=descope-or-escalate
```

> Note: `DESIGN_LANGUAGE.md` lives at `reference/DESIGN_LANGUAGE.md`, not bundle root (non-canonical layout).

If `/project-direction` does not accept these flags literally, paste this as a paragraph: "Run feature-evaluation reconciliation against the entity-redesign brief at `docs/redesigns/2026-05-17-entity-brief.md`. The bundle at `pr/overview/entity-redesign/` is the source. `pr/overview/entity-redesign/reference/DESIGN_LANGUAGE.md` is binding visual language. Section 0 of the brief (`#0-design-intent`) is binding design intent. Apply `descope-or-escalate` policy to any red verdict surfaced during reconciliation."

Carry-forward instructions:

- `pr/overview/entity-redesign/reference/DESIGN_LANGUAGE.md` is **binding**. Every spec section must preserve it.
- Section 0 of this brief is **binding**. Spec drift away from intent fails reconciliation.
- All `fixture`-evidence rows in §3 API delta must resolve against either a live endpoint or a new spec before commit.
- The two anti-temptation guardrails (canned glosses, deterministic Finder) must appear verbatim in the relevant spec sections.
- The chronicler aggregator discipline (MCP tools, not direct SQL) must appear in the `/api/entities/:id/activity` spec.
- After `/project-direction` Phase 3 produces the beads graph, Phase G of `butlers-redesign-prompt` will split the backend epic (Section 3.Proposed backend epic) into its own bead under title `entity redesign — backend contracts`, wired as a `blocked-by` upstream of the frontend epic.
