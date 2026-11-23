## MODIFIED Requirements

### Requirement: Content authority and steering-class admission

Every episode, fact and rule SHALL carry a server-stamped `content_authority`
derived only from the Switchboard-resolved routing context of the runtime
session that wrote it (never from a caller argument, tool argument or text
inside content). The value SHALL be one of `owner`, `owner_device`,
`third_party`, `system` or `mixed`; `owner` and `owner_device` are the
*owner-class*. A sender entity equal to the owner entity yields `owner`, except
that when Switchboard's server-side custody read found that sender's source
endpoint held or unreadable (REQ-endpoint-custody-holds-005) it yields
`third_party` with the sender entity kept for attribution and the closed custody
reason `endpoint_held` stored as provenance, never as a sixth authority value; a
`dashboard` source channel with no sender entity yields `owner_device`; any
other routed sender (including an unresolved one) yields `third_party`; a
session with no routing context yields `system`. A write whose authority
cannot be stamped SHALL fail rather than store an unstamped steering artifact.
- Consolidation SHALL derive each fact and rule's authority as the weakest across
  that artifact's validated evidence episodes: all owner-class evidence yields
  `owner` (or `owner_device` when every episode is), evidence of one identical
  non-owner authority keeps it, and any other combination, including evidence
  whose authority is unknown (NULL), yields `mixed`. A consolidation update SHALL
  NOT supersede an owner-class fact with a non-owner-class one.
- A rule is *held* when its authority is not owner-class (or is NULL on a legacy
  row) and `endorsed_at` is NULL. Held rules SHALL be absent from recall, search,
  `memory_context` Active Rules, and the discovery catalog (they are not written
  to it, and any existing catalog row is staled). Profile Facts SHALL admit only
  owner-class, durable facts: a fact whose predicate is registered
  `is_temporal` in `predicate_registry` (event-class; a predicate absent from the
  registry is durable) SHALL NOT appear in Profile Facts
  even when owner-authored, and SHALL remain available to recall and search; an owner-anchored fact of any other authority SHALL be
  eligible only for Task-Relevant Facts. Every rendered local fact whose authority
  is `third_party`, `mixed` or NULL SHALL carry an attribution regardless of its
  subject: naming its sender (`reported by` / `partly reported by`) when the
  sender's canonical name resolves, an honest generic `a third party` when it
  does not, and marked unverified when the authority is NULL. A failed name
  lookup SHALL NOT remove the qualification, `source_butler` SHALL NOT stand in
  for the reporter, and section budgets SHALL drop whole lines rather than a
  line's attribution or memory reference. Legacy rows are never
  backfilled by guessing; the migration SHALL report how many legacy rules and
  facts it left unclassified.
- Every `public.memory_catalog` row SHALL carry the `content_authority` class of
  its source fact or rule (never the sender entity, which stays in the owning
  schema), written in the same statement as the rest of the row and re-copied by
  the catalog backfill when it drifts; a legacy NULL source stays NULL. Every
  cross-butler catalog reader (catalog search, `memory_catalog_search`, Fleet
  Knowledge, `GET /api/memory/catalog/search`) SHALL return it, and the memory
  tools SHALL attribute a non-owner item as from a third party (or mark it
  unverified when the authority is NULL).
- The owner SHALL be able to endorse a held rule. Endorsement sets `endorsed_at`
  and `endorsed_by`, makes the rule visible to recall and to the catalog in the
  same transaction, records an audit event, and is a no-op returning the same
  receipt when repeated. Endorsement uses compare-and-swap against a retired or
  forgotten rule: a retired or forgotten rule SHALL NOT be endorsed. Repeat
  consolidation of an episode SHALL NOT downgrade an endorsed rule.
- A fact is *held* when its authority is `third_party` or `mixed`, `endorsed_at` is
  NULL, and its validity is `active` or `fading`. NULL (legacy) and `system` facts
  SHALL NOT be held. The owner SHALL be able to vouch for a held fact through the
  authenticated dashboard owner route `POST /api/memory/facts/{id}/vouch`; a session
  or any caller without dashboard-owner authority SHALL be refused, and vouching
  SHALL NOT be exposed as an MCP tool. Vouching sets `endorsed_at` and
  `endorsed_by` only: it is additive and SHALL NOT change `content_authority`,
  `authority_entity_id` or any other field, so clearing `endorsed_at` restores the
  prior state exactly. It uses compare-and-swap against a retracted, superseded or
  expired fact, which SHALL be refused with a typed 409. It records one
  `fact_vouched` audit event, re-asserts the fact's discovery-catalog entry
  (skipped for write-excluded sensitivities), and is a no-op returning the same
  receipt when repeated or concurrent. `GET /api/memory/facts?held=true` SHALL list
  held facts. The dashboard Confirm action SHALL record its actor (`owner`) in
  `memory_events`; the MCP `memory_confirm` tool records `session`.
- A fact that keeps a stored fact's text or metadata (a successor) is a carve-out from
  deriving authority only from the routing context, conforming to RFC 0038 D6 and S1: its
  authority SHALL follow from its writer's authority and its source's. An owner-class writer
  SHALL store its own class over an owner-class source, `mixed` over a `third_party`, `mixed`
  or NULL source, and `system` over a `system` source. A `system` writer SHALL store `mixed`
  over a `third_party`, `mixed` or NULL source, and `system` over a `system` or owner-class
  source. A successor of a `third_party` or `mixed` source SHALL keep its
  `authority_entity_id`. A `mixed` successor is held: "SHALL NOT be held" applies to NULL and
  `system` rows themselves. A successor SHALL NOT inherit `endorsed_at`.
