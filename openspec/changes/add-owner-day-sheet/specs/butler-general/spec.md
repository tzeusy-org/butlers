## MODIFIED Requirements

### Requirement: General Butler Schedules
The general butler SHALL run memory maintenance, briefing aggregation, a deterministic tomorrow day-sheet push, and a daily end-of-day preparation prompt that incorporates cross-butler data. The preparation prompt SHALL NOT enumerate tomorrow's events when the day-sheet push has recorded an outcome for tomorrow.

ID: REQ-butler-general-010
Source: bu-5ii4gj.2 (run 26 move 2), slice S3
Scope: v1-mandatory

#### Scenario: Scheduled task inventory
- **WHEN** the general butler daemon is running
- **THEN** it executes: `memory_consolidation` (0 */6 * * *, job), `memory_episode_cleanup` (0 4 * * *, job), `collect_briefing_contributions` (58 6 * * *, job: 14:58 SGT; aggregates specialist briefing contributions into combined payload), `day_sheet_push` (30 22 * * *, job: 22:30 owner-local; sends tomorrow's day sheet, a no-op unless `DAY_SHEET_PUSH_ENABLED` is set), and `eod-tomorrow-prep` (0 15 * * *, prompt-based: 23:00 SGT end-of-day briefing incorporating cross-butler specialist summaries, sent via Telegram)

#### Scenario: Day sheet push is deterministic and dark by default
- **WHEN** `day_sheet_push` runs with `DAY_SHEET_PUSH_ENABLED` unset
- **THEN** it reads and sends nothing
- **AND** when enabled it sends `render_day_sheet_text` over tomorrow's owner-local day, composed by no model
- **AND** it keys delivery by the sheet digest, so a rerun of unchanged content sends nothing
- **AND** it records `day_sheet/pushed/<tomorrow>` with the delivery outcome

#### Scenario: EOD briefing leaves the calendar to the day sheet
- **WHEN** the `eod-tomorrow-prep` prompt executes and `day_sheet/pushed/<tomorrow>` exists
- **THEN** the briefing does not list or describe tomorrow's events
- **AND** a recorded outcome other than `delivered` is reported as "could not be confirmed", never as a clear day
- **AND** with no marker it falls back to `calendar_list_events`, and a failed call is reported as unreadable, never as "No events scheduled"

#### Scenario: EOD briefing reads combined contributions
- **WHEN** the `eod-tomorrow-prep` prompt executes
- **THEN** it reads `state_get('briefing/combined/<today-SGT>')` to obtain the aggregated specialist data
- **AND** incorporates sections with `has_updates=true` into the briefing message
- **AND** omits sections with `has_updates=false` entirely

#### Scenario: EOD briefing without specialist data
- **WHEN** the `eod-tomorrow-prep` prompt executes and `briefing/combined/<today>` is absent or empty
- **THEN** the briefing omits the highlights section and carries only the calendar line
