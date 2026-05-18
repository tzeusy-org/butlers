## ADDED Requirements

### Requirement: Relationship facts triple store

The relationship butler SHALL own a single triple-store table `relationship.facts` that
serves as the canonical RDF (subject-predicate-object) registry for both relational
(`knows`, `family-of`, `partner-of`, `co-attended`, `colleague-of`, ...) and contact
(`has-email`, `has-phone`, `has-handle`, `has-address`, `has-birthday`, `has-website`)
predicates. This table **supersedes** RFC 0004 §3 ("Contacts and Contact Info") as the
canonical channel-identity registry.

**Schema location:** `relationship` schema (NOT `public`). Cross-butler reads go through
Switchboard / MCP, consistent with RFC 0006 schema isolation. A single triple table avoids
the dual-write trap of putting contact-triples in `public` and relational-triples in
`relationship`.

**Single table for both predicate families.** Contact-facts and relational-facts live in
ONE `relationship.facts` table (NOT two). Rationale: RDF purity (subject-predicate-object
is the contract); identical column shape across predicate families; query simplicity
(`SELECT * FROM relationship.facts WHERE subject = $1`); storage cost is identical;
resolves the Phase 1 Amendment 1.1 open question.

**Schema:**

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `subject` | UUID NOT NULL | FK to `public.entities(id)` |
| `predicate` | TEXT NOT NULL | From `relationship.predicate_registry` |
| `object` | TEXT NOT NULL | Literal value (for `has-*` predicates) or `entity_id::text` (for relational predicates) |
| `object_kind` | TEXT NOT NULL | `'literal'` or `'entity'`; informs how to interpret `object` |
| `src` | TEXT NOT NULL | Authoring butler |
| `conf` | FLOAT NOT NULL DEFAULT 1.0 | 0..1 |
| `last_seen` | TIMESTAMPTZ NULL | |
| `weight` | INT NULL | Relational aggregation weight |
| `verified` | BOOL NOT NULL DEFAULT false | Owner-confirmed |
| `primary` | BOOL NULL | Primary-of-kind for multi-valued contact preds |
| `validity` | TEXT NOT NULL DEFAULT 'active' | `active \| retracted \| superseded` |
| `created_at` | TIMESTAMPTZ NOT NULL DEFAULT now() | |
| `updated_at` | TIMESTAMPTZ NOT NULL | |

**Indexes (required):**
- `(subject, predicate)` — primary access pattern
- `(predicate, object) WHERE object_kind = 'literal'` — reverse-lookup for ingestion
  routing (e.g. "incoming Telegram chat 12345 → which entity")
- `(predicate) WHERE validity = 'active'` — Concentration aggregation
- `(last_seen DESC)` — stale detection, Finder tie-break
- `(subject) WHERE validity = 'active' AND predicate LIKE 'has-%'` — contacts endpoint

**Uniqueness:** `UNIQUE (subject, predicate, object) WHERE validity = 'active'`.

### Requirement: Predicate catalog

The set of valid predicates lives in `relationship.predicate_registry` (table seeded by
Alembic migration). Predicates are grouped into families:

- **Contact predicates** (`object_kind='literal'`): `has-email`, `has-phone`, `has-handle`,
  `has-address`, `has-birthday`, `has-website`.
- **Relational predicates** (`object_kind='entity'`): `knows`, `family-of`, `partner-of`,
  `parent-of`, `child-of`, `colleague-of`, `friend-of`, `co-attended`, `purchased-from`,
  `subscribed-to`, `visited` (set extensible).
- **Override predicates** (`object_kind='literal'`, JSON): `dunbar_tier_override` (per
  RFC 0013 weight-at-query decision and Phase 1 Amendment 6).

No predicate ID MAY be hardcoded outside `entity-model.ts` (frontend) and
`relationship.predicate_registry` (backend); resolves Brief §1 hard-don't.

### Requirement: Central writer — `relationship_assert_fact()`

ALL writes into `relationship.facts` MUST go through a single MCP tool
`relationship_assert_fact(subject, predicate, object, *, src, conf, weight, primary,
verified, object_kind)` exposed by the relationship butler. No butler MAY issue a direct
`INSERT INTO relationship.facts` or `UPDATE relationship.facts` from outside the
relationship butler's schema role.

The central writer is responsible for:
- Predicate validation against `relationship.predicate_registry`.
- Dedup (`ON CONFLICT (subject, predicate, object) WHERE validity='active' DO UPDATE`).
- Provenance enforcement (every triple has `src`, `conf`, `verified`).
- Supersession on update (mark prior row `validity='superseded'`, insert new row).