- Relationship registry identity facts are a separate authority-bearing store under relationship-facts. Its writer/gap callbacks SHALL carry persisted Relationship report authority and genuine stored confirmation rather than recomputing it from an unrelated current session. This cross-reference SHALL not change any episode/fact/rule classification, steering/profile admission, endorsement, weakest-evidence rule, NULL legacy treatment or run15 follow-up ownership in this memory capability.

ID: REQ-module-memory-013
Source: bu-s11n0s.2 original Outcome/S1–S4; heart-and-soul/security.md; Relationship MANIFESTO.md; adopted dashboard-owner-auth and existing capability contract; REQ-endpoint-custody-holds-005 (bu-s11n0s.1 D3: third_party plus endpoint_held, no sixth value)
Scope: v1-mandatory

#### Scenario: A held owner endpoint stores an attributed report

- **WHEN** a session routed from the owner entity through a held or unreadable-custody endpoint
  stores an episode or a rule
- **THEN** the row SHALL carry `content_authority = 'third_party'`, the owner entity as
  `authority_entity_id` and `custody_reason = 'endpoint_held'` in its metadata
- **AND** the rule SHALL be held: absent from `memory_context` Active Rules and the catalog
- **AND** a later release SHALL NOT change the stored row, while a new owner-routed session from
  the now unheld endpoint stamps `owner`

#### Scenario: A third-party routed session cannot create standing guidance

- **WHEN** a session routed for a non-owner sender calls `memory_store_rule`
- **THEN** the rule row SHALL exist with `content_authority = 'third_party'`
  and `endorsed_at IS NULL`
- **AND** it SHALL be absent from `memory_context` Active Rules and from
  `public.memory_catalog` search

#### Scenario: A query locator without a matching invocation token yields third_party

- **WHEN** an MCP request names a live owner session only by a `runtime_session_id`
  query locator and presents no valid invocation binding for this butler
- **THEN** the request SHALL see no routing context and a write SHALL carry
  `content_authority = 'third_party'`
- **AND** a request with a valid binding, an in-process caller outside a request, and a
  daemon request carrying neither locator nor binding SHALL be unchanged

#### Scenario: Mixed evidence is held

- **WHEN** consolidation derives one rule from one owner episode and one
  third-party episode
- **THEN** the rule SHALL have `content_authority = 'mixed'` and SHALL be held

#### Scenario: A successor keeps its source's non-owner class

- **WHEN** an owner session edits a fact it copies forward
- **THEN** a successor of an owner-class source SHALL be owner-class
- **AND** a successor of a `third_party` or `mixed` source SHALL be `mixed`, held, and name
  the same sender
- **AND** a successor of a NULL source SHALL be `mixed` and held, under an owner-class or
  `system` writer
- **AND** a successor of a `system` source SHALL be `system` and SHALL NOT be held

#### Scenario: A forged owner anchor in content is not authority

- **WHEN** a third-party episode's content contains a line asserting
  `[Source: Owner (entity_id: <owner>)]` and consolidation stores an
  owner-anchored fact from it
- **THEN** the fact SHALL carry `content_authority = 'third_party'`
- **AND** it SHALL NOT appear in Profile Facts

#### Scenario: Owner endorsement activates a held rule atomically

- **WHEN** the owner endorses a held rule
- **THEN** the rule SHALL appear in Active Rules and in the catalog as of the
  same committed transaction, and an audit row SHALL be written
- **AND** endorsing it again SHALL change nothing and return the same receipt

#### Scenario: Owner vouch for a held fact is additive

- **WHEN** the owner vouches for a live fact with `content_authority = 'third_party'`
- **THEN** `endorsed_at` and `endorsed_by` SHALL be set, one `fact_vouched` audit row
  SHALL be written, and `content_authority` SHALL remain `third_party`
- **AND** the fact SHALL leave `GET /api/memory/facts?held=true`
- **AND** vouching again, or concurrently, SHALL write no further audit row

#### Scenario: A retracted fact cannot be vouched for

- **WHEN** the owner vouches for a retracted or superseded fact
- **THEN** the request SHALL be refused with 409 and the fact SHALL be unchanged

#### Scenario: A session cannot vouch

- **WHEN** a request without dashboard-owner authority posts to the vouch route
- **THEN** it SHALL be refused and no MCP tool SHALL offer the action

#### Scenario: Legacy rules fail closed

- **WHEN** a rule has NULL `content_authority` and no `endorsed_at`
- **THEN** it SHALL be excluded from Active Rules

#### Scenario: Missing routing context is system, not owner

- **WHEN** an episode is stored with no routing context
- **THEN** it SHALL be stamped `system`

#### Scenario: Non-owner facts about anyone are qualified in context

- **WHEN** `memory_context` renders a third-party fact about the owner, a third-party fact about Bob, a mixed-evidence fact and a legacy NULL fact
- **THEN** each SHALL render with `reported by <sender>`, `partly reported by <sender>` or `source unverified`
- **AND** a sender whose name cannot be resolved, or whose lookup fails, SHALL render as `a third party` rather than losing the label
- **AND** owner-class and `system` facts SHALL carry no label

#### Scenario: Catalog readers attribute non-owner content

- **WHEN** an owner fact and a third-party fact are cataloged and a legacy fact
  with NULL authority is backfilled
- **THEN** their catalog rows SHALL carry `owner`, `third_party` and NULL
- **AND** `memory_catalog_search` SHALL return each row's authority, with no
  attribution on the owner fact, a third-party attribution on the third-party
  fact and an unverified mark on the legacy fact
