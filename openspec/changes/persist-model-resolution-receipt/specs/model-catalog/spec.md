## ADDED Requirements

### Requirement: Durable Model Resolution Receipt

Each catalog-backed dispatch attempt SHALL persist the prompt-free model
resolution receipt that produced its candidate in
`public.model_dispatch_attempts.resolution_receipt`. The receipt SHALL name the
policy version, requested and effective intent, winner, ordered candidates,
candidate outcomes and exclusions, and tie-break reason. Persisting the receipt
MUST NOT change routing eligibility, ordering, or selection.

#### Scenario: Breaker exclusion is durable

- **WHEN** an otherwise eligible candidate has an open dispatch-outcome breaker
- **THEN** it remains excluded from selection exactly as before
- **AND** the selected attempt's receipt records that candidate with
  `exclusion="breaker_open"`

#### Scenario: Failover attempt explains its predecessor

- **WHEN** attempt zero fails with a classified failure and same-tier attempt one runs
- **THEN** attempt one's receipt names attempt zero and its failure class
- **AND** its winner names the candidate actually invoked for attempt one

#### Scenario: Oversized candidate evidence remains explicit

- **WHEN** a receipt exceeds the bounded storage projection
- **THEN** the ordered candidate list is truncated to a fitting prefix
- **AND** `truncated=true` and the original `candidate_count` are persisted
- **AND** the receipt is not silently dropped
- **AND** the complete persisted JSON projection remains at or below 32 KiB even
  when winner or intent metadata contains oversized catalog-backed strings

#### Scenario: Post-resolution policy override stays coherent

- **WHEN** a spend rule or private-content lane replaces the resolver's winner
- **THEN** the receipt names the final invoked candidate as its sole selected candidate
- **AND** clears stale exclusions on that candidate
- **AND** records the policy override as the winner reason rather than retaining the
  resolver's earlier tie-break reason

#### Scenario: Attempt identity is atomic

- **WHEN** quota skips or runtime retries precede a persisted attempt
- **THEN** the row's `attempt_index` equals its receipt's `attempt_index`
- **AND** no earlier row for that logical dispatch has the same index

#### Scenario: Discretion dispatches retain receipts

- **WHEN** DiscretionDispatcher resolves a catalog model and records quota-skip,
  success, runtime-failure, or suppression provenance
- **THEN** each recorded attempt carries the same bounded receipt contract
- **AND** receipt capture adds no tool-use requirement and preserves the catalog
  eligibility, ordering, and winner used by its legacy `mcp_servers={}` path

#### Scenario: Read surfaces distinguish historical absence

- **WHEN** session detail or a Models dispatch-attempt read returns a recorded receipt
- **THEN** the API includes it without re-deriving a current routing decision
- **AND** the session UI discloses why the model won
- **WHEN** no receipt was recorded for a historical or static-fallback session
- **THEN** the API returns null and the UI says `No receipt recorded.`
