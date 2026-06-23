# Time-Aware Delivery

## Purpose
Provides quiet hours enforcement and notification batching for butler outbound notifications. Delivery preferences (quiet hours, timezone, batch settings) are stored per-butler in a `delivery_preferences` table. Deferred notifications are persisted in a `deferred_notifications` table and flushed by the scheduler's tick loop. High-priority notifications always bypass quiet hours; medium and low-priority notifications are deferred to a configurable batch delivery time.

## ADDED Requirements

### Requirement: Delivery Preferences Configuration
Delivery preferences are stored in a `delivery_preferences` table with fields: `id` (UUID), `butler_name` (unique), `quiet_hours_start` (time, default 22:00), `quiet_hours_end` (time, default 07:00), `timezone` (string, required), `batch_low_priority` (boolean, default true), `batch_delivery_time` (time, default 07:00), `override_channels` (JSONB, optional -- per-channel overrides), `created_at`, `updated_at`.

#### Scenario: Create delivery preferences
- **WHEN** `delivery_preferences_set(timezone="America/New_York", quiet_hours_start="22:00", quiet_hours_end="07:00", batch_low_priority=true)` is called
- **THEN** a `delivery_preferences` row is upserted for this butler

#### Scenario: Default quiet hours applied
- **WHEN** no `delivery_preferences` row exists for this butler
- **THEN** the system assumes no quiet hours enforcement (notifications deliver immediately)

#### Scenario: Invalid timezone rejected
- **WHEN** `delivery_preferences_set(timezone="Invalid/Zone")` is called
- **THEN** a `ValueError` is raised indicating the timezone is not recognized

