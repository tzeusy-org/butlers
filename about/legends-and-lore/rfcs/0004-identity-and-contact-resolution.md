# RFC 0004: Identity and Contact Resolution

**Status:** Accepted
**Date:** 2026-03-24
**Amended:** 2026-04-29 — added contact_info context tagging and context-aware notify() routing
**Amended:** 2026-05-19 — contacts collapsed to RDF triples per Amendment 2
**Amended:** 2026-06-18 — declared entity_info/entity_facts seam law; telegram_chat_id mapped to has-handle predicate; write-guard added

**Proposed amendment:** Amendment 4 below is an unadopted design candidate.
Owner merge adopts the contract; implementation and live cutover remain separate.

## Summary

Butlers maintains a shared identity registry spanning two tables in the `public` PostgreSQL schema: `entities` and `relationship.entity_facts` (RDF triple store). The `resolve_contact_by_channel()` function queries the triple store to map external channel identifiers to canonical entities with role information. Unknown senders receive temporary identity records with disambiguation metadata. An identity preamble is prepended to every routed message, providing downstream butlers with structured sender context for personalized responses, access control, and entity-linked memory.

## Motivation

Every external message entering the system arrives with a provider-native sender identifier (Telegram user ID, email address, Discord handle). Butlers need to resolve this raw identifier to a known identity to enable: owner-aware routing and priority handling, role-based approval gates, entity-linked memory retrieval, personalized responses, and outbound notification targeting. Without a shared identity layer, each butler would need its own contact database, creating duplication and inconsistency.

## Design

### Schema Structure

All identity tables reside in the `public` PostgreSQL schema, readable by all butler database roles.

#### public.entities

The anchor table for identity. Each row represents a known person or actor.

| Column | Type | Description |
|--------|------|-------------|
| `id` | UUID | Primary key |
| `canonical_name` | TEXT | Display name |
| `entity_type` | TEXT | Typically `"person"` |
| `roles` | TEXT[] | Role assignments (e.g., `['owner']`) |
| `aliases` | TEXT[] | Alternative names |
| `metadata` | JSONB | Extensible; temporary entities carry `{"unidentified": true}` |

#### Contact channel identifiers

Contacts are triples in `relationship.entity_facts` (Amendment 2), for example
`(entity_id, "has-email", "alice@example.com")`; see `openspec/specs/relationship-facts/spec.md`.

#### public.entity_info

**Seam law (Amendment 3):** `public.entity_info` is a **credentials-only** store.
The split axis is SENSITIVITY, not TYPE.

- **`relationship.entity_facts`** is the single source of truth for ALL non-secret
  facts, identifiers, and relationships (channel handles, email, phone, telegram
  chat IDs, websites, …).
- **`public.entity_info`** holds ONLY `secured=True` credentials (OAuth tokens,
  API hashes, session strings) plus a narrow whitelist of non-secret technical
  configuration entries that have no predicate home in entity_facts
  (`telegram_api_id`, `home_assistant_url`).

A write-time guard (`credential_store.assert_entity_info_secured()`) enforces
this rule at every `public.entity_info` write surface and raises `ValueError`
when a non-secret, non-whitelisted type is passed. The long-term intent is to
rename this table to `public.entity_secrets` (later phase of epic bu-oluyt).

Extended identity-bound data linked to entities. Used for credentials that belong to a specific identity (see RFC 0006 for credential store details).

| Column | Type | Description |
|--------|------|-------------|
| `entity_id` | UUID (FK) | Links to `public.entities` |
| `info_type` | TEXT | Type identifier (e.g., `"google_oauth_refresh"`, `"telegram_api_id"`) |
| `value` | TEXT | The stored value |
| `is_primary` | BOOLEAN | Preferred entry when multiple exist |
| `secured` | BOOLEAN | Must be `true` except for whitelisted technical identifiers |

**Registered `info_type` values:**

| `info_type` | Description | Secured | Notes |
|-------------|-------------|---------|-------|
| `google_oauth_refresh` | Google OAuth 2.0 refresh token | yes | |
| `telegram_api_id` | Telegram user-client API ID | no | Technical credential component; no predicate home in entity_facts |
| `telegram_api_hash` | Telegram user-client API hash | yes | |
| `steam_api_key` | Steam Web API key for the gaming butler | yes | |
| `telegram_user_session` | Telegram user-client StringSession | yes | |
| `home_assistant_url` | Home Assistant service base URL | no | Service URL config entry; no predicate home in entity_facts |

