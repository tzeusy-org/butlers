## Why

Butlers currently schedule work via cron expressions -- recurring triggers that fire at fixed clock times. This works for periodic tasks (daily check-ins, hourly polls) but cannot express countdown-based deadlines, post-event workflows, seasonal context, or timezone-aware delivery windows. Users need butlers that understand "6 weeks before my visa expires" or "after the doctor appointment, log the visit and update the next one" -- temporal reasoning that cron was never designed for.

## What Changes

- **Deadline tracking**: Butlers can register deadlines with target dates, lead times, dependency chains, and configurable alert thresholds. Deadlines are countdown-based triggers (days/weeks until a target date), not cron schedules. Examples: visa renewal 6 weeks before trip, medication refill 1 week before running out, tax filing 8 weeks before April 15.
- **Event chains**: Post-event trigger sequences that fire after a calendar event completes or a deadline passes. Enables automated follow-up workflows (after doctor appointment -> log visit, update next appointment; after tax filing deadline -> archive documents).
- **Seasonal/cyclical awareness**: Contextual awareness of recurring annual periods -- tax season, holidays, academic terms, annual renewals. Butlers can adjust behavior, surface relevant reminders, and prioritize work based on what season/period is active.
- **Time-aware delivery**: Quiet hours enforcement (default 22:00-07:00 local time) and intelligent batching of low-priority notifications for morning delivery. Respects user timezone for all delivery decisions.
- **Integration with existing scheduler**: All temporal features extend the current `core-scheduler` and `module-calendar` infrastructure rather than replacing it. Deadlines and event chains create `scheduled_tasks` entries with new metadata fields.

## Capabilities

### New Capabilities
- `deadline-tracking`: Countdown-based deadline registration with target dates, lead times, alert thresholds, and dependency chains. Extends scheduled_tasks with deadline-specific metadata.
- `event-chains`: Post-event trigger sequences that fire workflows after calendar events complete or deadlines pass. Defines trigger-action pairs with ordering and conditions.
- `seasonal-awareness`: Contextual period definitions (tax season, holidays, academic terms) with active-period detection and butler behavior modifiers.
- `time-aware-delivery`: Quiet hours enforcement, timezone-aware delivery windows, and low-priority notification batching for morning delivery.

### Modified Capabilities
- `core-scheduler`: Extended with deadline-type tasks, countdown computation, and event-chain trigger dispatch alongside existing cron dispatch.
- `core-notify`: Extended with delivery window enforcement (quiet hours) and notification batching/deferral for time-aware delivery.

## Impact

- **Database**: New tables for deadlines, event chains, seasonal periods, and delivery preferences. New columns on `scheduled_tasks` for deadline metadata.
- **Core scheduler** (`src/butlers/core/scheduler.py`): `tick()` gains deadline evaluation alongside cron evaluation. New countdown computation logic.
- **Core notify** (`src/butlers/core/notify.py`): Delivery pipeline gains quiet-hours gating and batch-deferral logic.
- **Module calendar** (`src/butlers/modules/calendar.py`): Event completion hooks feed into event-chain triggers.
- **MCP tools**: New tools for deadline CRUD, event chain management, seasonal period configuration, and delivery preference management.
- **butler.toml**: New config sections for delivery preferences (quiet hours, timezone) and seasonal period definitions.
