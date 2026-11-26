## MODIFIED Requirements

### Requirement: Aggregation Job

ID: REQ-cross-butler-briefing-aggregation-001
Source: bu-5ii4gj.5.1 (run 26 move 5)
Scope: v1-mandatory
The General butler SHALL have a `collect_briefing_contributions` deterministic job that reads all specialist contributions for today's date in the owner's configured timezone (falling back to SGT) via the `v_briefing_contributions` view, merges them into a combined payload, and writes the result to General's state store under key `briefing/combined/<YYYY-MM-DD>`.

#### Scenario: All specialists contributed
- **WHEN** the aggregation job runs and all 7 specialist contributions exist for today
- **THEN** the combined payload contains entries from all 7 butlers, ordered by butler name
- **AND** the 7 butlers are precisely: `education`, `finance`, `health`, `home`, `lifestyle`, `relationship`, `travel`

#### Scenario: Partial contributions
- **WHEN** the aggregation job runs and only 3 of 7 specialists have contributed
- **THEN** the combined payload contains the 3 available contributions
- **AND** the `missing_butlers` field lists the names of butlers that did not contribute

#### Scenario: No contributions available
- **WHEN** the aggregation job runs and no specialist contributions exist for today
- **THEN** the combined payload has an empty `contributions` array and `missing_butlers` lists all 7 specialist butler names

#### Scenario: Malformed contribution skipped
- **WHEN** a specialist's state entry exists but contains invalid JSON or is missing required fields
- **THEN** the aggregation job logs a warning and excludes that contribution from the combined payload
- **AND** the butler is listed in `missing_butlers`