**Mapping of previously misrouted types:**

The following types were written to `entity_info` with `secured=False` before
Amendment 3.  They are non-secret routing handles and must now go to
`relationship.entity_facts` instead:

| Old `info_type` | New entity_facts predicate | Object format |
|-----------------|---------------------------|---------------|
| `telegram` | `has-handle` | `telegram:<id>` |
| `telegram_chat_id` | `has-handle` | `telegram:<id>` |

### resolve_contact_by_channel()

The core identity operation in `src/butlers/identity.py`. Given a channel type and value, it performs a triple-store lookup to resolve the entity. The operation queries `relationship.entity_facts` for a fact matching the channel type and value, then returns the associated entity with its role information.

For email addresses:
```sql
SELECT DISTINCT e.id, e.canonical_name, COALESCE(e.roles, '{}')
FROM relationship.entity_facts f
JOIN public.entities e ON e.id = f.subject
WHERE f.predicate = 'has-email' AND f.object = $1
```

For other channel types (Telegram, Discord, etc.):
```sql
SELECT DISTINCT e.id, e.canonical_name, COALESCE(e.roles, '{}')
FROM relationship.entity_facts f
JOIN public.entities e ON e.id = f.subject
WHERE f.predicate = CONCAT('has-', $1) AND f.object = $2
```

Returns a `ResolvedContact` dataclass:

```python
@dataclass(frozen=True)
class ResolvedContact:
    name: str | None
    roles: list[str]  # Sourced from public.entities.roles
    entity_id: UUID | None  # The routing key; the shape carries no contact identifier
    is_unidentified: bool = False  # public.entities.metadata.unidentified
```

**Resilience contract:** The function catches all database exceptions and returns `None` gracefully. It is safe to call before migrations have run, during partial startup, or when the database is temporarily unavailable.

### Unknown Sender Handling

When `resolve_contact_by_channel()` returns no match, the system creates a temporary identity via `create_temp_entity()`:

1. Creates a `public.entities` row with `metadata = {"unidentified": true}`, `entity_type = "person"`.
2. Asserts a fact via `relationship_assert_fact()` linking the entity to the channel identifier. For example: `relationship_assert_fact(entity_id, "has-email", "unknown@example.com")` or `relationship_assert_fact(entity_id, "has-telegram", "unknown-chat-id")`.
3. Uses `ON CONFLICT DO NOTHING` semantics for race safety when concurrent requests arrive from the same unknown sender.
4. Returns a `ResolvedContact` with empty roles.

Temporary entities are distinguishable from permanent ones by the `metadata = {"unidentified": true}` flag. They are intended to be merged or promoted by operator action via the dashboard.

### Identity Preamble

The `build_identity_preamble()` function constructs a structured text prefix prepended to every routed message. The format varies by sender classification:

- **Owner:** `[Source: Owner (entity_id: <uuid>), via telegram]`
- **Known contact:** `[Source: Chloe (entity_id: <uuid>), via telegram]`
- **Unknown sender:** `[Source: Unknown sender (entity_id: <uuid>), via telegram -- pending disambiguation]`

This preamble provides downstream butlers with:

- Sender name for personalized responses
- `entity_id` for entity-linked memory retrieval and fact anchoring
- Channel source for reply targeting
- Disambiguation status for trust-level decisions

### Owner Contact

The owner entity is bootstrapped automatically on daemon startup. It carries the `"owner"` role, which is used for:

- **Identity preamble** -- Owner messages are identified with `[Source: Owner ...]`.
- **Approval gates** -- Certain sensitive tool calls may require owner authorization.
- **Routing priority** -- Owner messages receive preferential queue ordering in the email priority tier system (see RFC 0003).
- **Credential anchoring** -- Owner entity_info entries store identity-bound credentials (e.g., Telegram user-client session, Google OAuth tokens).

### Context-Aware Recipient Resolution

`notify()` accepts an optional `msg_context` parameter (`"personal"`, `"work"`, or `"other"`).
When provided, it influences both recipient selection and the approval gate.

#### Recipient selection (contact_id path)

When `contact_id` is given, `_resolve_entity_channel_identifier()` reads the channel's predicate
from `relationship.entity_facts` for that entity. Triples carry no context column, so resolution
does not prefer a matching-context address; `msg_context` is enforced only by the mismatch gate
below.

