## ADDED Requirements

### Requirement: Seasonal Period Definition
Seasonal periods are stored in a `seasonal_periods` table with fields: `id` (UUID), `name` (unique per butler), `period_type` (enum: `annual`, `academic`, `fiscal`, `custom`), `start_month` (integer 1-12), `start_day` (integer 1-31), `end_month` (integer 1-12), `end_day` (integer 1-31), `timezone` (string, default from butler config), `metadata` (JSONB, optional -- custom attributes like priority modifiers or context hints), `butler_name`, `enabled` (boolean, default true).

#### Scenario: Create annual seasonal period
- **WHEN** `seasonal_period_create(name="tax-season", period_type="annual", start_month=1, start_day=1, end_month=4, end_day=15, metadata={context_hint: "Tax filing season. Prioritize financial document organization and tax-related reminders."})` is called
- **THEN** a `seasonal_periods` row is inserted with `enabled=true`
- **AND** the period's UUID is returned

#### Scenario: Create academic seasonal period
- **WHEN** `seasonal_period_create(name="spring-semester", period_type="academic", start_month=1, start_day=15, end_month=5, end_day=15)` is called
- **THEN** a `seasonal_periods` row is inserted

#### Scenario: Invalid month/day combination rejected
- **WHEN** `seasonal_period_create(name="bad-period", start_month=2, start_day=30, end_month=3, end_day=15)` is called
- **THEN** a `ValueError` is raised indicating February 30 is not a valid date

#### Scenario: Duplicate name rejected
- **WHEN** `seasonal_period_create(name="tax-season", ...)` is called and a period with that name already exists for this butler
- **THEN** a `ValueError` is raised

### Requirement: Active Period Detection
A `get_active_seasons()` function SHALL return all `seasonal_periods` where the current date falls within the period's date range, accounting for periods that wrap across year boundaries (e.g., winter holiday season from November to January).

#### Scenario: Period active within same year
- **WHEN** today is March 15 and a period has `start_month=1, start_day=1, end_month=4, end_day=15`
- **THEN** `get_active_seasons()` includes this period

#### Scenario: Period inactive outside range
- **WHEN** today is June 1 and a period has `start_month=1, start_day=1, end_month=4, end_day=15`
- **THEN** `get_active_seasons()` does NOT include this period

#### Scenario: Period wrapping year boundary
- **WHEN** today is December 20 and a period has `start_month=11, start_day=15, end_month=1, end_day=10`
- **THEN** `get_active_seasons()` includes this period

#### Scenario: Disabled period excluded
- **WHEN** a period has `enabled=false`
- **THEN** `get_active_seasons()` does NOT include it regardless of date

#### Scenario: Multiple concurrent periods
- **WHEN** today is January 20 and both "tax-season" (Jan 1 - Apr 15) and "winter-holidays" (Nov 15 - Jan 31) are defined and active
- **THEN** `get_active_seasons()` returns both periods

### Requirement: Seasonal Context Injection
The scheduler SHALL query `get_active_seasons()` during task dispatch and inject active seasonal context into the prompt or job metadata. This allows butlers to adjust behavior based on what periods are currently active.

#### Scenario: Active season injected into prompt dispatch
- **WHEN** `tick()` dispatches a prompt-mode task
- **AND** `get_active_seasons()` returns `[{name: "tax-season", metadata: {context_hint: "Prioritize financial tasks"}}]`
- **THEN** the dispatch context includes `active_seasons` with the period names and metadata

#### Scenario: No active seasons
- **WHEN** `tick()` dispatches a task and `get_active_seasons()` returns an empty list
- **THEN** no seasonal context is injected into the dispatch

### Requirement: Seasonal Period CRUD Tools
The module SHALL register MCP tools: `seasonal_period_create`, `seasonal_period_update`, `seasonal_period_list`, `seasonal_period_delete`.

#### Scenario: List all seasonal periods
- **WHEN** `seasonal_period_list()` is called
- **THEN** all `seasonal_periods` for this butler are returned with their `enabled` status and whether they are currently active

#### Scenario: Update seasonal period dates
- **WHEN** `seasonal_period_update(id, start_month=2, start_day=1)` is called
- **THEN** the period's start date is updated
- **AND** the new date combination is validated

#### Scenario: Delete seasonal period
- **WHEN** `seasonal_period_delete(id)` is called
- **THEN** the period row is removed

#### Scenario: Toggle seasonal period
- **WHEN** `seasonal_period_update(id, enabled=false)` is called
- **THEN** the period is disabled and excluded from `get_active_seasons()`

### Requirement: Common Seasonal Period Presets
The system SHALL provide a `seasonal_period_create_preset` tool that creates commonly used periods with sensible defaults. Presets include: `us-tax-season` (Jan 1 - Apr 15), `year-end-holidays` (Dec 15 - Jan 5), `back-to-school` (Aug 1 - Sep 15), `spring-semester` (Jan 15 - May 15), `fall-semester` (Aug 25 - Dec 15).

#### Scenario: Create preset period
- **WHEN** `seasonal_period_create_preset(preset="us-tax-season")` is called
- **THEN** a seasonal period is created with `name="us-tax-season"`, `period_type="fiscal"`, `start_month=1`, `start_day=1`, `end_month=4`, `end_day=15`, and metadata with a relevant context hint

#### Scenario: Unknown preset rejected
- **WHEN** `seasonal_period_create_preset(preset="unknown-period")` is called
- **THEN** a `ValueError` is raised listing available presets
