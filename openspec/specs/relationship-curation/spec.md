# Relationship Curation

## Purpose

The Relationship Curation capability provides autonomous weekly maintenance of the
relationship butler's entity graph: a scheduled pass groups four curation jobs
(prose→edge proposal, entity dedup, contradiction sweep, and approval-expiry
surfacing) into one session, applies safe high-confidence changes automatically,
and routes uncertain or owner-touching changes through the approval system.
Owner notification is via `notify()` with a consolidated digest.

## Requirements

### Requirement: Weekly curation pass

The relationship butler SHALL run a weekly `dispatch_mode="prompt"` scheduled
task that performs four curation jobs in one session — prose→edge proposal,
entity dedup, contradiction sweep, and approval-expiry surfacing — and emits a
single owner-facing digest. The session is headless; it MUST communicate results
via `notify()`.

#### Scenario: Scheduled pass runs all four jobs
- **WHEN** the curation schedule fires
- **THEN** the session MUST attempt all four jobs (prose→edges, dedup, contradiction sweep, approval surfacing) in one run
- **AND** it MUST end by calling `notify()` with a consolidated digest
- **AND** if no job produced any proposal, auto-action, or expiring approval, the session MAY exit without notifying

#### Scenario: Headless output reaches the owner only via notify
- **WHEN** the pass produces proposals or auto-applied changes
- **THEN** the summary MUST be delivered through `notify()` (not session text, which is discarded in a headless run)

### Requirement: Curation autonomy boundary

Every candidate mutation SHALL be classified as auto-apply, propose, or
report-only. A mutation MAY be auto-applied only when it does **not** touch the
owner entity, clears the high-confidence bar, and is reversible and logged.
Anything touching the owner entity, or below the confidence bar, MUST be
proposed for approval.

#### Scenario: Owner-touching change is always proposed
- **WHEN** a candidate edge, merge, or retraction involves the owner entity
- **THEN** it MUST be routed to approval (via `relationship_assert_fact()` → `pending_actions` for edges, or a `pending_actions` row for merges/retractions)
- **AND** it MUST NOT be applied directly

#### Scenario: Low-confidence non-owner change is proposed
- **WHEN** a non-owner merge or retraction does not clear the high-confidence bar
- **THEN** it MUST be proposed for approval rather than auto-applied

#### Scenario: High-confidence non-owner change is auto-applied and reported
- **WHEN** a non-owner merge or retraction clears the high-confidence bar
- **THEN** it MAY be applied directly
- **AND** the applied change MUST be recorded with provenance and listed in the digest so it is reviewable after the fact

### Requirement: High-confidence criteria for auto-apply

The high-confidence bar SHALL be explicit. Entity merges auto-apply only on a
normalized-name match plus at least one corroborating signal; fact retractions
auto-apply only on direct contradiction by a newer fact of equal-or-higher
confidence. Weaker matches are proposed, never auto-applied.

#### Scenario: Name-only entity match is proposed, not merged
- **WHEN** two entities share a normalized name but have no shared `contact_info`, overlapping alias, or shared edge to a third entity
- **THEN** the merge MUST be proposed, not auto-applied

#### Scenario: Corroborated entity match is auto-merged
- **WHEN** two non-owner entities share a normalized name AND at least one corroborating signal (shared contact_info, overlapping alias, or shared edge)
- **THEN** the merge MAY be auto-applied and reported in the digest

#### Scenario: Stale-but-uncontradicted fact is proposed, not retracted
- **WHEN** a fact is old or decayed but has no contradicting newer fact
- **THEN** any retraction MUST be proposed, not auto-applied

#### Scenario: Directly contradicted fact is auto-retracted
- **WHEN** a non-owner fact is contradicted by a newer fact of equal-or-higher confidence on the same `(entity, predicate, scope)`
- **THEN** the stale fact MAY be auto-retracted and reported in the digest

### Requirement: Auto-applied changes are reversible

Auto-applied merges and retractions SHALL be reversible. Retractions MUST set
`validity='retracted'` (never hard-delete) and record a curation reason; merges
MUST record the source entity id and re-pointed rows.

#### Scenario: Auto-retraction is a soft retract
- **WHEN** the curator auto-retracts a fact
- **THEN** the row MUST be marked `validity='retracted'` with a `metadata` curation reason
- **AND** the fact's content MUST remain recoverable (no hard delete)

### Requirement: Prose-to-edge proposal job

The pass SHALL scan active relationship-bearing prose facts (relational language
with no structured object link) and, for each standing relationship, resolve-or-
create the object entity and assert the registry-relational edge — proposing
owner edges and auto-asserting non-owner durable edges.

#### Scenario: Owner prose relationship becomes a proposed edge
- **WHEN** an active prose fact on the owner asserts a standing relationship to a resolvable entity (e.g. "cohabiting partner with Chloe Wong")
- **THEN** a `partner-of` (or appropriate registry-relational) edge MUST be proposed via `relationship_assert_fact()` (which parks it for approval)
- **AND** the originating prose fact MUST be left intact until the edge is approved

#### Scenario: Episodic prose is not turned into an edge
- **WHEN** a prose fact describes a one-off event (e.g. "planned dinner with", "wake coordination")
- **THEN** the pass MUST leave it as narrative and MUST NOT propose a relational edge

### Requirement: Approval-expiry surfacing job

The pass SHALL surface owner `pending_actions` approaching their 72h expiry so
they are decided rather than silently dropped.

#### Scenario: Expiring approval is surfaced in the digest
- **WHEN** an owner `pending_action` is within ~24h of its 72h expiry at pass time
- **THEN** it MUST be included in the digest with enough context to decide
- **AND** the pass MUST NOT auto-approve or auto-reject it