#### Context mismatch approval gate

After recipient resolution, the email guard (`check_email_recipient()`) checks whether the
resolved address's `context` conflicts with the caller's `msg_context`.

**Conflict definition:** both `msg_context` and `address_context` are non-NULL and differ.
Unclassified (NULL) addresses are always compatible — they never trigger a park.

**On conflict:** the delivery is parked as a `pending_action` for human review, regardless
of whether a standing approval rule would otherwise permit it.  This prevents a "work"
email from being sent when the butler declared `msg_context="personal"`.

#### Default context inference

Callers may omit `msg_context`. When omitted, no mismatch check occurs.  Butler-level context defaults are not currently enforced
in code; future work may map butler domains (e.g. `health`, `lifestyle` → `"personal"`)
to a default context automatically.

### Usage Points

Identity resolution is invoked at:

| Integration Point | Purpose |
|-------------------|---------|
| Switchboard ingestion (RFC 0003) | Resolve sender identity, build preamble for routed messages |
| `notify()` tool | Resolve outbound recipient from `contact_id` to channel-specific address; `msg_context` feeds the mismatch gate |
| Approval gate (email guard) | Role-based access + context mismatch detection for outbound email delivery |
| Memory module | Anchor facts and episodes to the correct entity for retrieval |

## Integration

- **RFC 0003:** The Switchboard resolves sender identity during ingestion and prepends the identity preamble to routed messages.
- **RFC 0005:** Identity resolution failures are logged but do not create OTel error spans; the system degrades gracefully to unknown-sender handling.
- **RFC 0006:** Identity tables live in the `public` schema, readable by all butlers. Writes are primarily controlled by the contacts module in the relationship butler.
- **RFC 0007:** The dashboard exposes contact management, entity detail views, and unknown-sender disambiguation workflows.

## Amendments Applied

### Amendment 2 (2026-05-19) — Contacts as RDF Triples

Applied per `openspec/changes/archive/2026-05-20-relationship-tabs-to-entities/rfc-amendments/0004-amendment-2-contacts-as-triples.md`.

**Summary:** The 3-table identity model (`public.entities`, `public.contacts`, `public.contact_info`) collapsed to a 2-table model (`public.entities` + `relationship.entity_facts` triple store). Contact channel identifiers are now stored as RDF facts (e.g., `(entity_id, "has-email", "alice@example.com")`), eliminating two-table indirection and providing native multi-valuedness, provenance, and verification status.

**Changes made:**
- §"public.contacts" and §"public.contact_info" marked as superseded with forward pointer to `openspec/specs/relationship-facts/spec.md`
- §"resolve_contact_by_channel()" updated to query the triple store instead of joining contacts and contact_info tables
- §"ResolvedContact dataclass" simplified to remove `contact_id` field; now carries `entity_id` only
- §"build_identity_preamble" updated to emit `entity_id` only (removed `contact_id`)
- §"Unknown Sender Handling" updated to reference `create_temp_entity()` and `relationship_assert_fact()` instead of the deprecated contact creation path
- `public.entity_info` remains unchanged and out of scope for this amendment

### Amendment 3 (2026-06-18) — entity_info/entity_facts Seam Law

**Summary:** Formally declares the sensitivity-based seam between the two identity stores.
The split axis is **SENSITIVITY**, not TYPE.

- **`relationship.entity_facts`** is the single source of truth for ALL non-secret facts,
  identifiers, and relationships — including channel routing handles such as `telegram_chat_id`.
- **`public.entity_info`** is a **credentials-only** store. It holds ONLY `secured=True`
  credential entries (OAuth tokens, API hashes, session strings) plus a narrow whitelist of
  non-secret technical configuration entries that have no predicate home in entity_facts
  (`telegram_api_id` — Telegram client API credential component; `home_assistant_url` —
  service URL config entry).

**Changes made:**

- `telegram_chat_id` was added to the predicate-mirror constants mapping it to `"has-handle"`:
  - `src/butlers/identity.py` — `_CHANNEL_TYPE_TO_PREDICATE`
  - `roster/relationship/tools/relationship_assert_fact.py` — `_CI_TYPE_TO_PREDICATE`
  - `roster/relationship/jobs/relationship_jobs.py` — `_CI_TYPE_TO_PREDICATE`
