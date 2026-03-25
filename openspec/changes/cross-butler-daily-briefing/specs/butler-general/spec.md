## MODIFIED Requirements

### Requirement: General Butler Schedules
The general butler runs memory maintenance, briefing aggregation, and a daily end-of-day preparation prompt that incorporates cross-butler data.

#### Scenario: Scheduled task inventory
- **WHEN** the general butler daemon is running
- **THEN** it executes: `memory-consolidation` (0 */6 * * *, job), `memory-episode-cleanup` (0 4 * * *, job), `collect-briefing-contributions` (58 6 * * *, job: aggregates specialist briefing contributions into combined payload), and `eod-tomorrow-prep` (0 7 * * *, prompt-based: end-of-day briefing reviewing tomorrow's calendar AND incorporating cross-butler specialist summaries, sent via Telegram)

#### Scenario: EOD briefing reads combined contributions
- **WHEN** the `eod-tomorrow-prep` prompt executes
- **THEN** it reads `state_get('briefing/combined/<today-SGT>')` to obtain the aggregated specialist data
- **AND** incorporates sections with `has_updates=true` into the briefing message
- **AND** omits sections with `has_updates=false` entirely

#### Scenario: EOD briefing without specialist data
- **WHEN** the `eod-tomorrow-prep` prompt executes and `briefing/combined/<today>` is absent or empty
- **THEN** the briefing degrades gracefully to calendar-only format (current behavior preserved)

## ADDED Requirements

### Requirement: EOD Briefing Multi-Domain Format
The EOD briefing message SHALL follow a structured multi-domain format when specialist contributions are available. The message SHALL be under 500 words for mobile readability.

#### Scenario: Full briefing with specialist sections
- **WHEN** the combined briefing payload has contributions with `has_updates=true`
- **THEN** the message includes a calendar timeline section followed by a "Today's Highlights" section grouping specialist summaries by domain (Health, Finance, Travel, Relationships, Learning, Home)
- **AND** only domains with `has_updates=true` appear in the output

#### Scenario: Cross-domain heads-up flags
- **WHEN** multiple specialist contributions contain high-priority highlights
- **THEN** the briefing MAY include a "Heads-up" section at the end highlighting cross-domain correlations (e.g., travel departure conflicting with a medical appointment)

#### Scenario: Calendar-only fallback
- **WHEN** no specialist contributions have `has_updates=true`
- **THEN** the briefing renders the calendar timeline only, matching the existing format