### Requirement: Quiet Hours Enforcement
The `notify()` tool SHALL check delivery preferences before constructing the notification envelope. During quiet hours (computed in the user's local timezone), notifications are classified by priority and handled accordingly.

#### Scenario: High-priority notification during quiet hours
- **WHEN** `notify(channel="telegram", message="Urgent alert", priority="high")` is called
- **AND** the current time in the user's timezone is 23:30 (within quiet hours 22:00-07:00)
- **THEN** the notification is delivered immediately (quiet hours bypassed)

#### Scenario: Low-priority notification during quiet hours
- **WHEN** `notify(channel="telegram", message="Weekly summary ready", priority="low")` is called
- **AND** the current time in the user's timezone is 01:00 (within quiet hours)
- **AND** `batch_low_priority` is true
- **THEN** the notification is deferred to the `deferred_notifications` table
- **AND** the tool returns `{"status": "deferred", "deliver_at": "07:00 local"}`

#### Scenario: Medium-priority notification during quiet hours
- **WHEN** `notify(channel="telegram", message="Appointment tomorrow", priority="medium")` is called
- **AND** the current time in the user's timezone is within quiet hours
- **THEN** the notification is deferred to the `deferred_notifications` table

#### Scenario: Notification outside quiet hours
- **WHEN** `notify(channel="telegram", message="Update available")` is called
- **AND** the current time in the user's timezone is 14:00 (outside quiet hours)
- **THEN** the notification is delivered immediately regardless of priority

### Requirement: Notification Priority Classification
The `notify()` tool SHALL accept an optional `priority` parameter (enum: `high`, `medium`, `low`, default `medium`). Priority determines quiet-hours behavior. High-priority notifications always deliver immediately. Medium and low-priority notifications are subject to quiet-hours deferral.

#### Scenario: Default priority is medium
- **WHEN** `notify(channel="telegram", message="Info")` is called without a `priority` parameter
- **THEN** priority defaults to `medium`

#### Scenario: Invalid priority rejected
- **WHEN** `notify(channel="telegram", message="Test", priority="urgent")` is called
- **THEN** an error response is returned listing valid priority values

### Requirement: Deferred Notification Storage
Deferred notifications are stored in a `deferred_notifications` table with fields: `id` (UUID), `butler_name`, `channel`, `message`, `priority`, `envelope` (JSONB -- full notify.v1 envelope), `deferred_at` (timestamp), `deliver_at` (timestamp -- computed from `batch_delivery_time` in user timezone), `status` (enum: `pending`, `delivered`, `expired`, `cancelled`), `delivered_at` (timestamp, nullable).

#### Scenario: Deferred notification persisted
- **WHEN** a medium-priority notification is deferred during quiet hours
- **THEN** a row is inserted into `deferred_notifications` with `status='pending'` and `deliver_at` computed as the next occurrence of `batch_delivery_time` in the user's timezone

#### Scenario: Daemon restart preserves deferred notifications
- **WHEN** the daemon restarts after a deferred notification was stored
- **THEN** the deferred notification remains in the database and is delivered at the scheduled `deliver_at` time

### Requirement: Deferred Notification Flush
The scheduler's `tick()` function SHALL include a deferred-notification flush pass. On each tick, it queries `deferred_notifications` where `status='pending' AND deliver_at <= now()`, delivers each via the standard notify pipeline, and updates `status='delivered'` and `delivered_at`.

#### Scenario: Deferred notifications delivered at batch time
- **WHEN** `tick()` runs at 07:01 local time
- **AND** 3 deferred notifications have `deliver_at <= now()` and `status='pending'`
- **THEN** all 3 are delivered via the standard notify pipeline
- **AND** each is updated to `status='delivered'` with `delivered_at=now()`

#### Scenario: Failed deferred delivery retries on next tick
- **WHEN** a deferred notification delivery fails (e.g., Switchboard unreachable)
- **THEN** the notification remains `status='pending'`
- **AND** delivery is reattempted on the next tick

#### Scenario: Expired deferred notifications
- **WHEN** a deferred notification has been `pending` for more than 24 hours past its `deliver_at`
- **THEN** the notification is set to `status='expired'`
- **AND** it is NOT delivered

### Requirement: Delivery Preferences MCP Tools
The module SHALL register MCP tools: `delivery_preferences_set`, `delivery_preferences_get`, `deferred_notifications_list`, `deferred_notification_cancel`.

#### Scenario: Get current delivery preferences
- **WHEN** `delivery_preferences_get()` is called
- **THEN** the current `delivery_preferences` for this butler are returned
- **AND** if no preferences exist, a response indicating defaults (no quiet hours) is returned

#### Scenario: List pending deferred notifications
- **WHEN** `deferred_notifications_list(status="pending")` is called
- **THEN** all pending deferred notifications for this butler are returned with their `deliver_at` times

#### Scenario: Cancel deferred notification
- **WHEN** `deferred_notification_cancel(id)` is called
- **THEN** the notification's status is set to `cancelled`
- **AND** it is NOT delivered at its scheduled time

### Requirement: Per-Channel Quiet Hours Override
Delivery preferences MAY include per-channel overrides via the `override_channels` JSONB field. A channel override specifies different quiet hours for a specific channel (e.g., email has wider quiet hours than telegram).

#### Scenario: Channel override applied
- **WHEN** delivery preferences have `override_channels={"email": {quiet_hours_start: "20:00", quiet_hours_end: "09:00"}}`
- **AND** `notify(channel="email", message="Report", priority="medium")` is called at 21:00 local time
- **THEN** the email-specific quiet hours (20:00-09:00) apply and the notification is deferred

#### Scenario: Channel without override uses defaults
- **WHEN** delivery preferences have `override_channels={"email": {...}}`
- **AND** `notify(channel="telegram", message="Update", priority="medium")` is called during default quiet hours
- **THEN** the default quiet hours (22:00-07:00) apply to the telegram notification

### Requirement: Owner Scheduling-Availability Preferences

The system SHALL store the owner's scheduling-availability preferences as a single owner-scoped record, distinct from the per-butler notification quiet hours in `delivery_preferences`. These preferences answer "when may a meeting occupy the owner's time?" (a life/availability concern owned by the human), NOT "when may a butler send a notification?" (the existing per-butler concern). The record holds: `earliest_meeting_time`, `latest_meeting_time`, `meeting_days` (allowed weekdays), `timezone` (owner/residence timezone), and `no_meeting_blocks` (recurring intervals such as a daily lunch). Slot-ranking consumers (the calendar module's `_build_suggested_slots` and `calendar_find_free_slots`) SHALL read these preferences when proposing times.

#### Scenario: Scheduling preferences are owner-scoped, not per-butler

- **WHEN** owner scheduling-availability preferences are set
- **THEN** they are stored as a single owner-scoped record, NOT keyed by `butler_name`
- **AND** they are separate storage from the per-butler `delivery_preferences` notification quiet hours

#### Scenario: Set owner scheduling preferences

- **WHEN** `scheduling_preferences_set(timezone="America/New_York", earliest_meeting_time="09:00", latest_meeting_time="18:00", meeting_days=["MO","TU","WE","TH","FR"], no_meeting_blocks=[{"start":"12:00","end":"13:00"}])` is called
- **THEN** the owner scheduling-availability record is upserted with those values

#### Scenario: Get owner scheduling preferences

- **WHEN** `scheduling_preferences_get()` is called
- **THEN** the current owner scheduling-availability preferences are returned
- **AND** if no record exists, a response indicating no scheduling constraints is returned

#### Scenario: Invalid timezone rejected

- **WHEN** `scheduling_preferences_set(timezone="Invalid/Zone")` is called
- **THEN** a `ValueError` is raised indicating the timezone is not recognized

#### Scenario: Scheduling preferences do not change notification quiet hours

- **WHEN** owner scheduling-availability preferences are set
- **THEN** the per-butler `delivery_preferences` quiet-hours behavior for `notify()` is unaffected
- **AND** widening or narrowing notification quiet hours does not change the bookable meeting window, and vice versa

#### Scenario: Slot ranking consumes the preferences

- **WHEN** a slot-ranking consumer (`_build_suggested_slots` or `calendar_find_free_slots`) builds candidate slots and an owner scheduling-availability record exists
- **THEN** candidate slots that start before `earliest_meeting_time`, end after `latest_meeting_time`, fall on a weekday not in `meeting_days`, or overlap a `no_meeting_blocks` interval are excluded
- **AND** when no record exists, slot ranking applies no life-availability filtering