- Approval-specific primacy mirrors were retired by RFC 0017 §2.7; outbound
  owner authorization now uses the canonical identity mapping directly.
- Write-time guard `assert_entity_info_secured(info_type, secured)` added to
  `src/butlers/credential_store.py`; raises `ValueError` when a non-secret, non-whitelisted
  type is passed. Guard wired at:
  - `upsert_owner_entity_info()` — function-call level enforcement
  - `POST /api/relationship/entity-info` — HTTP API level enforcement (HTTP 422 on violation)
- `public.entity_info` table documentation updated to reflect the seam law, add a `secured`
  column note, add whitelisted non-secret types (`telegram_api_id`, `home_assistant_url`),
  and show the migration table for previously misrouted types.

**Previously misrouted types** (were written to `entity_info` with `secured=False`; must now
go to `relationship.entity_facts`):

| Old `info_type` | New entity_facts predicate | Object format |
|-----------------|---------------------------|---------------|
| `telegram` | `has-handle` | `telegram:<id>` |
| `telegram_chat_id` | `has-handle` | `telegram:<id>` |

**Not in scope:** This phase is declaration + write-guard + predicate-mirror consistency only.
No destructive Alembic migration, no table rename, no data backfill. Those are later phases
of epic bu-oluyt.

## Alternatives Considered

**Per-butler contact tables.** Rejected because every butler needs sender context for personalized responses. Duplicating contact data across schemas would create synchronization problems and inconsistent identity states.

**Roles on contacts instead of entities.** The retired `public.contacts.roles` column was this alternative. Roles are now sourced from the entity, which is the canonical anchor. A contact is a named reference to an entity, not an independent role bearer. The entity-centric model supports multiple contacts per entity (e.g., a person with both personal and work email addresses) sharing the same role set.

**Inline identity in message payload.** Rejected in favor of the structured preamble. Inline identity would require every butler to parse arbitrary message formats. The preamble provides a predictable, machine-readable prefix that LLM sessions can consistently interpret.

---

## Amendment 4: Source-owned public identity-channel projection (proposed)

**Status:** Proposed while this PR is unmerged; owner merge is the adoption
event. Implementation is separate.
**Decision basis:** The owner's 2026-10-14 `identity-direct-read` choice D authorizes
this amendment draft. Independent review and owner merge are required for adoption.
Implementation is `bu-ys9hdx.4`; the measured foreign-schema fence is `bu-ys9hdx.5`.
Neither a source-only validation receipt nor adoption authorizes a live backfill.

### 4.1 Boundary and precedence

Relationship's `relationship.entity_facts` remains the canonical, private RDF
store. Deterministic identity infrastructure reads its channel projection in
`public`, not the private table or a function that reads it on the caller's
behalf. This removes the identity-specific private-schema read sanctioned by
Amendments 2 and 3; it does not add a third private-schema exception to vision
rule 3. The owner-approved credential seam, technical whitelist, entity-only
return shape and transitory reservation/notification flow are unchanged.

Upon adoption, sections 4.2–4.7 supersede this RFC's earlier direct-read SQL,
its assertion that all identity tables have unrestricted public-schema read
access, and any identity-specific definer bridge into Relationship. Earlier
amendments remain historical records. Current implementation still contains
those private reads until the separately reviewed implementation cuts over.
The matching OpenSpec delta replaces the two direct-read requirements and adds
the projection contract; unrelated canonical and active requirements remain in
force. A candidate changeset does not silently override adopted runtime behavior.

RFC 0031 supplies the source-owned, derived projection pattern only. Its
entity-graph table, private assertion semantics, withheld stubs, grants and open
bootstrap-grant rider are unchanged. The new channel table has its own explicit
ACL/bootstrap contract below; it does not inherit an unresolved graph grant.
RFC 0017's owner-routing protections are also unchanged: identification is not
cryptographic authentication, primacy is not owner authorization, and an
ambiguous identifier cannot obtain an owner bypass.

### 4.2 Row contract and eligibility

The table is `public.identity_channel_projection`. It is rebuildable derived
state, never a fact-write API or independent source of truth. Exactly one row
represents each eligible source fact, keyed by that fact's actual UUID:

