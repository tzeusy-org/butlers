## MODIFIED Requirements

### Requirement: Relationship Butler Schedules

The relationship butler SHALL run date checks, maintenance sweeps, and memory jobs. `upcoming-dates-check` and `relationship-maintenance` SHALL be deterministic jobs: each renders a fixed template from an existing read and sends it to the owner through the owner-notify boundary, spawning no session and calling no model.

The registered upcoming_dates management tool and the scheduled upcoming-dates-check birthday/anniversary push SHALL consider only canonical people whose current posture is active at the date query; an unreadable posture SHALL raise rather than fabricate active or empty results. This contract SHALL preserve historical dates and the separate calendar overlay's existing remembrance behavior.

ID: REQ-butler-relationship-005
Source: RFC 0038 D2 and D18; bu-idc5mx.4
Scope: v1-mandatory

#### Scenario: Scheduled task inventory
- **WHEN** the relationship butler daemon is running
- **THEN** it executes: `upcoming-dates-check` (0 8 * * *, job: check birthdays/anniversaries in the next 7 days), `relationship-maintenance` (0 9 * * 1, job: rank overdue contacts by Dunbar tier-weighted urgency and suggest top 3 reconnections), `memory-consolidation` (0 */6 * * *, job), `memory-episode-cleanup` (0 4 * * *, job), and `insight-scan` (0 7 * * *, job: evaluate relationship domain data and generate insight candidates)

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
- **AND** the `upcoming-dates-check` job SHALL send no reminder or all-clear on that failed read, and the scheduler records the job failure

#### Scenario: Daily reminder uses only the successful active date projection
- **WHEN** the 08:00 upcoming-dates-check runs
- **THEN** its job SHALL call the registered upcoming_dates(days_ahead=7), stay silent for a successful empty result, and send one templated owner reminder only from a successful active-date result
- **AND** this tool/push SHALL NOT expose memorial remembrance without a separately adopted opt-in

#### Scenario: Maintenance job sends no all-clear when cadence is unmeasurable
- **WHEN** the weekly `relationship-maintenance` job runs and the overdue evaluation reports `cadence_available` false
- **THEN** it SHALL send no reconnect suggestion and no all-clear
- **AND** when cadence is available and no contact is overdue it SHALL send exactly "All your key relationships are up to date — no overdue check-ins this week."

#### Scenario: Maintenance ranks only eligible overdue contacts by unified urgency
- **WHEN** the weekly `relationship-maintenance` job finds overdue, measurable contacts
- **THEN** it SHALL rank only those contacts by the dunbar-tier-scoring unified urgency score and suggest at most 3, fewer when fewer qualify
- **AND** a contact that is not overdue SHALL NOT be suggested, whatever its context bonus
- **AND** an overdue contact missing from the tier ranking SHALL be counted and logged, not ranked from raw interaction rows

#### Scenario: Scheduled reminders spawn no session
- **WHEN** the `upcoming-dates-check` or `relationship-maintenance` job runs
- **THEN** it SHALL spawn no session and call no model, and its message text SHALL come from a fixed template
- **AND** it SHALL send through the owner-notify boundary: quiet-hours and context-bus suppression, the owner's resolved Telegram recipient, a `notify.v1` send through the Switchboard `deliver` tool, and one attention-ledger row per outcome (delivered, suppressed or failed)
- **AND** a suppressed send SHALL be dropped and recorded rather than deferred, and a failed send SHALL NOT be retried by the job
