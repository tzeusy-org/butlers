# Contacts.listed Flag — Migration Decision

Date: 2026-05-19
Bead: bu-gpc2u
Discovered-from: bu-wkjc2

---

## Current Semantics

`public.contacts.listed` is a **UI-visibility / CRM-active flag** that distinguishes
live, user-managed contacts from archived or system-shadow contacts. It defaults to
`true` on creation and is set to `false` in two scenarios:

1. **Explicit archive** — `contact_archive()` sets `listed = false` (and
   `archived_at = now()`). Tests confirm archived contacts disappear from search.
2. **Contact merge** — after the source contact is merged into the target,
   `listed = false` is applied to the source (it is logically retired).

`listed = true` is NOT a relationship-strength indicator — it is a hard
include/exclude gate. Every read path that filters on it uses `WHERE c.listed = true`
to suppress archived contacts from working surfaces.

### Reader locations that filter on `contacts.listed`

| File:Line | Context | Intent |
|---|---|---|
| `roster/relationship/tools/resolve.py:143,214,249` | Contact name-match in `contact_resolve()` | Exclude archived contacts from resolution |
| `roster/relationship/tools/dunbar.py:254` | Dunbar decay scoring (`compute_tier_ranking`) | Score only active contacts |
| `roster/relationship/tools/dunbar.py:905` | `_get_all_listed_contacts()` | Load active contacts for Dunbar engine |
| `roster/relationship/tools/dunbar.py:911` | `get_dunbar_ranking()` | List active contacts with tier |
| `roster/relationship/tools/vcard.py:48` | vCard export | Export only active contacts |
| `roster/relationship/tools/dates.py:77` | Upcoming important dates | Dates for active contacts only |
| `roster/relationship/tools/labels.py:43` | `contact_search_by_label()` | Label search on active contacts |
| `roster/relationship/api/router.py:1022` | `list_overdue_contacts` dashboard endpoint | Overdue cadence for active contacts |
| `roster/relationship/jobs/relationship_jobs.py:187` | Upcoming dates briefing job | Active contacts only |
| `roster/relationship/jobs/relationship_jobs.py:311` | Stale contacts scan job | Active contacts only |
| `roster/relationship/jobs/relationship_jobs.py:431` | Gift-contact date lookup | Active contacts only |
| `roster/relationship/jobs/relationship_jobs.py:528` | Interaction milestones job | Active contacts only |
| `src/butlers/jobs/briefing.py:661` | Stay-in-touch highlights in briefing | Active contacts only |

Total confirmed `c.listed = true` filter sites: 13 (consistent with "10+" in the issue).
The `contact-migration-read-path-inventory.md` notes this in Follow-up 2: "its
post-cut-over encoding on entities must be finalized in bead 5 before bead 7 can cut
over those readers."

---

## Options Considered

### Option A — Boolean column on `public.entities` (`entities.listed`)

Add `listed BOOLEAN NOT NULL DEFAULT true` to `public.entities`.

- **Faithfulness:** exact 1:1 mapping. The bead 5 backfill sets
  `entities.listed = contacts.listed` for every entity. All 13 read sites re-point
  from `WHERE c.listed = true` to `WHERE e.listed = true` (or `WHERE e.id IN (... listed=true ...)`).
- **Migration cleanliness:** bead 5 reads `public.contacts_pre_migration_YYYYMMDD.listed`
  and copies the value to `public.entities`. No information lost.
- **Spec alignment:** Brief §0 says `public.entities` is "the canonical entity registry
  (id, type, name, tier, state, lastSeen, aliases)". Adding `listed` follows the brief's
  own framing that `public.entities` holds CRM-state. It is a simple, indexed boolean
  — the same shape as `archived_at IS NULL` on contacts.

### Option B — Entity-attribute fact in `relationship.entity_facts`

Store predicate `is-listed` with object `'true'` or `'false'` as a triple.

- **Faithfulness:** information is preserved but indirection increases. The 13 read
  sites must change from a boolean column filter to a triple sub-join:
  `EXISTS (SELECT 1 FROM relationship.entity_facts WHERE subject=e.id AND predicate='is-listed' AND object='true' AND validity='active')`.
- **Migration cleanliness:** bead 5 must emit a `is-listed` triple for every contact
  row in addition to the contact-info → `has-*` triples. This inflates the backfill
  scope and is a category mismatch: `is-listed` is an entity state, not a contact
  predicate derived from `public.contact_info`.
- **Query complexity:** adds a correlated subquery or join to every read site. Dunbar
  engine (`compute_tier_ranking`) scores hundreds of contacts in a single SQL pass;
  a sub-join per entity would hurt latency.
- **Spec alignment:** `relationship.entity_facts` is scoped to contact predicates
  (`has-email`, `has-phone`, …) and relational predicates (`knows`, `family-of`, …).
  A boolean visibility flag is neither — it is CRM lifecycle state, not a
  multi-valued contact attribute.

### Option C — Relationship-strength derivation

Derive `listed` from Dunbar score or interaction weight: e.g., `listed ↔ dunbar_tier < threshold`.

- **Faithfulness:** lossy. Archived contacts may have high historical interaction
  counts; setting `listed=false` is an explicit owner decision not deducible from
  score. Information loss during backfill is unacceptable per Brief Amendment 1.1.A.1.
- **Migration cleanliness:** unworkable — a 0-weight entity could be user-created
  (brand new contact with no interactions) and should be `listed=true`.