| Column | Type / constraint | Meaning |
|---|---|---|
| `source_fact_id` | UUID PRIMARY KEY | Actual `relationship.entity_facts.id`; stable replay/upsert/delete key, not caller attribution |
| `entity_id` | UUID NOT NULL, FK `public.entities(id)` ON DELETE CASCADE | Actual source `subject`, joined to the current live entity |
| `predicate` | TEXT NOT NULL, CHECK in `has-handle`, `has-email`, `has-phone`, `has-website` | Existing channel predicate, not a new transport taxonomy |
| `value` | TEXT NOT NULL | Exact canonical source `object`; no normalization on write |
| `is_primary` | BOOLEAN NULL | Exact nullable source `"primary"`, for existing delivery ordering only |
| `source_created_at` | TIMESTAMPTZ NOT NULL | Source `created_at`, preserving current oldest-first ordering |
| `context` | TEXT NULL | Only the source `metadata->>'context'` routing label, preserving existing email-context comparison; never the whole metadata object |

Indexes support `(predicate, value, entity_id)` reverse lookup and
`(entity_id, predicate, is_primary, source_created_at)` forward selection.
There is **no** global unique handle or normalized-value constraint: existing
multiple facts and cross-entity collisions must remain visible as ambiguity,
not be arbitrarily resolved or prevented by a new uniqueness policy. The source
UUID is provenance, not a secret, authentication token or entity-incarnation
witness. No foreign key to a private table is needed by public readers.

Eligible rows have one of the four predicates, `object_kind='literal'`, and
`validity='active'`, subject to the already adopted candidate/owner-adoption and
entity-liveness contracts. Candidate, pending, rejected, retracted or superseded
rows are absent. Legacy active rows with NULL attribution remain eligible;
NULL does not mean owner authority. Already eligible transitory facts retain
existing deterministic dedup behavior. The source's current temporal reader
semantics are preserved; this amendment neither expires extra rows based on a
new clock rule nor revives expired/ineligible rows. Readers join
`public.entities` and reject merged/deleted entities using the existing markers.

Only the listed channel fields are copied. Private reporter tokens, lifecycle
witnesses, owner-confirmation/evidence packets, arbitrary fact metadata,
credentials, session strings and `public.entity_info` values are excluded.
`context` preserves the existing routing label exactly, including NULL; the
implementation must audit its actual source population rather than replace an
unrecognized label with NULL or copy unrelated metadata. Context is a routing
classification, not permission, a preferred-address ranking or sender authority.
Names, roles and the explicit `unidentified` marker are read from the current
`public.entities` row, never a stale duplicated projection snapshot.

### 4.3 Source-owned atomic maintenance

The concrete candidate mechanism is a Relationship-migration-owned
`AFTER INSERT OR UPDATE OR DELETE` row trigger on `relationship.entity_facts`,
calling `relationship.maintain_identity_channel_projection()` (RETURNS TRIGGER)
owned by the existing `butler_relationship_rw` role, with
`SET search_path = pg_catalog, pg_temp` and fully qualified source/target names.
It projects eligible NEW rows
by `source_fact_id` and deletes an OLD row when deleted or no longer eligible.
The function uses narrowly scoped `SECURITY DEFINER` solely for derived writes:
an existing public-entity deletion can cascade source deletions under another
runtime role, whose projection DML remains denied. It returns `trigger`, accepts
no arguments, admits only its exact source relation and row events, uses a fixed
safe search path and qualified objects, and has no PUBLIC/runtime direct EXECUTE
grant or callable read interface. It consumes OLD/NEW source tuples, never
caller SQL or a foreign-read request. This source maintenance function grants no
role access to Relationship and is distinct from the retired owner-read bridge.
Relationship's existing source-admission,
canonicalization, entity/handle/fact lock order, candidate lifecycle and exact
owner-adoption checks run before source writes; the projection cannot mint or
repair any of that private authority.

Every successful source statement updates source and projection on the **same
connection and transaction**. There is no asynchronous queue, polling task,
after-commit callback, independent pool acquisition or best-effort savepoint
that commits a source write after mandatory projection failure. A projection
constraint, permission, function or table failure fails that source operation;
transaction rollback restores both. A deliberately handled source savepoint
may roll back the entire failed source-plus-projection statement, never only
the projection. Other explicitly optional secondary effects retain their
existing contracts; they do not make this projection optional.

