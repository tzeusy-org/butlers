# General Butler

The catch-all second brain. It stores anything that has no specialist home as schema-free
items in named collections, and it is Switchboard's safe fallback target when classification is
uncertain, so no message is dropped.

- **Identity and scope:** [`roster/general/MANIFESTO.md`](../../roster/general/MANIFESTO.md)
- **Required behavior:** [`butler-general` spec](../../openspec/specs/butler-general/spec.md)
- **Schedules, modules, and port:** [`roster/general/butler.toml`](../../roster/general/butler.toml)

## Ordinary-Only Collections

General's generic tools and dashboard API see only **ordinary** collections
([RFC 0037](../../about/legends-and-lore/rfcs/0037-general-capture-and-private-source-boundaries.md),
migration `gen_003`). The boundary is passive: General never classifies a collection private and
does not implement private custody.

- **Classification:** `collections.custody_private` defaults to `false`. A collection that is
  `true`, or that is flagged ordinary but holds an item with the reserved `possession_profile`
  key, is excluded from every generic list, search, export, count and statistic. An exact read or
  id-based write of such a collection behaves exactly like one for a missing id. Generic input
  can never set the flag or write `possession_profile`, and a classification can never be
  cleared.
- **One predicate:** `roster/general/tools/source_authority.py` owns the ordinary-parent
  predicate and the lock order: parent collection first, then item. The MCP tools and
  `roster/general/api/router.py` share both.
- **Name namespace:**
  - Uniqueness among ordinary names comes from the partial index
    `collections_ordinary_name_key`. It sits beside the legacy global `UNIQUE (name)` constraint,
    which keeps deployed `ON CONFLICT (name)` writers working; General never drops that
    constraint.
  - While the legacy constraint still holds, any private collection makes every name-based call
    refuse with the same fixed "unavailable" error. This covers create, declare, resolve, export
    and search by name, and it stops a name-specific conflict from revealing a private
    collection.
  - After a separately released custody cutover removes global uniqueness, a name used only by
    a private collection behaves exactly like an unused name.
  - A schema without this contract answers "unavailable" (HTTP 503 on the dashboard) instead of
    serving unfiltered reads.

## Vocabulary

`collection_declare(name, shape_description, aliases)` records an explicit ordinary collection
with a required, non-blank shape. Spellings that differ only by case, spacing, `_`/`-` or
punctuation share one normalized key. The database enforces its uniqueness among ordinary
collections, so concurrent variant declarations converge on one entry.

`collection_resolve(name)` resolves only:

- an exact ordinary collection name, or
- an exact declared spelling.

Normalized and fuzzy near matches come back only as `suggestions`, never as a guessed collection.

`item_create` and `collection_create` keep their absent-name creation; declaring is optional. No
vocabulary is seeded, renamed or consolidated automatically.

## Source Versions

Every generic item mutation appends an immutable row to `source_versions` in the same
transaction. The row records the item, parent collection, version number, sha256 digest of the
item projection, and the parent's `eligibility_generation`. Deletes append a content-free
tombstone. The table rejects `UPDATE`, `DELETE` and `TRUNCATE`.

`read_source_version` returns a stored version only while:

- the item still exists under an ordinary parent, and
- that parent's eligibility generation still matches the version.

Classifying a parent advances its generation, so stored history never outlives privacy.
`item_create_versioned` accepts an acquired connection, so a caller such as the future capture
service can commit the item, its version and its own receipt in one transaction.

## Rollback

Downgrading `gen_003` refuses while any private collection or globally duplicated collection name
exists. It never resets flags or renames, merges or deletes rows to fit. Otherwise it restores the
global name constraint and drops the passive representation. The `source_versions` and vocabulary
tables are dropped only when empty; if they hold rows, they stay in place unused, and a later
re-upgrade picks them up again.

## Related Pages

- [Switchboard Butler](switchboard.md) -- routes messages here as the default fallback
- [Relationship Butler](relationship.md) -- owns contact data beyond General's freeform model
