## MODIFIED Requirements

### Requirement: General Butler Schedules
The general butler SHALL run memory maintenance, briefing aggregation, a deterministic tomorrow day-sheet push (run by the dashboard-api process), and a daily end-of-day preparation prompt that incorporates cross-butler data. The preparation prompt SHALL NOT enumerate tomorrow's events when the day-sheet push has recorded an outcome for tomorrow, and it SHALL derive dates from the owner's timezone, never a fixed offset.

ID: REQ-butler-general-010
Source: bu-5ii4gj.2 (run 26 move 2), slice S3
Scope: v1-mandatory

#### Scenario: Scheduled task inventory
- **WHEN** the general butler daemon is running
- **THEN** it executes: `memory_consolidation` (0 */6 * * *, job), `memory_episode_cleanup` (0 4 * * *, job), `collect_briefing_contributions` (58 6 * * *, job: 14:58 SGT; aggregates specialist briefing contributions into combined payload), and `eod-tomorrow-prep` (0 23 * * *, prompt-based: 23:00 owner-local end-of-day briefing incorporating cross-butler specialist summaries, sent via Telegram)

#### Scenario: Day sheet push is deterministic and dark by default
- **WHEN** the dashboard-api `day_sheet_push` loop ticks with `DAY_SHEET_PUSH_ENABLED` unset
- **THEN** it reads and sends nothing
- **AND** when enabled, a tick inside 22:30 to 23:30 owner-local sends `render_day_sheet_text` over the owner-local tomorrow, from the same `query_calendar_day_sheet` fan-out the dashboard serves, composed by no model
- **AND** a prior `delivered` ledger row for the same digest ends the day, while a failed or suppressed send does not and is retried
- **AND** it records `day_sheet/pushed/<date>` and `day_sheet/pushed/latest` (carrying `date`, `outcome` and `digest`) in the general butler's state

#### Scenario: EOD briefing leaves the calendar to the day sheet
- **WHEN** the `eod-tomorrow-prep` prompt executes and `day_sheet/pushed/latest` carries tomorrow's owner-local date
- **THEN** the briefing does not list or describe tomorrow's events
- **AND** an outcome other than `delivered` is reported as "could not be confirmed", never as a clear day
- **AND** with no applicable marker it falls back to `calendar_list_events`, and a failed call is reported as unreadable, never as "No events scheduled"

#### Scenario: EOD briefing reads combined contributions
- **WHEN** the `eod-tomorrow-prep` prompt executes
- **THEN** it reads `state_get('briefing/combined/<today-SGT>')` to obtain the aggregated specialist data
- **AND** incorporates sections with `has_updates=true` into the briefing message
- **AND** omits sections with `has_updates=false` entirely

#### Scenario: EOD briefing without specialist data
- **WHEN** the `eod-tomorrow-prep` prompt executes and `briefing/combined/<today>` is absent or empty
- **THEN** the briefing omits the highlights section and carries only the calendar line
