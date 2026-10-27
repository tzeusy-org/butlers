## MODIFIED Requirements

### Requirement: Complexity Enum
The system SHALL define a complexity classification enum with six canonical tiers representing model capability and resource requirements. This requirement is the single definition of the tier vocabulary and its order; other specs reference it rather than restating it. Callers that still emit a retired tier value SHALL be remapped with a loud deprecation warning.

#### Scenario: Enum values
- **WHEN** a complexity classification is assigned
- **THEN** it MUST be one of: `reasoning`, `workhorse`, `cheap`, `specialty`, `local`, `legacy`

#### Scenario: Enum ordering
- **WHEN** complexity tiers are compared for tier fallthrough during model resolution
- **THEN** the canonical order (highest to lowest capability) is: `reasoning` > `workhorse` > `cheap` > `specialty` > `local` > `legacy`

#### Scenario: Legacy vocabulary remapping
- **WHEN** a caller supplies a retired tier value
- **THEN** it is remapped as follows: `trivial` to `cheap`, `medium` to `workhorse`, `high` to `reasoning`, `extra_high` to `reasoning`, `discretion` to `specialty`, `self_healing` to `specialty`
- **AND** a deprecation warning is logged naming the offending caller

### Requirement: Tick Trigger Complexity
The `tick` and `classification` trigger sources cover two distinct internal paths, each of which SHALL resolve its own complexity tier as described below. The system SHALL NOT apply a single fixed low-cost tier to every tick/classification-triggered session.

1. The scheduler tick handler (`tick()` in `src/butlers/core/scheduler.py`) dispatches each due scheduled task at that task's own configured complexity, defaulting to `workhorse` (`_DEFAULT_COMPLEXITY`) when the row specifies none or an unrecognized value. These sessions carry a `schedule:<name>` or `deadline:<name>` trigger source, not a fixed low-cost tier.
2. The Switchboard routing-classification spawn (`src/butlers/modules/pipeline.py`) is the only session literally tagged `trigger_source="classification"` (historical rows carry `"tick"` for this call site, so spend rules and QA/chronicler consumers that key off it SHALL match both values); it uses the `cheap` tier for its lightweight routing-LLM decision.

#### Scenario: Scheduler tick uses each task's complexity, default workhorse
- **WHEN** the scheduler tick handler dispatches a due cron, deadline, or event-chain task
- **THEN** the complexity is the task row's stored value
- **AND** a row with no complexity or an unrecognized value falls back to `workhorse`

#### Scenario: Switchboard routing classification uses cheap
- **WHEN** the Switchboard pipeline spawns its routing-classification session (`trigger_source="classification"`)
- **THEN** the complexity is set to `cheap`
