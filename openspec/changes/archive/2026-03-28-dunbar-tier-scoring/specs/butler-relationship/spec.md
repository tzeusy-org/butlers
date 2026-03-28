# Butler Relationship — Dunbar Tier Scoring Delta

## MODIFIED Requirements

### Requirement: Relationship Butler Schedules
The relationship butler runs date checks, maintenance sweeps, and memory jobs.

#### Scenario: Scheduled task inventory
- **WHEN** the relationship butler daemon is running
- **THEN** it executes: `upcoming-dates-check` (0 8 * * *, prompt-based: check birthdays/anniversaries in the next 7 days), `relationship-maintenance` (0 9 * * 1, prompt-based: rank overdue contacts by Dunbar tier-weighted urgency and suggest top 3 reconnections), `memory-consolidation` (0 */6 * * *, job), and `memory-episode-cleanup` (0 4 * * *, job)

### Requirement: Relationship Butler Skills
The relationship butler has gift brainstorming and reconnection planning skills.

#### Scenario: Skill inventory
- **WHEN** the relationship butler operates
- **THEN** it has access to `gift-brainstorm` (personalized gift idea generation with budget tiers and gift pipeline integration) and `reconnect-planner` (Dunbar tier-aware stale contact identification and reconnection outreach planning using tier-weighted urgency ranking), plus shared skills `butler-memory` and `butler-notifications`

### Requirement: Relationship Memory Taxonomy
The relationship butler uses a person-centric memory taxonomy.

#### Scenario: Memory classification
- **WHEN** the relationship butler extracts facts
- **THEN** it uses the person's human-readable name as subject (with entity_id as anchor); predicates like `relationship_to_user`, `birthday`, `preference`, `current_interest`, `workplace`, `lives_in`, `dunbar_tier_override`; permanence `permanent` for identity facts and tier overrides, `stable` for workplace/location, `standard` for interests, `volatile` for temporary states

## ADDED Requirements

### Requirement: Relationship Butler Tool Surface — Dunbar Tier
The relationship butler exposes a tool for manual Dunbar tier overrides.

#### Scenario: Dunbar tier tool in tool inventory
- **WHEN** a runtime instance is spawned for the relationship butler
- **THEN** it MUST have access to `dunbar_tier_set(contact_id, tier)` for setting or clearing manual Dunbar tier overrides
- **AND** `contact_get` and `contact_search` responses MUST include `dunbar_tier` and `dunbar_score` fields
