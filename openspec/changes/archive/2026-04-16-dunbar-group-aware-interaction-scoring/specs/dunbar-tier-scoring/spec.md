## MODIFIED Requirements

### Requirement: Decay score computation from interaction history
The system SHALL compute a decay score for each contact by summing exponentially decayed, direction-weighted, group-size-divided contributions from all recorded interactions.

#### Scenario: Score for a contact with recent interactions
- **WHEN** a contact has interactions recorded as temporal facts (`predicate='interaction'`, `scope='relationship'`)
- **THEN** the decay score MUST be computed as `sum(exp(-lambda * days_since_interaction_i) * direction_weight * (1.0 / group_size))` for each interaction fact
- **AND** `lambda` MUST equal `ln(2) / 30` (30-day half-life)
- **AND** `days_since_interaction_i` MUST be computed from each interaction's `valid_at` relative to the current timestamp

#### Scenario: Direction weighting multipliers
- **WHEN** computing the decay contribution of an interaction fact
- **THEN** `direction_weight` MUST be determined from `facts.metadata->>'direction'`:
  - `'outgoing'` → 10.0 (owner actively chose to communicate)
  - `'mutual'` → 5.0 (bidirectional exchange in the same session/day)
  - `'incoming'` → 1.0 (baseline passive receipt)
  - NULL or unknown → 1.0 (backward compatibility)

#### Scenario: Group size dilution
- **WHEN** computing the decay contribution of an interaction fact
- **THEN** the contribution MUST be divided by `group_size` read from `facts.metadata->>'group_size'`
- **AND** if `group_size` is NULL or absent, it MUST default to 1.0 (DM weight)
- **AND** `group_size` MUST be clamped to a minimum of 1.0 to prevent division by zero

#### Scenario: Score for a contact with no interactions
- **WHEN** a contact has zero interaction facts
- **THEN** the decay score MUST be `0.0`

#### Scenario: Score rewards frequency and recency
- **WHEN** contact A has 10 interactions in the past 30 days and contact B has 1 interaction 5 days ago
- **THEN** contact A's decay score MUST be greater than contact B's decay score

#### Scenario: Score computation excludes inactive facts
- **WHEN** computing the decay score for a contact
- **THEN** the computation MUST only include interaction facts with `validity='active'`
- **AND** superseded, expired, and retracted facts MUST be excluded

#### Scenario: Backward compatibility with pre-existing facts
- **WHEN** computing scores with interaction facts that lack `direction` or `group_size` in metadata
- **THEN** the scoring MUST use default multipliers (direction_weight=1.0, group_size=1.0)
- **AND** existing scores MUST remain identical to pre-change behavior for facts without enriched metadata

## ADDED Requirements

### Requirement: Batch group interaction logging
The system SHALL provide a tool that logs interaction facts for all members of a contact group in a single deterministic call, eliminating per-member LLM tool calls.

#### Scenario: Log interaction for group members
- **WHEN** `interaction_log_group(group_id, direction, occurred_at, summary)` is called with a valid group_id
- **THEN** the tool MUST resolve group membership from `relationship.group_members`
- **AND** for each member, it MUST call `interaction_log()` with `metadata` including `group_size` (equal to member count) and `group_id`
- **AND** it MUST return `{"logged": N, "skipped": M, "group_size": G}`

#### Scenario: Empty group returns zero
- **WHEN** `interaction_log_group` is called for a group with no members
- **THEN** the tool MUST return `{"logged": 0, "skipped": 0, "group_size": 0}`

#### Scenario: Group exceeding size threshold
- **WHEN** `interaction_log_group` is called for a group with more than 20 members
- **THEN** the tool MUST return early with `{"skipped": "group_too_large", "group_size": G}`
- **AND** no interaction facts MUST be created

#### Scenario: Default direction is mutual
- **WHEN** `interaction_log_group` is called without a `direction` argument
- **THEN** the direction MUST default to `"mutual"`
