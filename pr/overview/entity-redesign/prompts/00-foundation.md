# 00 · Foundation — data model, API, contacts fold-in

**Build this before anything else.** Every other prompt in this pack
assumes the work in this one is done.

## Why this comes first

The redesign rests on three premises about the data model:

1. **Contacts are predicates, not a separate noun.** A person's emails,
   phones, handles, addresses, birthdays, and websites are all
   multi-valued predicates whose object is a literal string. The
   `/contacts` page exists today as a separate surface only because the
   storage layer was historically separate; that's now a UI concern,
   not a model concern.

2. **Every fact carries provenance and confidence.** No exceptions. The
   Dispatch design hides these in the gutter most of the time, but a
   workbench view needs them present, and the Finder needs them to rank.

3. **Every entity has at most one of four owner-state flags.**
   `unidentified` (system saw it, doesn't yet know who it is),
   `duplicate-candidate` (system thinks this is the same as another
   entity), `stale` (no touches for a long time), or none. These are
   what fill the curation queue.

If these three premises aren't true of the backend yet, this prompt is
the place to make them true. If they are already true, this prompt is
the place to confirm it and write the types.

---

## Featureset to implement

### 0.1 — Predicate catalog

Define the canonical predicate list. The prototype's list is in
`reference/prototype/data.jsx`; reproduce it as a TypeScript module.

**Relational predicates** (object is another entity id):

```
knows · family-of · partner-of · colleague-of · employer-of · employed-by
purchased-from · subscribed-to · lives-at · visited · attended
co-attended · mentioned-in
```

**Contact predicates** (object is a literal string, multi-valued):

```
has-email · has-phone · has-handle · has-address · has-birthday · has-website
```

Each predicate has `{id, label, domain, kind: 'relational' | 'contact',
literal: boolean}`. The `domain` is the butler that most often writes
this predicate; the UI uses it for provenance grouping.

Extending the catalog later should be a one-line change. The detail page
should never hardcode predicate IDs in its display logic — it should
read from the catalog.

### 0.2 — Entity type catalog

```
person · organization · place · product · account · event · group
```

Type drives the **glyph in the entity-mark** (`P / O / L / X / @ / E / G`),
not the hue. Hue stays neutral; that rule is non-negotiable per
`DESIGN_LANGUAGE.md §1c`.

### 0.3 — Entity record

```ts
type EntityId = string;

interface Entity {
  id: EntityId;
  type: 'person' | 'organization' | 'place' | 'product'
      | 'account' | 'event' | 'group';
  name: string;

  // Optional fields
  role?: 'owner' | 'admin';
  tier?: 0 | 1 | 2 | 3 | 4 | 5 | 6;   // 0 = owner; 1..6 = Dunbar tier (5/15/50/150/500/1500)
  aliases?: string[];
  category?: string;              // e.g. 'employer' | 'vendor' | 'subscription'

  // Owner-state — at most one
  state?: 'unidentified' | 'duplicate-candidate' | 'stale';
  dupOf?: EntityId;               // when state === 'duplicate-candidate'

  firstSeen?: string;             // ISO date
  lastSeen?: string;              // ISO date
}
```

`firstSeen` and `lastSeen` must be indexed for time-series queries.

**Dunbar tier ramp (six layers):** `tier` maps to one of six Dunbar-inspired size rings.
`0` is the owner pseudo-tier (excluded from the ramp); tiers `1..6` correspond to Dunbar
values 5 / 15 / 50 / 150 / 500 / 1500. The sixth layer (tier-6, 1500, "Recognizable") is the
outermost ring — casual acquaintances and recognised-but-unconnected entities. It renders
with the `--tier-6` CSS token (gray dot in `TierBadge`). Any entity whose computed tier
exceeds 1500 also falls into tier-6.

### 0.4 — Relation record

```ts
interface Relation {
  subject: EntityId;
  predicate: string;              // from the relational predicates above
  object: EntityId;
  meta: {
    conf: number;                 // 0..1
    src: string;                  // butler id
    weight: number;               // supporting-fact count
    lastSeen?: string;
  };
}
```

A relation is a triple plus metadata. Bidirectional traversal happens
client-side via an adjacency builder (the prototype has one), or
server-side via `/api/entities/:id/neighbours`.

### 0.5 — Contact-fact record

```ts
interface ContactFact {
  subject: EntityId;
  predicate: 'has-email' | 'has-phone' | 'has-handle'
           | 'has-address' | 'has-birthday' | 'has-website';
  value: string;                  // the literal
  meta: {
    conf: number;                 // 0..1
    src: string;                  // butler id
    verified: boolean;            // owner has confirmed
    primary?: boolean;            // display preference within (subject, predicate)
    lastSeen?: string;
  };
}
```

**Multi-valued is the default.** A person can have 4 emails. UI never
collapses to "the email" when there is more than one; it shows the
primary first and lists the rest. Verified vs unverified is rendered
with a small dot in the amber state colour.

### 0.6 — Owner-state queue

```ts
GET /api/entities/queue → {
  unidentified: Entity[];        // sorted lastSeen desc
  duplicateCandidates: Array<{
    a: Entity;
    b: Entity;                   // the existing entity it likely matches
    reason: string;              // e.g. "shared email · same employer"
    score: number;               // 0..1
  }>;
  stale: Entity[];               // sorted lastSeen asc (oldest first)
}
```

This single endpoint feeds the right-rail queue on the Index. The
ordering above is what the UI renders — keep it server-side so all
clients agree.

### 0.7 — Contacts fold-in

Remove `/contacts` from the sidebar. Add a redirect:

```
/contacts                  → /entities?has=contact
/contacts/:id              → /entities/:id
```

The Index filter `has=contact` is "person entities with at least one
contact predicate". It is a chip on the filter bar, not a separate
page. Surface the chip prominently — most users will visit Entities
looking for what they used to call Contacts.

---

## API touch-points

Confirm or add these:

```
GET  /api/entities                     ?type=&state=&q=&has=contact&cursor=
GET  /api/entities/:id
GET  /api/entities/:id/contacts        grouped by predicate
GET  /api/entities/:id/neighbours      grouped by predicate
GET  /api/entities/:id/activity        chronological
GET  /api/entities/queue               see 0.6
GET  /api/search?q=                    fuzzy across name/alias/contact/predicate

POST /api/entities                     create
POST /api/entities/:id/contacts        add or verify a contact-fact
POST /api/entities/:id/merge           into another entity
POST /api/entities/:id/promote-tier    +1 or -1
POST /api/entities/:id/archive
POST /api/entities/queue/dismiss       for unidentified rows
DELETE /api/entities/:id               forget (tombstone source)
```

### Provenance counts

A useful derived endpoint:

```
GET /api/entities/:id/provenance → { [butlerId: string]: number }
```

Used by the Editorial detail page's "provenance" rail. Cheap to compute
on the server side from existing triple metadata.

---

## TypeScript types module

Land all of the above in a single types module that the rest of the
work imports:

```
frontend/src/lib/entity-model.ts
```

Don't sprinkle these definitions across pages. The model belongs in one
place; pages depend on it.

---

## Acceptance criteria

- [ ] `entity-model.ts` exports `Entity`, `Relation`, `ContactFact`,
      `PREDICATES`, `TYPES`, with the schemas above.
- [ ] All seven endpoints in §0.6 and §API touch-points exist or have
      stubs that return well-shaped data.
- [ ] `/contacts` redirects to `/entities?has=contact`. Sidebar nav drops
      the Contacts item; the Index filter chip "has contact" exists and
      is wired.
- [ ] Existing pages (`EntitiesPage`, `EntityDetailPage`) continue to
      compile and render using the new model. No functional regressions
      expected at this stage — the visual redesign comes in later
      prompts.
- [ ] No predicate IDs are hardcoded outside `entity-model.ts` and the
      predicate-catalog UI rendering.

## Out of scope for this prompt

- Visual changes to existing pages — those come in 01/05.
- The Finder spotlight — 07.
- Any new sub-route under `/entities` — 02 / 03 / 04.