### Option D — Drop the flag (treat entity existence as listing)

Treat every entity as "listed"; only archived entities (marked via `metadata->>'deleted_at'`
or a similar tombstone) are excluded.

- **Faithfulness:** lossy. The entity model already uses `(metadata->>'merged_into') IS NULL`
  as a soft-delete sentinel (see `core_002_identity.py:124-128` partial unique index).
  Overloading `metadata` for archive state repeats the contact-era anti-pattern.
  It conflates "entity is live" with "contact is visible to the owner in CRM lists."
  An entity created by the system (e.g., an organization entity auto-minted during a
  fact assertion) should not appear in the owner's Dunbar ranking just because it
  exists.
- **Migration cleanliness:** requires re-encoding `listed=false` contacts into a
  metadata convention that has no current schema contract. Audit trail is weaker
  than a typed boolean column.

---

## Decision

**Chosen: Option A** — add `listed BOOLEAN NOT NULL DEFAULT true` to `public.entities`.

**Rationale:**

1. **Semantic faithfulness.** `listed` is an entity-scoped, owner-managed, binary
   CRM-lifecycle flag. It belongs on the entity row itself, not in the predicate store
   (Option B) and not derived from scoring (Option C).

2. **Query efficiency.** All 13 read sites use the flag as a simple filter predicate.
   A boolean column on the entity table preserves O(1) index lookup. A triple-based
   encoding (Option B) would add a correlated sub-join to every site, degrading the
   Dunbar engine's batch scoring query.

3. **Migration cleanness.** Bead 5 backfills `relationship.entity_facts` from
   `public.contact_info`. The `listed` flag comes from `public.contacts`, not
   `public.contact_info` — so it is a separate backfill target. Storing it on
   `public.entities` means bead 5 also sets `entities.listed = contacts.listed` in a
   single UPDATE pass after the contact-info triple emission loop. No schema design
   decision is deferred.

4. **Spec alignment.** Brief §0 keeps `public.entities` as the canonical entity
   registry for CRM-state. Brief Amendment 1 explicitly says "`public.entities` remains
   as the canonical entity registry." A boolean column is a first-class entity attribute,
   consistent with how `roles`, `entity_type`, and `aliases` are handled today.

5. **Option D rejected explicitly.** The existing `(metadata->>'merged_into') IS NULL`
   partial-unique tombstone is a code smell inherited from the legacy model. Adding
   another soft-delete convention to `metadata` compounds the problem. A typed boolean
   column is cleaner and auditable.

---

## Migration Implications

### Bead 5 backfill

The bead 5 script (`src/butlers/scripts/contact_backfill_triples.py`) currently reads
`public.contact_info_pre_migration_YYYYMMDD` → emits `has-*` triples. It must also:

1. After the triple emission loop, execute:

   ```sql
   UPDATE public.entities e
   SET listed = c.listed
   FROM public.contacts_pre_migration_YYYYMMDD c
   WHERE c.entity_id = e.id;
   ```

   This is a one-shot UPDATE using the pre-migration snapshot (so the flag values are
   frozen at cut-over time, not affected by any runtime changes during the migration window).

2. The script's parity report should include a row-count check:
   `entities updated with listed=false = contacts with listed=false and entity_id IS NOT NULL`.

3. Entities with no matching contact row (i.e., entities that pre-date the contacts
   system) receive the column default `true` — correct, since they were never explicitly
   archived.

### New schema delta

A new Alembic migration (tentatively `core_103_entities_listed_flag.py`) must:

```sql
ALTER TABLE public.entities
  ADD COLUMN IF NOT EXISTS listed BOOLEAN NOT NULL DEFAULT true;

CREATE INDEX IF NOT EXISTS idx_entities_listed
  ON public.entities (listed)
  WHERE listed = true;
```

This is a new core migration, not a relationship-butler migration, because
`public.entities` is a core table (owned by `core_002_identity`). The migration is
non-destructive and backward-compatible (existing code that does not yet reference
`entities.listed` is unaffected).

### Read-path migration

All 13 read sites listed above re-point in bead 7 (read-path cut-over). The mechanical
change is:

- `JOIN contacts c ON ... WHERE c.listed = true` becomes `WHERE e.listed = true`
  (or `JOIN public.entities e ON ... WHERE e.listed = true` if the entity join is not
  already present).

Sites that already have `e` in scope (e.g., `dunbar.py:234` which joins `contacts c`
and derives `entity_id`) can promote the filter to the entity directly once the contacts
JOIN is removed.

The read-path cut-over for the `listed` filter is therefore a by-product of the general
bead 7 work — no dedicated sub-bead is needed, but bead 7 acceptance criteria must
explicitly include "all `c.listed = true` filters re-pointed to `e.listed = true`."

---

## Follow-up beads filed

Two follow-up beads are required for implementation:

1. **Schema migration bead** (filed as `bu-69zp9`): author `core_103_entities_listed_flag.py`
   — add `listed BOOLEAN NOT NULL DEFAULT true` + index to `public.entities`. This must
   land before bead 5 runs.

2. **Bead 5 script amendment bead** (filed as `bu-qpiy0`): extend
   `contact_backfill_triples.py` to include the `UPDATE public.entities SET listed = c.listed`
   step and add it to the parity report.

Both beads are `discovered-from: bu-gpc2u`.
