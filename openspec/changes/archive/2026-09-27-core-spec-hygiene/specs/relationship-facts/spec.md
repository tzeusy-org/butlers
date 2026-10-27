## REMOVED Requirements

### Requirement: Migration safety — dual-write, parity, cut-over
**Reason**: The contacts-to-triples cut-over is finished: `public.contacts` and `public.contact_info` are dropped, and there is no dual-write or parity window left to govern.

**Migration**: None. `relationship.entity_facts` is the only store; see "Relationship entity facts triple store".

### Requirement: Orphan contact handling
**Reason**: The orphan resolver was a one-time migration tool whose source tables have been dropped; the requirement only forbade resurrecting it.

**Migration**: None. There are no orphan contact rows left to resolve.

## MODIFIED Requirements

### Requirement: Switchboard `resolve_contact_by_channel()` re-points to triples

The Switchboard's `resolve_contact_by_channel()` function (defined in RFC 0004 §"resolve_contact_by_channel()", `rfcs/0004:83-95`) SHALL query `relationship.entity_facts` (the dropped `public.contact_info` table is never read). The query MUST use the following SQL shape:

```sql
SELECT f.subject AS entity_id,
       e.canonical_name AS name,
       COALESCE(e.roles, '{}') AS roles
FROM relationship.entity_facts f
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

The `ResolvedContact` return shape carries `entity_id` as the routing key in place of the
retired `contact_id`, and the identity preamble references only `entity_id`.

#### Scenario: Telegram chat resolves to entity via has-handle triple
- **WHEN** an incoming Telegram message arrives with chat_id `12345` and a triple
  `(subject=ent-7, predicate='has-handle', object='telegram:12345',
  object_kind='literal', validity='active')` exists in `relationship.entity_facts`
- **THEN** `resolve_contact_by_channel('telegram', 'telegram:12345')` MUST return a
  `ResolvedContact` with `entity_id = ent-7`
- **AND** the returned shape MUST NOT include a `contact_id` field

#### Scenario: Unknown channel value returns no match
- **WHEN** `resolve_contact_by_channel('email', 'nobody@example.com')` is called and no
  triple with predicate `has-email` and object `nobody@example.com` exists
- **THEN** the function MUST return `None` (or equivalent absent-match sentinel)
- **AND** the identity preamble builder MUST treat the caller as unknown

### Requirement: Credentials carve-out

Secured contact details (credentials such as encrypted session blobs, OAuth tokens, or
secret-store pointers) SHALL live in the non-triple `relationship.credentials` table and MUST
NOT be stored in `relationship.entity_facts` as triples. `relationship.credentials` is keyed by
`entity_id`, holds at most one active (non-revoked) credential per `(entity_id, type)`, and is
writable only by the relationship butler; other butlers reach credentials only through the
relationship butler's tool surface.

#### Scenario: Credentials are never stored as triples
- **WHEN** a secured credential is recorded for an entity
- **THEN** it MUST be written to `relationship.credentials`
- **AND** no triple carrying the credential value MUST exist in `relationship.entity_facts`

#### Scenario: Credentials are not surfaced on entity contacts endpoint
- **WHEN** `GET /api/butlers/relationship/entities/{id}/contacts` is called for an entity
  that has both a non-secured `has-email` triple and a secured credential
- **THEN** the response MUST include the non-secured email
- **AND** the response MUST NOT include the credential row from `relationship.credentials`

### Requirement: `verified` is a column, not a triple

The `verified` field SHALL be a **column on `relationship.entity_facts`** and MUST NOT be modeled
as a separate verification-triple (`(triple_id, verified-by, owner)`). Rationale: pure RDF would split it out, but every triple needs a verified
flag, the verifying actor is always the owner (single-user v1), and the column form keeps
query plans simple. If v2 introduces multi-actor verification, the column MAY be promoted
to a separate verification table at that point.

#### Scenario: Verifying a fact updates the column, not a new row
- **WHEN** the owner confirms an existing triple `(subject, predicate, object)` via the
  approval ceremony
- **THEN** the existing row in `relationship.entity_facts` MUST be updated to set `verified = true`
- **AND** no new `(triple_id, verified-by, owner)` row MUST be inserted
- **AND** the row's `validity` MUST remain `'active'`

#### Scenario: No verification-triple predicate is registered
- **WHEN** `relationship.entity_predicate_registry` is queried for all seeded predicates
- **THEN** no predicate named `verified-by` (or analogue) MUST appear in the registry
- **AND** any attempt to assert such a predicate MUST be rejected by the central writer
  per Requirement: Predicate catalog

### Requirement: Inferred relationship facts pass a confidence gate

An inferred relationship fact MUST carry a confidence value and provenance, and an inferred **family** relationship below the confidence bar MUST be proposed for confirmation rather than written as an active fact. ("Inferred" means derived by the system rather than stated directly by the owner.)

As built in `roster/relationship/tools/relationship_assert_fact.py` (`_FAMILY_GATE_PREDICATES`, `_FAMILY_GATE_CONF`), the gated family predicates are `parent-of`, `child-of`, and `family-of` (`_FAMILY_GATE_PREDICATES`), and the confidence bar is `_FAMILY_GATE_CONF = 0.8`. A call asserting one of those predicates with `conf < 0.8` is routed to `pending_approval` rather than written active.

#### Scenario: Low-confidence inferred family fact is not written active
- **WHEN** extraction infers a family relationship (e.g. "has a son") without direct owner confirmation and below the confidence bar
- **THEN** it MUST NOT be stored as an active fact
- **AND** it MUST be surfaced for owner confirmation before becoming active

#### Scenario: Inferred fact records provenance
- **WHEN** any relationship fact is stored from inference
- **THEN** it MUST record its confidence and the source it was inferred from
