## MODIFIED Requirements

### Requirement: Relationship Butler Schedules

The relationship butler SHALL run date checks, maintenance sweeps, and memory jobs.

The registered upcoming_dates management tool and the scheduled upcoming-dates-check birthday/anniversary push SHALL consider only canonical people whose current posture is active at the date query; an unreadable posture SHALL raise rather than fabricate active or empty results. This contract SHALL preserve historical dates and the separate calendar overlay's existing remembrance behavior.

#### Scenario: Scheduled task inventory
- **WHEN** the relationship butler daemon is running
- **THEN** it executes: `upcoming-dates-check` (0 8 * * *, prompt-based: check birthdays/anniversaries in the next 7 days), `relationship-maintenance` (0 9 * * 1, prompt-based: rank overdue contacts by Dunbar tier-weighted urgency and suggest top 3 reconnections), `memory-consolidation` (0 */6 * * *, job), `memory-episode-cleanup` (0 4 * * *, job), and `insight-scan` (0 7 * * *, job: evaluate relationship domain data and generate insight candidates)

#### Scenario: Both upcoming date anchor paths enforce active posture
- **WHEN** listed contact-anchored or contactless local-entity-anchored people have dates inside the lookahead window
- **THEN** upcoming_dates SHALL return active people under the existing listed/date guards
- **AND** it SHALL omit memorial, quiet and no_contact people in both UNION arms while retaining an unrelated active positive row

#### Scenario: Memorial date reappears after reactivation
- **WHEN** a memorial person's birthday is in 3 days and the same person is later restored to active through the existing posture writer
- **THEN** upcoming_dates SHALL initially omit the date and then return the unchanged date after reactivation
- **AND** no historic date, memory or posture audit history SHALL be deleted

#### Scenario: Posture read failure cannot produce a reminder
- **WHEN** the real upcoming date query cannot read posture or its database query fails
- **THEN** the tool SHALL propagate the error rather than return an active fallback or a fabricated empty list
- **AND** the existing scheduled prompt SHALL instruct no reminder or all-clear on that failed read

#### Scenario: Daily reminder uses only the successful active date projection
- **WHEN** the 08:00 upcoming-dates-check runs
- **THEN** its prompt SHALL call the registered upcoming_dates(days_ahead=7), stay silent for a successful empty result, and draft an ordinary owner reminder only from a successful active-date result
- **AND** this tool/push SHALL NOT expose memorial remembrance without a separately adopted opt-in
