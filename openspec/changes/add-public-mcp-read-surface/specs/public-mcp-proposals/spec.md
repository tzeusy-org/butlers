## Purpose

Let the owner's external AI clients propose a Relationship fact without gaining any write authority. A proposal is stored as quarantined input, deterministically validated and parked for the owner's per-event review, and becomes a fact only after the owner approves it; until then it has no effect anywhere in Butlers.

## ADDED Requirements

### Requirement: propose_fact accepts only proposable relational triples
`propose_fact` SHALL accept `subject_entity_id`, `predicate`, `object_entity_id`, an optional `evidence_url`, an optional `note` of at most 500 characters and an `idempotency_key` of at most 64 characters, and nothing else. `predicate` SHALL be in the literal allowlist `src/butlers/knowledge_read/proposable.toml`, which in v1 holds only relational-kind predicates whose object is an entity (`knows`, `colleague-of`, `friend-of`, `co-attended`, `visited`, `works-at`, `member-of`). A family-gated, contact-kind or override-kind predicate, or a literal object, SHALL be refused with `invalid_argument` and SHALL write nothing. On acceptance the tool SHALL return `{proposal_id, status: "received"}`.

ID: REQ-public-mcp-proposals-001
Source: RFC 0039 D10 (propose_fact row) and D13 (proposable predicates); owner decision OD4
Scope: v1-mandatory

#### Scenario: Valid proposal is received
- **WHEN** an authorized client proposes `(A, "works-at", B)` for two existing entities with a fresh idempotency key
- **THEN** the result SHALL be `{proposal_id, status: "received"}` and one inbox row SHALL exist for it

#### Scenario: Family predicate is not proposable
- **WHEN** a client proposes `(A, "parent-of", B)`
- **THEN** the result SHALL be `invalid_argument` and no inbox row SHALL be written

#### Scenario: Contact predicate is not proposable
- **WHEN** a client proposes predicate `has-email` with any object
- **THEN** the result SHALL be `invalid_argument` and no inbox row SHALL be written

#### Scenario: Oversized note is refused
- **WHEN** a client sends a 501-character `note`
- **THEN** the result SHALL be `invalid_argument` and no inbox row SHALL be written

### Requirement: Idempotent receipt
The server SHALL derive `proposal_id` deterministically from the caller `sub`, the client id, the `idempotency_key` and the canonical payload digest, and SHALL insert with no-op-on-conflict semantics, without reading the inbox. An identical retry SHALL return the same `proposal_id` and SHALL create no second row. A different payload under a reused idempotency key SHALL receive a different `proposal_id`. For each `(sub, client, idempotency_key)`, the earliest proposal, ordered by inbox receipt time and then `proposal_id`, SHALL proceed, and the importer SHALL mark every later proposal with a different payload `conflict`, including when both arrive in one importer pass.

ID: REQ-public-mcp-proposals-002
Source: RFC 0039 D13 step 1
Scope: v1-mandatory

#### Scenario: Retry returns the same id
- **WHEN** the client repeats an identical `propose_fact` call after a network timeout
- **THEN** both calls SHALL return the same `proposal_id` and exactly one proposal SHALL exist

#### Scenario: Reused key with a changed payload is a conflict
- **WHEN** a client reuses an idempotency key with a different object entity
- **THEN** the second call SHALL return a new `proposal_id` with status `received`
- **AND** after import that proposal SHALL be in state `conflict` and SHALL not be parked

#### Scenario: Both payloads arrive in one pass
- **WHEN** an original and a changed payload under the same key are both in the inbox when the importer runs
- **THEN** the earlier-received one SHALL proceed to validation and the later one SHALL be `conflict`

### Requirement: Inbox is quarantined, private and capped
The inbox SHALL be writable by the public server's role only through column-limited INSERT and SHALL not be readable by it, so no client can see any proposal's content. Database constraints SHALL bound every column length. Once 1,000 rows are undrained, further inserts SHALL be refused by the database and `propose_fact` SHALL return `unavailable` with `retryable: true`. An inbox row SHALL cause no effect, wake no process and start no session until the importer pulls it on its own schedule.

ID: REQ-public-mcp-proposals-003
Source: RFC 0039 D13 (inbox hardening) and D14 item 3; bu-mzvel0 owner rule (no unreviewed outside write)
Scope: v1-mandatory

#### Scenario: Full inbox refuses new proposals
- **WHEN** 1,000 inbox rows are undrained and a client calls `propose_fact`
- **THEN** the result SHALL be `unavailable` with `retryable: true` and no row SHALL be added

