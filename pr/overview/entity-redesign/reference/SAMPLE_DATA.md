# Sample data — the design's contract with the backend

`reference/prototype/data.jsx` is the canonical sample dataset for the
prototype, and the closest thing in this pack to a written schema for
the entity store.

Read it before writing types. The shape of every record there is what
the rest of the prompts depend on.

## What's in it

### `ENTITIES`

~30 entities across all 7 types — owner, family at tier 1, friends at
tiers 2–5, vendors, employers, subscriptions, places, groups, plus
deliberately-broken entities to exercise queue logic:

- `p-unk1`, `p-unk2`, `p-unk3` — `state: 'unidentified'`
- `p-tan2` — `state: 'duplicate-candidate'`, `dupOf: 'p-tan'`
- `p-stale1` — `state: 'stale'`, last touch 20 months ago

### `RELATIONS`

The triple list. Subject–predicate–object–metadata. Includes:

- Owner-rooted edges of every relational predicate type
- Friend-of-friend edges (`p-deb` knows `p-rav`, etc.) so Hop can find
  paths
- Reciprocal employer/employed-by chains so the workbench duplicate
  view has something to detect
- `co-attended` edges with `lastSeen` set so recency-sorting tests work

### `CONTACT_FACTS`

The fold-in. Multi-valued contact predicates per entity. Variations:

- A person with 2 emails and 1 phone (Lin) — exercises primary-first
  rendering
- A person with multiple unverified facts (Amy) — exercises the amber
  dot
- Unidentified entities whose only "attribute" is the contact fact
  itself (`p-unk1` has-email `unknown@swiftpost.co`)
- A duplicate candidate that shares a contact fact with its target
  (`p-tan` and `p-tan2` both has-email `tanvir.ahmed@…`) — exercises
  the duplicate-warning panel and the workbench's confidence
  inspector
- Organization-level contacts (`o-ndlm` has-website, has-address) —
  exercises the claim that contact predicates apply beyond persons

## Useful invariants

When you build the real types, these are worth asserting at the type
level or in tests:

1. Every entity has exactly one of: `state` ∈ {unidentified,
   duplicate-candidate, stale} or no state field. There's no
   "unidentified + stale".
2. Only `duplicate-candidate` entities have `dupOf`. Required.
3. Owner has `role: 'owner'` and `tier: 0`. No other entity has tier 0.
4. Tier 1–5 is monotone: 1 = inner, 5 = distant.
5. `partner-of` is a subset of `family-of` semantically but a separate
   predicate. Don't merge them; the UI uses both.
6. `mentioned-in` is the membership predicate for groups. Group
   membership has no other representation.
7. Contact-facts have `primary?` — at most one fact per
   (subject, predicate) should be primary. Enforce this server-side.
8. `lastSeen` on a relation is the most recent evidence date; on an
   entity, the most recent activity overall.

## Pitfalls when the real data lands

The prototype dataset is small and well-formed. The real one isn't.
Things to watch for:

- **Orphan triples** — relations whose subject or object isn't in the
  entity list. Filter at the API boundary; never let one through to the
  UI. The prototype's `buildAdjacency` skips them silently.
- **Duplicate aliases** — the same alias on two entities. The
  identity-resolver should flag this as a duplicate candidate, not let
  it through.
- **Very high-weight relations** — `purchased-from` on a daily coffee
  vendor can reach 5,000+. The bar charts in Concentration are
  fine with this; the Hop view caps neighbour-mark size at 16 px so
  it won't blow up the layout.
- **Missing `firstSeen`/`lastSeen`** — older entities may lack these.
  Render an em-dash; don't throw.

## A note on confidence

The prototype scatters `conf` values from 0.5 to 1.0 without much
discipline. In the real world:

- `conf: 1.0` should mean "owner-verified". Reserve it.
- `conf >= 0.85` is "system is confident". Show as `--fg`.
- `conf < 0.85` is "system has doubts". Show as `--amber` in the
  workbench confidence inspector.

These thresholds are not currently in the prototype; codify them in
`entity-model.ts`.