This covers all actual Relationship producers: single/batch assertions,
channel MCP tools and mounted API operations, import/repair/backfill, exact
candidate adoption/rejection, promotion, rotation, supersession/retraction,
Relationship's merge subscriber and deletion/restore recovery. Bulk SQL and
`COPY` must fire the trigger. No runtime role may disable it, use replica-mode
bypass, or issue `TRUNCATE` on either participating table. Because row triggers
do not maintain a TRUNCATE, the source migration also installs a
`BEFORE TRUNCATE FOR EACH STATEMENT` refusal trigger on `entity_facts` while
mandatory maintenance is active. That fixed, trigger-only invoker guard raises
without source/projection effects, even if bootstrap regrants source TRUNCATE;
the public projection's ACL separately denies runtime TRUNCATE. A controlled
migration needing wholesale removal must quiesce readers/writers and reconcile
both stores before reactivation, never silently bypass this guard. Future migrations
that alter source rows must keep the trigger active or reconcile within the
same controlled migration transaction before readers are enabled. Core-only
or Relationship-absent databases may have an empty projection without a
private-schema access attempt; the Relationship migration installs its trigger
before any eligible source mutation is admitted in the migrated deployment.

Update uses the source UUID as an upsert key, so an unchanged retry does not
append another row. Replacement with a new source UUID retracts/deletes the
old projection and creates the new one in the actual source transaction.
Candidate owner adoption inserts only the source's compatible active survivor;
a losing/stale/colliding adoption leaves both source and projection unchanged.
Retraction, eligibility loss and hard source deletion remove the projected row
in the same transaction. Entity deletion cascades remove projected rows, and
merge/tombstone checks prevent stale identity or owner resolution. A public
entity update cannot transfer a private fact to a different subject; existing
Relationship-owned merge receipts and source reconciliation still govern that
change. Recreating an entity UUID alone cannot reconstruct a deleted fact.

The trigger is the consistency mechanism under the adopted trusted runtime/DB
premise, not proof against a trusted administrator or authorized Relationship
code deliberately modifying derived state. Runtime trigger disabling and
independent projection edits are forbidden by this contract and checked in the
implementation's role/operation inventory; migration/admin recovery remains
separately gated.

### 4.4 Exact grants and bootstrap survival

Channel identifiers are personal data even though they are not credentials.
`public` is a schema name, not an anonymous disclosure policy. The projection
has no default/public reader and adds no generic handle-enumeration MCP tool,
public HTTP endpoint or direct LLM database access.

The named supported runtime readers are the existing roles
`butler_chronicler_rw`, `butler_concierge_rw`, `butler_education_rw`,
`butler_finance_rw`, `butler_general_rw`, `butler_health_rw`, `butler_home_rw`,
`butler_lifestyle_rw`, `butler_messenger_rw`, `butler_qa_rw`,
`butler_relationship_rw`, `butler_switchboard_rw`, and `butler_travel_rw`.
Their deterministic daemon identity/notification/approval paths need the
same channel lookups; granting only Switchboard would break owner-directed
checks in other daemons. `connector_writer` receives SELECT only for existing
connector sender-discretion resolution. Dashboard identity paths must use an
existing named runtime pool role; the shared login fallback is not a supported
projection consumer and must be refused by protected consumer admission before
the lookup, even when that login has independent administrative privileges.
A missing named role is skipped safely by provisioning, not created or assigned
credentials here. The already recognized legacy `butler_calendar_rw` is an
additional conditional SELECT reader only when it exists, preserving its
existing deterministic owner/notification lookup capability; provisioning does
not create it or expand its calendar/RFC 0020 surfaces. Other optional, future
or custom roles are denied until their actual consumer and least-privilege
grant are separately reviewed; a new roster entry does not automatically
inherit access through PUBLIC or default privileges.

Those readers receive table SELECT and public-schema USAGE only. Existing
`public.entities` access is not broadened. Runtime INSERT/UPDATE/DELETE is
reserved to `butler_relationship_rw`, used by the source-owned trigger.
PUBLIC and all other runtime roles receive no projection DML, TRUNCATE,
REFERENCES, TRIGGER, table ownership, grant option or privileged function
execution. The provisioning/migration owner remains trusted for DDL and
explicit recovery; it is not a runtime reader by fallback. The trigger-only
maintenance function has fixed qualified names/search paths, no caller-controlled
SQL, and only the existing Relationship owner's derived-write privileges;
no new service role or credential is introduced.

