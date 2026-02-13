## ADDED Requirements

### Requirement: Consolidation scheduled task

The Memory Butler SHALL run consolidation every 6 hours via a scheduled task (`cron = "0 */6 * * *"`). The task SHALL fetch episodes pending consolidation, group them by `(tenant_id, source butler)`, and process each group in deterministic order.

#### Scenario: Consolidation processes unconsolidated episodes
- **WHEN** the consolidation task fires
- **AND** 10 pending episodes exist (5 from 'health', 5 from 'general') in the same tenant
- **THEN** the system SHALL process 2 groups (`tenant_id`, health) and (`tenant_id`, general)
- **AND** successful episodes SHALL be marked `consolidation_status='consolidated'` and compatibility `consolidated=true`

#### Scenario: No unconsolidated episodes
- **WHEN** the consolidation task fires
- **AND** no pending episodes exist
- **THEN** no CC instances SHALL be spawned

### Requirement: Consolidation terminal states and retries

Every episode entering consolidation SHALL eventually reach exactly one terminal consolidation state: `consolidated`, `failed`, or `dead_letter`. Retryable failures SHALL record retry metadata.

#### Scenario: Retryable parse failure records retry metadata
- **WHEN** consolidation processing fails for an episode with a retryable error
- **THEN** the episode SHALL record `consolidation_attempts`, `last_consolidation_error`, and `next_consolidation_retry_at`
- **AND** the episode SHALL remain non-terminal until success or terminal failure

#### Scenario: Exhausted retries move episode to dead-letter
- **WHEN** an episode exceeds configured retry attempts
- **THEN** the episode SHALL transition to `consolidation_status='dead_letter'`

### Requirement: Consolidation spawns CC with extraction prompt

For each butler group, consolidation SHALL spawn a CC instance with a prompt that includes: the unconsolidated episodes, existing active facts (scoped to the butler), and existing active rules (scoped to the butler). The prompt SHALL instruct extraction of new facts (with permanence classification), updated facts (supersession), new rules, and confirmations of existing facts.

#### Scenario: CC extracts new fact from episodes
- **WHEN** consolidation processes episodes containing "User mentioned they are lactose intolerant"
- **AND** no existing fact covers this information
- **THEN** the CC output SHALL include a new fact with `subject='user'`, `predicate='dietary_restriction'`, `content='Lactose intolerant'`, and `permanence='stable'`

#### Scenario: CC identifies fact to supersede
- **WHEN** consolidation processes episodes containing "User now prefers tea over coffee"
- **AND** an existing fact states "User's preferred drink is coffee"
- **THEN** the CC output SHALL include an updated fact that supersedes the existing one

#### Scenario: CC confirms existing facts
- **WHEN** consolidation processes episodes where the user references their known name
- **AND** a fact already stores the user's name
- **THEN** the CC output SHALL include a confirmation of the existing fact's ID

### Requirement: Consolidation creates provenance links

When consolidation creates a new fact or rule from episodes, the system SHALL create `derived_from` memory links connecting the new fact/rule to the source episode(s). When a fact supports a rule, a `supports` link SHALL be created.

#### Scenario: Fact linked to source episode
- **WHEN** consolidation extracts a fact from episode E1
- **THEN** a memory link SHALL exist with `source_type='fact'`, `target_type='episode'`, `target_id=E1.id`, `relation='derived_from'`

### Requirement: Consolidation writes memory events

Consolidation lifecycle transitions and materialized outputs SHALL append entries to `memory_events`.

#### Scenario: Consolidation success emits events
- **WHEN** consolidation creates a fact and marks an episode consolidated
- **THEN** `memory_events` SHALL include event records for both the fact creation and episode state transition

### Requirement: Decay sweep scheduled task

The Memory Butler SHALL run a daily decay sweep (`cron = "0 3 * * *"`) that computes `effective_confidence = confidence × exp(-decay_rate × days_since_last_confirmed)` for all active facts and rules.

#### Scenario: Fact transitions to fading
- **WHEN** the decay sweep runs
- **AND** a fact's effective_confidence is 0.15 (below 0.2, above 0.05)
- **THEN** the fact's lifecycle state SHALL transition to `fading`

#### Scenario: Fact transitions to expired
- **WHEN** the decay sweep runs
- **AND** a fact's effective_confidence is 0.03 (below 0.05)
- **THEN** the fact's `validity` SHALL be set to 'expired'

#### Scenario: Permanent fact never decays
- **WHEN** the decay sweep runs
- **AND** a fact has `permanence='permanent'` (decay_rate=0.0)
- **THEN** the fact's effective_confidence SHALL remain unchanged regardless of time elapsed

### Requirement: Episode cleanup scheduled task

The Memory Butler SHALL run daily episode cleanup (`cron = "0 4 * * *"`) that deletes episodes where `expires_at < now()`. If episode count exceeds `max_entries` (default 10,000), the oldest episodes SHALL be deleted first. Episodes that have not reached a terminal consolidation state and have not expired SHALL never be deleted by cleanup.

#### Scenario: Expired episodes deleted
- **WHEN** episode cleanup runs
- **AND** 5 episodes have `expires_at` in the past
- **THEN** those 5 episodes SHALL be deleted

#### Scenario: Unconsolidated episodes preserved
- **WHEN** episode cleanup runs
- **AND** an episode has non-terminal `consolidation_status` and `expires_at` in the future
- **THEN** the episode SHALL NOT be deleted even if the max_entries cap is reached

#### Scenario: Capacity enforcement
- **WHEN** episode cleanup runs
- **AND** 10,500 episodes exist (500 over max_entries)
- **THEN** the 500 oldest consolidated expired episodes SHALL be deleted