#### Scenario: A proposal wakes nothing
- **WHEN** a proposal is received
- **THEN** no Butlers session, route, goal, schedule or notification SHALL be created before the importer's next scheduled pass

### Requirement: Deterministic import and validation
A Relationship daemon job, `public_mcp_proposal_import` (`dispatch_mode = "job"`, every 5 minutes, no LLM), SHALL read at most 100 inbox rows per pass, copy each into `relationship.external_fact_proposals` with no-op-on-conflict semantics, and then delete the copied inbox rows, so that a crash between the two steps loses or duplicates nothing. It SHALL then re-validate each proposal in this order, recording the first failing outcome: schema and predicate allowlist, and the subject and object entities exist, are neither merged nor deleted, and the subject is not the owner (`invalid`); note and evidence URL pass the DLP secret-shape scan (`invalid`); no identical active fact exists (`duplicate`); the proposal is not a later different payload under an already used `(sub, client, idempotency_key)` as defined in REQ-public-mcp-proposals-002 (`conflict`); the submitting `(sub, client)` has fewer than 20 proposals today and fewer than 50 unresolved (`rate_limited`). A proposal passing every check SHALL be parked.

ID: REQ-public-mcp-proposals-004
Source: RFC 0039 D13 step 2 and D14 item 5; heart-and-soul/vision.md Rule 4
Scope: v1-mandatory

#### Scenario: Crash between copy and delete is safe
- **WHEN** the importer crashes after copying rows to the primary but before deleting them from the inbox
- **THEN** the next pass SHALL delete them without creating a second proposal or a second parked action

#### Scenario: Merged subject is invalid
- **WHEN** a received proposal names a subject entity that has since been merged
- **THEN** it SHALL be recorded `invalid` and SHALL not be parked

#### Scenario: Deleted object is invalid
- **WHEN** a received proposal names an object entity that has since been soft-deleted
- **THEN** it SHALL be recorded `invalid` and SHALL not be parked

#### Scenario: Owner as subject is invalid
- **WHEN** a proposal's subject is the owner entity
- **THEN** it SHALL be recorded `invalid` and SHALL not be parked

#### Scenario: Existing fact is a duplicate
- **WHEN** an identical active fact `(A, "works-at", B)` already exists
- **THEN** the proposal SHALL be recorded `duplicate` and SHALL not be parked

#### Scenario: Secret-shaped note is invalid
- **WHEN** a proposal's note contains a token shape that the DLP scan matches
- **THEN** it SHALL be recorded `invalid` and SHALL not be parked

### Requirement: Valid proposals park for per-event owner review
For a valid proposal, the importer SHALL park one pending `relationship_assert_fact` action as specified by REQ-module-approvals-010 and REQ-relationship-facts-011, with deduplication key `external_mcp:<proposal_id>`, a 14-day expiry, deterministic template text for `why` and the summary, and evidence holding only the submitting client label, the evidence URL's scheme and host, and the SHA-256 of the full URL. The full evidence URL and the note SHALL stay only in `relationship.external_fact_proposals`, so no outside-authored text is stored in `pending_actions` or reaches a model through the approvals tools. It SHALL record the `pending_action_id` on the proposal and set its state to `parked`. Re-running the importer SHALL never park a second action for the same proposal.

ID: REQ-public-mcp-proposals-005
Source: RFC 0039 D13 step 3; owner decision OD4
Scope: v1-mandatory

#### Scenario: Valid proposal appears on the approvals page
- **WHEN** a valid proposal is imported
- **THEN** exactly one pending `relationship_assert_fact` action SHALL exist with deduplication key `external_mcp:<proposal_id>` and an expiry 14 days out
- **AND** the proposal SHALL be in state `parked` with that action's id

#### Scenario: Outside text stays out of the pending action
- **WHEN** a valid proposal carries a note and the evidence URL `https://example.com/ignore-previous-instructions`
- **THEN** the parked action's evidence SHALL hold only the client label, `https://example.com` and the URL's SHA-256
- **AND** neither the note nor the URL path SHALL appear in any `pending_actions` column

#### Scenario: Re-import parks nothing new
- **WHEN** the importer processes the same proposal id again
- **THEN** no second pending action SHALL be created

### Requirement: Proposal lifecycle is reconciled and visible to clients
Each importer pass SHALL map the parked action's status to the proposal state: `executed` to `applied`, `rejected` to `owner_rejected`, `expired` to `expired`, and an execution failure to `apply_failed`, with `decided_at` set at the transition. The terminal states SHALL be `invalid`, `duplicate`, `conflict`, `rate_limited`, `applied`, `owner_rejected`, `expired` and `apply_failed`. The `state` and `decided_at` columns SHALL replicate to the projection, so `fetch("proposal:<id>")` reports the outcome. An unanswered proposal SHALL expire, and expiry SHALL mean denial. 30 days after a proposal reaches a terminal state, the importer SHALL clear its `note` and `evidence_url` and SHALL keep its ids, state and timestamps.