This single-ingress contract preserves RFC 0006 schema isolation and RDF integrity.

#### Scenario: Direct SQL writes are blocked
- **WHEN** any butler other than relationship attempts `INSERT INTO relationship.facts`
- **THEN** PostgreSQL MUST reject the statement on role permissions
- **AND** the only successful write path MUST be `relationship_assert_fact()` via MCP

### Requirement: Switchboard `resolve_contact_by_channel()` re-points to triples

The Switchboard's `resolve_contact_by_channel(channel_type, channel_value)` function (defined
in RFC 0004 §"resolve_contact_by_channel()", `rfcs/0004:83-95`) MUST be re-implemented to
query `relationship.facts`:

```sql
SELECT f.subject AS entity_id,
       e.canonical_name AS name,
       COALESCE(e.roles, '{}') AS roles
FROM relationship.facts f
JOIN public.entities e ON e.id = f.subject
WHERE f.predicate = $1                                  -- e.g. 'has-handle' or 'has-email'
  AND f.object   = $2                                   -- channel-specific identifier
  AND f.object_kind = 'literal'
  AND f.validity = 'active';
```

The channel-type → predicate mapping is:
- `telegram` → `has-handle` (object value = `telegram:<chat_id>`)
- `email` → `has-email`
- `discord` → `has-handle` (object = `discord:<user_id>`)
- `phone` → `has-phone`

The `ResolvedContact` dataclass (RFC 0004:97-104) loses the `contact_id` field; the new
return shape carries `entity_id` (which serves the same routing purpose). The identity
preamble format (RFC 0004:119-132) is updated to drop `contact_id` and reference only
`entity_id`.

### Requirement: Migration safety — dual-write, parity, cut-over

The contacts → triple-store migration MUST follow the 10-step protocol enumerated in
Phase 1 Amendment 1.1.C (see `docs/redesigns/2026-05-17-entity-brief.md` §6b Amendment 1.1)
and tracked as 10 verification beads in the beads graph (NOT duplicated as tasks here —
see this change's tasks.md §11 for cross-refs). The protocol MUST guarantee:

1. **Pre-migration snapshot** of `public.contacts` and `public.contact_info` to
   `public.contacts_pre_migration_<YYYYMMDD>` and
   `public.contact_info_pre_migration_<YYYYMMDD>`.
2. **Backfill** triples from existing `public.contact_info` rows via the central writer.
3. **Dual-write period** (minimum 7 days): every existing writer to `public.contact_info`
   ALSO calls `relationship_assert_fact()` via MCP, gated by a feature flag.
4. **Read-path cut-over**: Switchboard `resolve_contact_by_channel()` queries triples after
   24h of zero parity drift.
5. **Write-path cut-over**: dual-write shims removed; `public.contact_info` becomes read-only
   for 30 days.
6. **Drop**: `public.contact_info` and `public.contacts` dropped after the 30-day soak +
   explicit operator sign-off.

The deprecation timeline is BINDING; deviation requires a new OpenSpec change.

### Requirement: Credentials carve-out

`public.contact_info` rows where `secured = true` (RFC 0004:54) are **credentials**,
not user-visible contact facts. They MUST NOT be migrated to `relationship.facts` as
triples. They either (a) remain in a renamed `public.contact_info_credentials` sub-table
or (b) move to `relationship.credentials` (separate non-triple table).

**Phase 2 decision:** option (b) — move to `relationship.credentials` co-located with
relationship butler ownership; the relationship butler is the canonical writer of credentials
already, per `roster/relationship/butler.toml:106-114`.

### Requirement: Orphan contact handling

`public.contact_info` rows whose `public.contacts.entity_id IS NULL` (orphan contacts) MUST
be resolved before backfill. Resolution policy (Phase 2 decision, resolves Amendment 1.1.A.3):

1. Run `memory_entity_create()` to mint an entity per orphan contact (mirrors the existing
   `resolve_contact_entity_id()` flow at `roster/relationship/tools/_entity_resolve.py`).
2. Backfill `public.contacts.entity_id` to the new entity.
3. Then emit triples.

The pre-migration baseline report MUST enumerate orphan count and post-resolution count.

### Requirement: `verified` is a column, not a triple

The `verified` field is a **column on `relationship.facts`**, not a separate
verification-triple (`(triple_id, verified-by, owner)`). Rationale (resolves Amendment 1
open question): pure RDF would split it out, but every triple needs a verified flag,
the verifying actor is always the owner (single-user v1), and the column form keeps
query plans simple. If v2 introduces multi-actor verification, the column may be promoted
to a separate verification table at that point.