The table must have **ENABLED and FORCED RLS**. Its SELECT policy admits only
the enumerated actual `current_user` runtime roles, and its INSERT/UPDATE/DELETE
policies admit only Relationship. No caller field, entity UUID, request label,
GUC or claimed owner role selects a policy. Actual protected pools apply the
server-controlled runtime `SET ROLE` before querying. Both ACLs and policies
are required; ordinary role membership cannot be treated as a per-process
credential boundary against the shared connecting login, a trusted admin,
the table/migration owner or a BYPASSRLS/superuser. This amendment introduces
no new role separation, credential custody or claim to resist those actors.

Current bootstrap and some core migrations broadly regrant public-table
privileges. A projection migration's REVOKE alone is insufficient. The future
implementation must add a **table-specific exception and final ACL/policy
installer** to the owning bootstrap path, remove prohibited privileges on this
one table after applicable broad grants, and retain the forced policies.
Replay core/Relationship migrations and actual `scripts/init-db.sql`, then
verify SELECT under each named reader, SQL denial for an ordinary non-reader
and application admission refusal for shared-login fallback (including trusted
administrative logins), and rejected DML/TRUNCATE/trigger control under ordinary non-writer
roles. Role grants/default privileges and bootstrap replay must be inspected
as well as successful queries. Do not solve this by broadening private grants
or changing RFC 0031's unrelated graph grant posture. Bootstrap compatibility
and real-role negative controls are implementation acceptance gates, not facts
established by this docs-only PR.

### 4.5 Readers, missing data and owner authority

Inbound exact, normalized and bulk identity reads, owner-channel corroboration,
notification recipient selection, email context classification and connector
sender-discretion lookups use only the projection plus `public.entities`.
Relationship-owned domain tools/API may still read their own source facts;
no other identity caller may fall back to private SQL, a private-schema view,
a definer bridge, credentials or name matching when the projection misses.

The existing channel-to-predicate map and transport aliases are preserved:
Telegram stores `telegram:`-prefixed handles; email and phone use `has-email`
and `has-phone`; websites use `has-website`; existing non-Telegram handle
formats are not rewritten. Existing email header/case canonicalization,
WhatsApp JID/phone equivalents, phone digit suffix minimum/delta bounds,
exact-before-fallback order and bulk candidate grouping remain unchanged.
Distinct matching facts for one live entity do not manufacture ambiguity;
multiple live entities across the full equivalent candidate universe return
no match. A reader must not filter to owner before measuring ambiguity.

Names, roles and explicit transitory state retain the `ResolvedContact`
entity-only shape. An absent/ineligible row, ambiguous result or unavailable
projection returns no established identity. Inbound traffic remains eligible
for ordinary unresolved-sender routing and the already adopted transitory
reservation and durable owner-notification flow; no contact row, duplicate
notification, false surfaced entity or source write in Switchboard is added.
Failures are reported by bounded reason/count diagnostics without raw handles,
message bodies or private attribution. A DB failure is not a successful
backfill or proof that the sender is genuinely unknown.

For owner-directed approval bypass, missing/ambiguous/unavailable identity
never implies owner. Existing standing rules, pending-action parking, dossier
validation and owner-authenticated execution remain authoritative. Preserve
RFC 0017's entire normalized candidate universe and both owner/non-owner
collision negatives. Delete the private definer fallback after cutover;
replace its decision with one public-only grouped lookup. A compatibility
wrapper, if retained temporarily for callers, must be invoker-only and read
only public tables; it must not retain privileged EXECUTE authority. Primacy
continues to order delivery, not authorize owner bypass. Non-owner explicit
email-context mismatches still park, NULL remains unclassified, and this
amendment adds no context-preference sorting or authentication claim.

### 4.6 Backfill, concurrency, recovery and rollback

Install the table/roles/policies and mandatory source trigger before backfill
and before switching readers. A future migration seeds existing eligible
facts from the real canonical chain. In a Relationship-absent database it
creates no invented source or success claim. Existing NULL attribution is
copied as eligible source state where already allowed; it is never repaired
into owner admission or a private reporter lifecycle witness.

