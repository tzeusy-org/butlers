## MODIFIED Requirements

### Requirement: Provenance contract — every fact carries its origin

Every entity-scoped endpoint MUST include provenance fields on every triple it returns.
The affected endpoints are: `/api/relationship/entities/{id}/contacts`,
`/api/relationship/entities/{id}/neighbours`,
`/api/relationship/entities/concentration`,
`/api/relationship/entities/queue`,
`/api/relationship/entities/{id}/{notes,interactions,gifts,loans,timeline}`,
and `/api/relationship/entities/search`. Provenance fields are defined in the
`relationship-facts` capability spec:
- `src` (TEXT, NOT NULL): butler that wrote the fact.
- `conf` (FLOAT 0..1, NOT NULL): confidence score, default 1.0 for owner-authored.
- `last_seen` (TIMESTAMP, NULLABLE): most recent observation of the triple.
- `weight` (INT, NULLABLE): aggregation weight for relational predicates.
- `verified` (BOOL, NOT NULL, default false): owner-confirmed flag.
- `primary` (BOOL, NULLABLE): primary-of-kind flag (for multi-valued contact predicates).
- UI rendering MAY hide these fields (Editorial mode); the API MUST NOT silently drop
  or omit them. Omission is a contract violation (per Brief §0 binding intent).
- **Envelope reconciliation (R1 D2):** success responses from all new entity endpoints are
  unwrapped per the relationship-domain convention (`rfcs/0007:88-91` exemption from the
  default envelope). Error responses MUST be wrapped per `rfcs/0007:75-87` so the `code`
  discriminator (`owner_required`, `entity_not_found`, etc.) is uniformly available to
  clients regardless of which endpoint raised the error.
- Owned identity-fact projections SHALL also carry explicit content authority, original author availability and confirmation_status, retaining raw verified and all old provenance keys. Identity rendering SHALL say Reported by <actual canonical sender>, Reported by an unknown sender, Recorded by the system, or Author unknown (legacy). Subject/src/body actor SHALL not be substituted for reporter. Owner confirmation SHALL be distinct from confidence, staleness and original authorship. Optional shared rendering extensions SHALL preserve narrative-memory consumers. Candidate reports SHALL appear only in protected review with no tel/mailto/send/preferred-channel affordance.
- A deleted reporter SHALL be rendered as Reported by an unknown sender (author deleted), with explicit deleted author availability and no identity/navigation/send link. The immutable original ID SHALL remain an inert provenance token; projections SHALL not join it to a recreated entity or substitute subject, owner, src, caller actor or a copied old name. Forgotten/merged unavailability and unresolved/system/legacy states SHALL remain distinguishable.

ID: REQ-dashboard-relationship-003
Source: bu-s11n0s.2 original Outcome/S1–S4; heart-and-soul/security.md; Relationship MANIFESTO.md; adopted dashboard-owner-auth and existing capability contract
Scope: v1-mandatory

#### Scenario: Provenance fields are present on every triple response
- **WHEN** any of the listed entity-scoped endpoints returns at least one triple-derived
  row to the client
- **THEN** every row MUST include the keys `src`, `conf`, `last_seen`, `weight`,
  `verified`, and `primary` (nullable values are explicit, never omitted)
- **AND** the API response MUST NOT silently drop any provenance field
- **AND** UI code MAY choose not to render the fields in Editorial mode, but the API
  contract is unaffected by render choice

#### Scenario: Error envelope carries `code` discriminator
- **WHEN** an entity-scoped endpoint returns a non-2xx response (for example 403
  `owner_required` or 404 `entity_not_found`)
- **THEN** the error payload MUST be wrapped per `rfcs/0007:75-87` with a `code` field
- **AND** clients MUST be able to dispatch on `code` uniformly across all entity endpoints


#### Scenario: Reporter and confirmation are separate

- **WHEN** a third-party report about another person is owner-confirmed
- **THEN** every owned identity response/rendering SHALL retain its actual reporter and distinct owner confirmation
- **AND** legacy/system/unknown states SHALL be explicit without guessed owner authorship


#### Scenario: Deleted reporter is unknown without relabelling the fact

- **WHEN** authorized reporter-only hard deletion leaves a surviving owner-confirmed or unconfirmed fact
- **THEN** the protected API and actual identity UI SHALL show deleted-author unknown and retain stored authority, original token and separate confirmation
- **AND** no reporter identity link or guessed/recreated author SHALL appear
- **AND** an independent live-reporter positive SHALL still display its permitted canonical name and link

## ADDED Requirements

### Requirement: Owner-admitted candidate Adopt door

The entity contact card SHALL expose an accessible exact Adopt and Reject door for candidate reports through the mounted protected API. Central actual auth/unsafe CSRF/Origin admission precedes body/domain access; the operation SHALL validate live owner/subject/candidate version/collision and commit one reporter-preserving receipt with its domain effects. Pending,401,409,network loss and unknown acknowledgement SHALL remain honest; no automatic replay after login or guessed success. Reporter/confirmation/legacy labels SHALL not create sending authority.

ID: REQ-dashboard-relationship-004
Source: bu-s11n0s.2 original Outcome/S1–S4; heart-and-soul/security.md; adopted owner-auth request admission; existing relationship/entity contracts
Scope: v1-mandatory

#### Scenario: Adopt door reflects actual receipt

- **WHEN** the owner activates the candidate Adopt control
- **THEN** only that row SHALL show pending until a committed receipt/readback is known
- **AND** success SHALL refresh relevant facts/contact/routing queries while preserving reporter

#### Scenario: Denied or interrupted action remains honest

- **WHEN** auth refuses, a collision/stale version returns409 or commit acknowledgement is lost
- **THEN** the card SHALL retain the report and actual refusal/unknown state
- **AND** it SHALL not claim saved, silently retry or expose candidate send affordances