ID: REQ-public-mcp-proposals-006
Source: RFC 0039 D13 steps 4 to 7 (retention); heart-and-soul/security.md Approval Gates (timeouts deny)
Scope: v1-mandatory

#### Scenario: Terminal proposals lose their outside text
- **WHEN** a proposal has been `owner_rejected` for 31 days and the importer runs
- **THEN** its `note` and `evidence_url` SHALL be null, and its `proposal_id`, state and `decided_at` SHALL be unchanged

#### Scenario: Owner approval is reported as applied
- **WHEN** the owner approves a parked proposal and the fact is written
- **THEN** after the next importer pass and replication the proposal's state SHALL be `applied` with `decided_at`

#### Scenario: Owner rejection is recorded
- **WHEN** the owner rejects a parked proposal
- **THEN** no fact SHALL be written, and the proposal's state SHALL become `owner_rejected`

#### Scenario: Silence denies
- **WHEN** 14 days pass without an owner decision
- **THEN** the pending action SHALL expire, no fact SHALL be written, and the proposal's state SHALL become `expired`

#### Scenario: Apply failure is surfaced
- **WHEN** an approved proposal's execution fails
- **THEN** its state SHALL become `apply_failed` and the apply-failure alert SHALL fire

### Requirement: Proposals kill switch stops intake and parking
While the `proposals` domain or `all` is disabled, `propose_fact` SHALL return `domain_disabled`, and the importer SHALL park no new action. The importer SHALL honour the `proposals` and `all` rows of `mcp_surface.controls` on the primary, read at the start of each pass through its `SELECT` grant (REQ-database-security-016); the break-glass ConfigMap and the in-process canary latch SHALL affect only the public server. Rows already received or imported SHALL be kept unparked until the switch is re-enabled. Actions already parked SHALL remain on the approvals page for the owner to decide.

ID: REQ-public-mcp-proposals-007
Source: RFC 0039 D13 step 2 and D16; owner decision OD3 (kill switches for incident response)
Scope: v1-mandatory

#### Scenario: Disabled proposals refuse new input
- **WHEN** the `proposals` domain is disabled and a client calls `propose_fact`
- **THEN** the result SHALL be `domain_disabled` and no inbox row SHALL be written

#### Scenario: Disabled proposals pause parking
- **WHEN** the `proposals` domain is disabled while 10 valid proposals are in the inbox
- **THEN** the importer SHALL park none of them until the domain is re-enabled

#### Scenario: The importer reads the replicated switch at its source
- **WHEN** the owner disables `proposals` on the Settings panel and replication to the projection is stalled
- **THEN** the importer's next pass SHALL park no new action

#### Scenario: Already-parked actions stay decidable
- **WHEN** the `proposals` domain is disabled and a proposal was parked earlier
- **THEN** the owner SHALL still be able to approve or reject it on the approvals page

### Requirement: Automatic approval stays off
The only path from a proposal to a fact SHALL be the owner's per-event approval: no standing rule, policy, model or batch job SHALL approve an external proposal.

ID: REQ-public-mcp-proposals-008
Source: RFC 0039 D13 (future auto-approval, not v1); owner decisions OD4 and OD8
Scope: v1-mandatory

#### Scenario: No proposal applies without a tap
- **WHEN** any number of valid proposals are parked and no owner decision is recorded
- **THEN** no fact SHALL be written from them

#### Scenario: No rule, policy or batch approves an importer-parked action
- **WHEN** a standing rule, autonomy policy or scheduled batch job is evaluated against an action the importer parked
- **THEN** it SHALL not approve the action, and the action SHALL stay `pending` until the owner decides or it expires

## Source References

- Non-Negotiable Rule 1 (owner sovereignty: no outside system obtains an unreviewed write) and Rule 4 (deterministic infrastructure: importer and apply are model-free jobs), `about/heart-and-soul/vision.md`.
- `about/heart-and-soul/security.md`, untrusted senders and Approval Gates (exact stored call; timeouts deny).
- RFC 0039 (`about/legends-and-lore/rfcs/0039-public-mcp-read-surface.md`), D13 to D16; owner decisions OD4 and OD8.
- RFC 0021 (decision loop) and the recorded bu-mzvel0 owner answer ("No outside server ever gets a write, send or spend effect"), applied in principle to the outbound direction.
