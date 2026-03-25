## ADDED Requirements

### Requirement: Correction-Driven Memory Retraction
The memory module SHALL support retraction of memories (facts, episodes, and rules) initiated by the correction system. Correction-driven retraction SHALL use the existing `memory_forget` mechanism to set validity to `retracted`, and SHALL additionally record correction provenance in the memory's metadata.

#### Scenario: Fact retracted via correction
- **WHEN** the `correct` tool processes a `memory_deletion` correction for a fact
- **THEN** the memory module's `memory_forget` SHALL be called with the fact's `memory_id` and `memory_type=fact`
- **AND** the fact's `metadata` SHALL be updated to include `correction_id` (the UUID of the correction record) and `correction_reason` (the user's description of why the memory is wrong)
- **AND** the fact's `validity` SHALL be set to `retracted`

#### Scenario: Episode retracted via correction
- **WHEN** the `correct` tool processes a `memory_deletion` correction for an episode
- **THEN** the memory module's `memory_forget` SHALL be called with the episode's `memory_id` and `memory_type=episode`
- **AND** the episode's `metadata` SHALL be updated to include `correction_id` and `correction_reason`

#### Scenario: Rule retracted via correction
- **WHEN** the `correct` tool processes a `memory_deletion` correction for a rule
- **THEN** the memory module's `memory_forget` SHALL be called with the rule's `memory_id` and `memory_type=rule`
- **AND** the rule's `metadata` SHALL be updated to include `correction_id` and `correction_reason`

#### Scenario: Already-retracted memory cannot be corrected
- **WHEN** a `memory_deletion` correction targets a memory whose validity is already `retracted`
- **THEN** the correction SHALL fail with `status=failed` and a summary explaining that the memory is already retracted

#### Scenario: Superseded memory cannot be corrected via deletion
- **WHEN** a `memory_deletion` correction targets a fact whose validity is `superseded`
- **THEN** the correction SHALL fail with `status=failed` and a summary explaining that the memory has been superseded by a newer version, and suggesting the user correct the newer version instead

#### Scenario: Correction provenance in memory events
- **WHEN** a memory is retracted via correction
- **THEN** a `memory_events` row SHALL be inserted with event type indicating correction-driven retraction
- **AND** the event's metadata SHALL include the `correction_id` for audit linkage
