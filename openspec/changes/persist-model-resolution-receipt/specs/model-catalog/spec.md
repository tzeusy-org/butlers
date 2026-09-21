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

#### Scenario: Read surfaces distinguish historical absence

- **WHEN** session detail or a Models dispatch-attempt read returns a recorded receipt
- **THEN** the API includes it without re-deriving a current routing decision
- **AND** the session UI discloses why the model won
- **WHEN** no receipt was recorded for a historical or static-fallback session
- **THEN** the API returns null and the UI says `No receipt recorded.`
