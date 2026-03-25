## 1. Database Schema

- [ ] 1.1 Create Alembic migration: add `task_type` (text, default 'cron'), `target_date` (date, nullable), `lead_time_days` (integer, nullable), `alert_thresholds` (JSONB, nullable), `deadline_status` (text, nullable), `fired_thresholds` (JSONB, nullable), `depends_on` (JSONB, nullable) columns to `scheduled_tasks` table
- [ ] 1.2 Create Alembic migration: add `event_chains` table (id UUID PK, name text UNIQUE per butler, trigger_type text, trigger_reference text, actions JSONB, status text default 'active', butler_name text, created_at, updated_at)
- [ ] 1.3 Create Alembic migration: add `seasonal_periods` table (id UUID PK, name text UNIQUE per butler, period_type text, start_month int, start_day int, end_month int, end_day int, timezone text, metadata JSONB, butler_name text, enabled boolean default true)
- [ ] 1.4 Create Alembic migration: add `delivery_preferences` table (id UUID PK, butler_name text UNIQUE, quiet_hours_start time, quiet_hours_end time, timezone text, batch_low_priority boolean default true, batch_delivery_time time default '07:00', override_channels JSONB, created_at, updated_at)
- [ ] 1.5 Create Alembic migration: add `deferred_notifications` table (id UUID PK, butler_name text, channel text, message text, priority text, envelope JSONB, deferred_at timestamp, deliver_at timestamp, status text default 'pending', delivered_at timestamp nullable)

## 2. Deadline Tracking Core

- [ ] 2.1 Add deadline validation logic: `target_date` must be future, `alert_thresholds` non-empty, `days_before` <= `lead_time_days`, valid date combinations
- [ ] 2.2 Implement deadline countdown computation: `days_remaining = (target_date - now().date()).days`, threshold matching with fired-threshold tracking
- [ ] 2.3 Implement deadline status state machine: pending -> alerted -> escalated -> completed/expired transitions
- [ ] 2.4 Implement deadline dependency checking: skip threshold evaluation when `depends_on` references incomplete deadlines
- [ ] 2.5 Implement deadline prompt context injection: augment dispatch prompt with target_date, days_remaining, fired_threshold, all_thresholds

## 3. Deadline MCP Tools

- [ ] 3.1 Implement `deadline_create` MCP tool with validation (target_date, lead_time_days, alert_thresholds)
- [ ] 3.2 Implement `deadline_update` MCP tool (target_date change resets fired thresholds and status)
- [ ] 3.3 Implement `deadline_list` MCP tool with status filter
- [ ] 3.4 Implement `deadline_delete` MCP tool (reject TOML-sourced deadlines)

## 4. Event Chains Core

- [ ] 4.1 Implement event chain action schema validation: action_type enum, delay_minutes >= 0, required fields per action type
- [ ] 4.2 Implement event chain trigger detection for `calendar_event_end`: query projection for events with end_at < now() since last tick
- [ ] 4.3 Implement event chain trigger detection for `deadline_passed`: detect deadline status transitions to expired/completed
- [ ] 4.4 Implement event chain trigger detection for `deadline_threshold`: detect threshold fires with severity matching
- [ ] 4.5 Implement chain action materialization: create one-shot scheduled_tasks with source='chain', computed delays, and until_at for auto-disable
- [ ] 4.6 Implement chain depth tracking and 3-level cascade limit

## 5. Event Chain MCP Tools

- [ ] 5.1 Implement `event_chain_create` MCP tool with duplicate name checking
- [ ] 5.2 Implement `event_chain_update` MCP tool (status reset on action change)
- [ ] 5.3 Implement `event_chain_list` MCP tool with trigger_type filter
- [ ] 5.4 Implement `event_chain_delete` MCP tool

## 6. Seasonal Awareness Core

- [ ] 6.1 Implement `get_active_seasons()` query with year-boundary wrapping logic
- [ ] 6.2 Implement month/day validation for seasonal period dates (reject Feb 30, etc.)
- [ ] 6.3 Implement seasonal context injection into tick() dispatch context
- [ ] 6.4 Define seasonal period presets (us-tax-season, year-end-holidays, back-to-school, spring-semester, fall-semester)

## 7. Seasonal Awareness MCP Tools

- [ ] 7.1 Implement `seasonal_period_create` MCP tool with duplicate name checking
- [ ] 7.2 Implement `seasonal_period_update` MCP tool with date validation
- [ ] 7.3 Implement `seasonal_period_list` MCP tool (include current active status)
- [ ] 7.4 Implement `seasonal_period_delete` MCP tool
- [ ] 7.5 Implement `seasonal_period_create_preset` MCP tool

## 8. Time-Aware Delivery Core

- [ ] 8.1 Implement quiet hours check: determine if current time (in user timezone) falls within quiet_hours_start/end range
- [ ] 8.2 Implement notification priority classification: add `priority` parameter to notify() tool (high/medium/low, default medium)
- [ ] 8.3 Implement quiet hours gate in notify() pipeline: defer medium/low during quiet hours, bypass for high
- [ ] 8.4 Implement per-channel quiet hours override resolution from delivery_preferences.override_channels
- [ ] 8.5 Implement deferred notification storage: persist envelope to deferred_notifications table with computed deliver_at

## 9. Deferred Notification Flush

- [ ] 9.1 Implement deferred notification flush pass in tick(): query pending notifications with deliver_at <= now(), deliver via standard notify pipeline
- [ ] 9.2 Implement deferred notification expiry: mark pending notifications older than 24 hours past deliver_at as expired
- [ ] 9.3 Implement failed delivery retry: keep status='pending' on delivery failure for next-tick retry

## 10. Delivery Preferences MCP Tools

- [ ] 10.1 Implement `delivery_preferences_set` MCP tool with timezone validation
- [ ] 10.2 Implement `delivery_preferences_get` MCP tool (return defaults when no preferences exist)
- [ ] 10.3 Implement `deferred_notifications_list` MCP tool with status filter
- [ ] 10.4 Implement `deferred_notification_cancel` MCP tool

## 11. Scheduler Integration

- [ ] 11.1 Extend tick() with deadline evaluation pass (before cron dispatch)
- [ ] 11.2 Extend tick() with event chain trigger detection pass (after cron/deadline dispatch)
- [ ] 11.3 Extend tick() with deferred notification flush pass (after chain detection)
- [ ] 11.4 Add telemetry span attributes: deadlines_evaluated, chains_fired, deferred_flushed
- [ ] 11.5 Extend TOML sync to handle task_type='deadline' entries with deadline fields

## 12. Tests

- [ ] 12.1 Write tests for deadline validation (future date, threshold bounds, non-empty thresholds)
- [ ] 12.2 Write tests for deadline countdown computation and threshold firing
- [ ] 12.3 Write tests for deadline status state machine transitions
- [ ] 12.4 Write tests for deadline dependencies blocking threshold evaluation
- [ ] 12.5 Write tests for event chain creation, trigger detection, and action materialization
- [ ] 12.6 Write tests for event chain depth limit enforcement
- [ ] 12.7 Write tests for seasonal period active detection (same-year, cross-year, disabled, multiple)
- [ ] 12.8 Write tests for quiet hours enforcement (high bypass, medium/low deferral, outside hours, no preferences)
- [ ] 12.9 Write tests for deferred notification flush (delivery, expiry, retry on failure)
- [ ] 12.10 Write tests for per-channel quiet hours overrides
- [ ] 12.11 Write tests for tick() integration with all three new passes