Backfill uses finite, resumable source-UUID keyset batches. For each batch,
plan the complete affected entity/handle set, acquire the existing sorted entity
and canonical handle locks, then obtain a short source-table `SHARE` lock with
`NOWAIT` before exact fact/context locks. Refusal rolls back and retries within
a finite bound, rather than waiting while holding locks against a source writer.
After the table lock, re-read the planned population: a changed subject/handle
set requires rollback and replanning, not an out-of-order extra lock. Within
that short batch transaction source mutations are blocked, so eligibility and
values are re-read **after** synchronization and keyed upserts/deletes cannot
race a phantom source insertion. Concurrent assertions, adoption,
retraction, subject changes and deletion must be reflected by the trigger or
locked re-read; an old snapshot cannot resurrect a retracted row. A batch
failure rolls back that batch, reports its bounded checkpoint and leaves no
false completion marker. Restart/replay is idempotent and does not undo newer
source changes. Orphan cleanup uses the same bounded table synchronization and
rechecks each exact source key before deleting; it must not delete a concurrent
live write based on an earlier snapshot. The migration declares its batch/time
bounds and temporary writer-blocking impact; contention exhaustion is an
incomplete backfill, not permission to weaken synchronization or widen a lock
timeout silently. There is no long-lived source-table lock or guessed completion
from the last UUID alone.

Cutover requires more than a row count: compare the exact eligible
`source_fact_id` set and every projected field to the locked/consistent source
snapshot, preserve cross-entity ambiguity, and execute old/new resolver parity
for exact/normalized/bulk/forward/owner/context paths under real roles.
Backfill/cutover receipts identify code and migration revisions, environment,
complete population and failure state without exporting raw handles. Partial
population, unavailable source or an incomplete receipt blocks cutover. No
current-state or runtime proof is supplied by this candidate.

After a trusted restore, source and projection must come from one consistent
snapshot with compatible code/trigger/policy revisions. If projection freshness
cannot be proven, keep identity consumers unavailable/fail closed while an
authorized Relationship reconciliation rebuilds from the actual source. An
independent source or projection restore is not accepted as an equivalent
ready state. Old private lifecycle capabilities retain their existing restore
invalidation/re-admission rules; a rebuild cannot refill historical NULL
attribution or mint owner evidence.

Rollback disables the affected identity consumers or leaves misses unresolved
while preserving canonical facts, credentials and adoption receipts. It does
not automatically restore private-schema resolution, grant wider access,
disable the trigger while source writes continue, or drop the projection
beneath active readers. Code plus schema/trigger/policy revisions must be
coordinated; reversal of a live migration/backfill requires its own reviewed
environment gate. A new choice to restore private-schema reads would require
owner adoption of a different contract, not an operational escape hatch.

### 4.7 Implementation allocation and proof boundary

This PR changes only the contract/doctrine and an unadopted OpenSpec delta.
There is no migration number reservation, grant execution, product edit,
live DB inspection, backfill, deployment or implementation-test claim.

| Future owner/surface | Obligation retained for implementation |
|---|---|
| Relationship source migration and actual fact producers | Trigger installation; all insert/update/delete/COPY/replay/adoption/merge/import paths; private authority and full source packet preserved |
| Core migration/bootstrap owner | Public table, fixed role ACL/policies, core-only safety, full bootstrap/default-grant replay and no private function bridge |
| `src/butlers/identity.py` and Switchboard pipeline | Exact/normalized/bulk lookup, ambiguity/unknown flow, explicit entity-only shape, public-only owner corroboration |
| Daemon/core notifications and approvals gate/email guard | Forward ordering; all runtime roles; existing owner, dossier, context, parking and pending-action failure semantics |
| Connector sender discretion and dashboard identity consumers | Existing runtime pool roles and actual aliases; no added enumeration API or permissive login fallback |
| Existing Relationship merge/adoption and RFC 0017 owners | Complete lifecycle/replay/lock ordering and authorization survivors; no source-owner takeover |
| Separate `.4` implementation review | Real migrated PostgreSQL: committed readback, injected projection failures, both race orders, delete/retraction/adoption/merge, replay/backfill/restore and role/bootstrap negatives |
| Separate `.5` fence | Measure actual foreign SQL and preserve its explicit RFC 0010/0020, RFC 0014, RFC 0030 and QA allowances; no guessed 18-file claim |

`.4` remains gated on this amendment's adoption and must reconcile its actual
source owners before implementation. `.5` remains gated on the implementation.
The held run-18 production gate is unchanged. Native spec/parser/preservation
checks prove a coherent source candidate only. Independent security/spec review
and owner merge are still required before this contract is adopted.
