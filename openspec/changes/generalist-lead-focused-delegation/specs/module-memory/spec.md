## MODIFIED Requirements

### Requirement: Content authority and steering-class admission

Every episode, fact and rule SHALL carry a server-stamped `content_authority`
derived only from the goal origin in the verified broker assertion lineage of
the call that wrote it (never from a caller argument, tool argument, spawner
context or text inside content). The value SHALL be one of `owner`,
`owner_device`, `third_party`, `system` or `mixed`; `owner` and `owner_device`
are the *owner-class*. An `owner`-origin assertion yields `owner`, except that
an owner-origin assertion whose principal or admitting channel is the
dashboard, with no sender entity, yields `owner_device`; a `third_party` origin
yields `third_party`; an `obligation` or `staffer` origin, and a write with no
assertion (a deterministic job or service-plane caller), yields `system`. A
write made under an exposed assertion SHALL additionally be stamped tainted,
every read that returns an episode, fact or rule SHALL return its authority and
taint stamp, and Profile Facts SHALL admit only untainted owner-class facts. A
write whose authority cannot be stamped SHALL fail rather than store an
unstamped steering artifact.
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
  owner-class facts; an owner-anchored fact of any other authority SHALL be
  eligible only for Task-Relevant Facts, rendered with an attribution naming its
  sender (or marked unverified when the authority is NULL). Legacy rows are never
  backfilled by guessing; the migration SHALL report how many legacy rules and
  facts it left unclassified.
- The owner SHALL be able to endorse a held rule through an owner surface only;
  endorsement SHALL NOT be LLM-presentable or grantable to any goal invocation.
  Endorsement sets `endorsed_at`
  and `endorsed_by`, makes the rule visible to recall and to the catalog in the
  same transaction, records an audit event, and is a no-op returning the same
  receipt when repeated. Endorsement uses compare-and-swap against a retired or
  forgotten rule: a retired or forgotten rule SHALL NOT be endorsed. Repeat
  consolidation of an episode SHALL NOT downgrade an endorsed rule.
- Relationship registry identity facts are a separate authority-bearing store under relationship-facts. Its writer/gap callbacks SHALL carry persisted Relationship report authority and genuine stored confirmation rather than recomputing it from an unrelated current session. This cross-reference SHALL not change any episode/fact/rule classification, steering/profile admission, endorsement, weakest-evidence rule, NULL legacy treatment or run15 follow-up ownership in this memory capability.

ID: REQ-module-memory-013
Source: bu-s11n0s.2 original Outcome/S1–S4; heart-and-soul/security.md; Relationship MANIFESTO.md; adopted dashboard-owner-auth and existing capability contract; RFC 0038 D4, D6, D7, D15
Scope: v1-mandatory

#### Scenario: A third-party routed session cannot create standing guidance

- **WHEN** an invocation of a `third_party`-origin goal calls `memory_store_rule`
- **THEN** the broker refuses the write, because the goal is exposed from admission
- **AND** any rule the memory domain does store under a `third_party`-origin assertion SHALL have `content_authority = 'third_party'` and `endorsed_at IS NULL`
- **AND** it SHALL be absent from `memory_context` Active Rules and from `public.memory_catalog` search

#### Scenario: Mixed evidence is held

- **WHEN** consolidation derives one rule from one owner episode and one
  third-party episode
- **THEN** the rule SHALL have `content_authority = 'mixed'` and SHALL be held

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

#### Scenario: Legacy rules fail closed

- **WHEN** a rule has NULL `content_authority` and no `endorsed_at`
- **THEN** it SHALL be excluded from Active Rules

#### Scenario: Missing routing context is system, not owner

- **WHEN** an episode is stored by a call with no verified assertion lineage, or under an `obligation` or `staffer` origin
- **THEN** it SHALL be stamped `system`
- **AND** a write under an exposed assertion of any origin SHALL also be stamped tainted, and a tainted owner-class fact SHALL NOT appear in Profile Facts
