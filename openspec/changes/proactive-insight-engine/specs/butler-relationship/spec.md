# Relationship Butler — Insight Scan

## Purpose
Adds an insight-scan scheduled task to the Relationship butler that generates proactive insight candidates from relationship domain data.

## MODIFIED Requirements

### Requirement: Relationship Butler Schedules
The relationship butler runs date checks, maintenance sweeps, memory jobs, and insight scans.

#### Scenario: Scheduled task inventory
- **WHEN** the relationship butler daemon is running
- **THEN** it executes: `upcoming-dates-check` (0 8 * * *, prompt-based: check birthdays/anniversaries in the next 7 days), `relationship-maintenance` (0 9 * * 1, prompt-based: review contacts not interacted with in 30+ days, suggest 3 reconnections), `memory-consolidation` (0 */6 * * *, job), `memory-episode-cleanup` (0 4 * * *, job), and `insight-scan` (0 7 * * *, job: evaluate relationship domain data and generate insight candidates)

## ADDED Requirements

### Requirement: Relationship Insight Scan Job
The relationship butler's `insight-scan` job SHALL evaluate relationship domain data and produce insight candidates covering upcoming dates, stale contacts, pending gifts, and interaction milestones. All candidates are submitted via the Switchboard's `propose_insight_candidate()` MCP tool — the butler does not write to `shared.insight_candidates` directly.

#### Scenario: Insight-scan job handler registration
- **WHEN** the relationship butler starts
- **THEN** it SHALL register an `insight-scan` job handler that is invokable by the scheduler's `job` dispatch mode

#### Scenario: Candidate submission via Switchboard MCP
- **WHEN** the `insight-scan` job generates a candidate
- **THEN** it SHALL submit the candidate by calling the Switchboard's `propose_insight_candidate()` MCP tool
- **AND** if the tool returns `{"status": "filtered"}`, the butler SHALL skip remaining candidates of the same category (verbosity is off)
- **AND** if the tool returns `{"status": "error"}`, the butler SHALL log the error and continue with remaining candidates

#### Scenario: Upcoming date insights
- **WHEN** the insight-scan job evaluates upcoming dates
- **THEN** it SHALL generate candidates for birthdays and anniversaries occurring in the next 7 days
- **AND** dates within 1 day SHALL have priority 95 (time-critical)
- **AND** dates within 3 days SHALL have priority 80
- **AND** dates within 7 days SHALL have priority 70
- **AND** the `dedup_key` SHALL be `birthday:{contact-entity-id}:{year}` or `anniversary:{contact-entity-id}:{year}` (shared namespace for cross-butler dedup with Calendar)
- **AND** `expires_at` SHALL be the date of the event
- **AND** `cooldown_days` SHALL be 1 for dates within 1 day, 3 for dates within 3 days, 7 for dates within 7 days

#### Scenario: Stale contact insights
- **WHEN** the insight-scan job evaluates contact staleness
- **THEN** it SHALL generate candidates for contacts whose last interaction exceeds their tier-aware cadence threshold (or `stay_in_touch_days` if set)
- **AND** contacts overdue by more than 2x their cadence SHALL have priority 45
- **AND** contacts overdue by 1-2x their cadence SHALL have priority 35
- **AND** the `dedup_key` SHALL be `relationship:stale-contact:{contact-id}:{year-week}` (butler-scoped, weekly granularity)
- **AND** `expires_at` SHALL be 7 days from generation
- **AND** tier 1500 contacts without `stay_in_touch_days` SHALL be excluded

#### Scenario: Pending gift insights
- **WHEN** the insight-scan job evaluates pending gifts
- **THEN** it SHALL generate candidates for gifts with status `idea` or `purchased` that have an associated date within 14 days
- **AND** priority SHALL be 60 (informational)
- **AND** the `dedup_key` SHALL be `relationship:pending-gift:{gift-id}`
- **AND** `expires_at` SHALL be the associated date

#### Scenario: Interaction milestone insights
- **WHEN** the insight-scan job detects notable interaction milestones
- **THEN** it SHALL generate candidates for milestones such as "100th interaction with {contact}" or "1-year anniversary of first interaction with {contact}"
- **AND** priority SHALL be 30 (low-urgency nudge)
- **AND** the `dedup_key` SHALL be `relationship:milestone:{contact-id}:{milestone-type}`
- **AND** `cooldown_days` SHALL be 30
- **AND** `expires_at` SHALL be 7 days from generation
