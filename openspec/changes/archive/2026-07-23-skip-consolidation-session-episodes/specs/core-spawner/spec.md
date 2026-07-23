## MODIFIED Requirements

### Requirement: Memory Context Injection
When the memory module is enabled, the Spawner SHALL fetch memory context via `fetch_memory_context()` before invocation and append it to the system prompt. On successful completion, it SHALL store non-empty session output as an episode via `store_session_episode()` unless the trigger source is exactly `schedule:consolidation`. This automatic episode-write exclusion SHALL NOT suppress the normal session record or change memory-context retrieval. Both memory operations SHALL be fail-open (log and continue).

#### Scenario: Memory context injected into system prompt
- **WHEN** the memory module is enabled and context is available
- **THEN** the memory context is appended to the base system prompt separated by a blank line

#### Scenario: Memory failure does not block invocation
- **WHEN** memory context retrieval fails
- **THEN** the failure is logged and the invocation proceeds with the base system prompt only

#### Scenario: Consolidation session skips automatic episode persistence
- **WHEN** memory is enabled and a runtime invocation with
  `trigger_source="schedule:consolidation"` succeeds with non-empty output
- **THEN** the Spawner completes the ordinary session record
- **AND** the Spawner SHALL NOT call `store_session_episode()` for that output

#### Scenario: Ordinary scheduled session still persists an episode
- **WHEN** memory is enabled and a runtime invocation with
  `trigger_source="schedule:daily_digest"` succeeds with non-empty output
- **THEN** the Spawner SHALL call `store_session_episode()` once for that
  output after normal session completion

#### Scenario: Failed session remains ineligible for automatic episode persistence
- **WHEN** memory is enabled and a runtime invocation fails
- **THEN** the Spawner SHALL NOT call `store_session_episode()` regardless of
  its trigger source
