# Time-Aware Delivery

## Purpose
Provides per-butler quiet-hours enforcement and notification batching for
butler outbound `send` and `insight` notifications. Delivery preferences (quiet
hours, timezone, batch settings) are stored per-butler in a
`delivery_preferences` table. Deferred notifications are persisted in the
originating butler's `deferred_notifications` table and flushed by the
scheduler's tick loop. The distinct global Owner Attention Policy or an active
suppressing context may also queue eligible routine implicit-owner
notifications with a supplied `deliver_at`; that path is not a per-butler
delivery preference. High-priority notifications bypass the per-butler
quiet-hours gate, while medium and low-priority notifications may be deferred
to a configurable batch delivery time.

## Requirements

### Requirement: Delivery Preferences Configuration
The system SHALL store configured per-butler delivery preferences in a `delivery_preferences` table with fields: `id` (UUID), `butler_name` (unique), `quiet_hours_start` (time, default 22:00), `quiet_hours_end` (time, default 07:00), `timezone` (string, required), `batch_low_priority` (boolean, default true), `batch_delivery_time` (time, default 07:00), `override_channels` (JSONB, optional -- per-channel overrides), `created_at`, `updated_at`.

#### Scenario: Create delivery preferences
- **WHEN** the Messenger butler calls `delivery_preferences_set(timezone="America/New_York", quiet_hours_start="22:00", quiet_hours_end="07:00", batch_low_priority=true)`
- **THEN** a `delivery_preferences` row is upserted for Messenger

#### Scenario: Default quiet hours applied
- **WHEN** no `delivery_preferences` row exists for this butler
- **THEN** the per-butler quiet-hours gate does not defer the notification

#### Scenario: Invalid timezone rejected
- **WHEN** the Messenger butler calls `delivery_preferences_set(timezone="Invalid/Zone")`
- **THEN** it returns an error indicating the timezone is not recognized

### Requirement: Quiet Hours Enforcement
The `notify()` tool SHALL resolve the recipient and enforce any decision dossier, then construct the `notify.v1` envelope before it evaluates per-butler delivery preferences for `send` and `insight` notifications. During configured local quiet hours, the per-butler gate classifies priority as follows.

#### Scenario: High-priority notification during quiet hours
- **WHEN** `notify(channel="telegram", message="Urgent alert", priority="high")` is called
- **AND** the current time in the user's timezone is 23:30 (within quiet hours 22:00-07:00)
- **THEN** the per-butler quiet-hours gate does not defer the notification

#### Scenario: Low-priority notification during quiet hours
- **WHEN** `notify(channel="telegram", message="Weekly summary ready", priority="low")` is called
- **AND** the current time in the user's timezone is 01:00 (within quiet hours)
- **AND** `batch_low_priority` is true
- **THEN** the notification is deferred to the `deferred_notifications` table
- **AND** the tool returns `{"status": "deferred", "deliver_at": "07:00 local"}`

#### Scenario: Medium-priority notification during quiet hours
- **WHEN** `notify(channel="telegram", message="Appointment tomorrow", priority="medium")` is called
- **AND** the current time in the user's timezone is within quiet hours
- **AND** `batch_low_priority` is true
- **THEN** the notification is deferred to the `deferred_notifications` table

#### Scenario: Notification outside quiet hours
- **WHEN** `notify(channel="telegram", message="Update available")` is called
- **AND** the current time in the user's timezone is 14:00 (outside quiet hours)
- **THEN** the per-butler quiet-hours gate does not defer it based on time or priority

### Requirement: Notification Priority Classification
The `notify()` tool SHALL accept an optional `priority` parameter (enum: `high`, `medium`, `low`, default `medium`). Priority determines per-butler quiet-hours behavior: high-priority notifications SHALL bypass that gate, while medium and low-priority notifications SHALL be subject to it.

#### Scenario: Default priority is medium
- **WHEN** `notify(channel="telegram", message="Info")` is called without a `priority` parameter
- **THEN** priority defaults to `medium`

#### Scenario: Invalid priority rejected
- **WHEN** `notify(channel="telegram", message="Test", priority="urgent")` is called
- **THEN** an error response is returned listing valid priority values

### Requirement: Deferred Notification Storage
The system SHALL store deferred notifications in a `deferred_notifications` table with fields: `id` (UUID), `butler_name`, `channel`, `message`, `priority`, `envelope` (JSONB -- full notify.v1 envelope), `deferred_at` (timestamp), `deliver_at` (timestamp -- set to the per-butler batch time or an authoritative owner-policy/context anchor), `status` (enum: `pending`, `delivered`, `expired`, `cancelled`), and `delivered_at` (timestamp, nullable).

The storage-and-flush mechanism SHALL also accept eligible routine
owner-default approvals-policy/context holds without a source-specific schema
field. That admission path supplies its own authoritative `deliver_at`: the
Owner Attention Policy's exact end-exclusive anchor or latest active suppressor
expiry. Each successful direct
owner-default hold retains one row per call; it SHALL NOT invent generic content
deduplication beyond the scheduler's existing delivery behavior.

This table's storage-and-flush mechanism can also hold an explicit retry envelope
for a genuinely *failed* delivery attempt, not just a quiet-hours defer. A
caller outside every butler daemon's own container (for example,
`butlers.jobs.secrets_lifecycle` scheduled inside `dashboard-api`) that hits a
transport error MAY insert a retry envelope into a target butler's
`deferred_notifications` table via `insert_deferred_notification`. The target
butler's scheduler makes that ordinary `deliver_at`-scheduled row eligible for
delivery on a later due tick; it has no schema or flush-pass distinction from a
quiet-hours defer. The attention-ledger row for the original attempt is
recorded with `outcome="failed"` (see the Notify Contract spec's Attention Ledger
requirement), not `outcome="deferred"`, because the retry is an explicit caller
action rather than the standard quiet-hours hold.

A caller that re-derives the same transition on a recurring scan MUST dedup its own retry envelopes so a persistent multi-tick outage does not accumulate one pending envelope per tick (which would all fire on recovery -- N+1 duplicate deliveries for a single transition). Before enqueueing a fresh retry envelope for a transition, the caller SHALL cancel prior `pending` envelopes for the same transition (supersede: latest state wins), and once a later direct delivery for the same transition succeeds it SHALL likewise cancel any leftover `pending` retry envelope, so switchboard's flush does not also redeliver it. This bounds the queue to one pending retry envelope per transition and yields exactly one delivery on the common recovery path. Because the `notify.v1` envelope is strictly re-validated on flush and cannot carry an out-of-band dedup field, the dedup token is a state-independent substring of the envelope's `message` (matched at a line boundary so a shorter token cannot collide with a longer sibling).

#### Scenario: Retry envelopes superseded across a persistent outage
- **WHEN** a recurring scan re-derives the same failed transition on each tick during a multi-tick transport outage
- **THEN** each tick cancels the prior `pending` retry envelope for that transition before enqueueing the latest one
- **AND** the `deferred_notifications` table holds at most one `pending` retry envelope for that transition at any time
- **AND** on recovery the owner receives a single delivery for the transition, not one per elapsed tick

#### Scenario: Deferred notification persisted
- **WHEN** a medium-priority notification is deferred during quiet hours
- **THEN** a row is inserted into `deferred_notifications` with `status='pending'` and `deliver_at` computed as the next occurrence of `batch_delivery_time` in the user's timezone

#### Scenario: Owner-default policy hold is persisted without a new schema
- **WHEN** the direct owner-default notify gate selects approvals-policy quiet
  hours
- **THEN** a pending row stores the full resolved envelope and the policy-derived
  UTC `deliver_at`
- **AND** the row uses no schema field dedicated to that hold source

#### Scenario: Stored Owner Attention Policy holds are not re-gated
- **WHEN** a routine owner-default notification was stored with a
  policy-derived UTC `deliver_at`
- **AND** the Owner Attention Policy changes before that timestamp becomes due
- **THEN** the scheduler uses the stored envelope and stored `deliver_at`
- **AND** it does not invoke a fresh policy gate or recalculate the anchor

#### Scenario: Owner-default context hold uses the supplied wake anchor
- **WHEN** the direct owner-default notify gate selects an active suppressing
  context
- **THEN** a pending row stores the full resolved envelope with the latest
  active suppressor expiry as `deliver_at`

#### Scenario: Daemon restart preserves deferred notifications
- **WHEN** the daemon restarts after a deferred notification was stored
- **THEN** the deferred notification remains in the database and is eligible for delivery on a later scheduler tick once `deliver_at` is due

### Requirement: Deferred Notification Flush
The scheduler's `tick()` function SHALL include a deferred-notification flush pass that expires stale pending rows and queries due rows where `status='pending' AND deliver_at <= now()`.

#### Scenario: Expired deferred notifications
- **WHEN** a deferred notification has been `pending` for more than 24 hours past its `deliver_at`
- **THEN** the notification is set to `status='expired'`
- **AND** it is NOT delivered

### Requirement: Deferred Notification Delivery and Digesting

The scheduler SHALL deliver a solo due row through the standard notify pipeline with its stored envelope. It SHALL compose one digest envelope for multiple due rows with the same delivery target that have no decision dossier or `approval_request`, then mark each affected row delivered only after success.

#### Scenario: Deferred notifications delivered at batch time
- **WHEN** `tick()` runs at 07:01 local time
- **AND** 3 deferred notifications have `deliver_at <= now()` and `status='pending'`
- **THEN** the rows are delivered individually or, when eligible rows share a delivery target, as one composed digest through the standard notify pipeline
- **AND** each affected row is updated to `status='delivered'` with `delivered_at=now()`

#### Scenario: Failed deferred delivery remains eligible for retry
- **WHEN** a queued deferred-notification delivery attempt fails (for example, Switchboard is unreachable)
- **THEN** the affected row or composed group remains `status='pending'`
- **AND** it is eligible for retry on a later scheduler tick

### Requirement: Delivery Preferences MCP Tools
The Messenger butler SHALL register MCP tools: `delivery_preferences_set`, `delivery_preferences_get`, `deferred_notifications_list`, and `deferred_notification_cancel`.

#### Scenario: Get current delivery preferences
- **WHEN** the Messenger butler calls `delivery_preferences_get()`
- **THEN** the current `delivery_preferences` for Messenger are returned
- **AND** if no preferences exist, a response indicates no per-butler quiet-hours preferences

#### Scenario: List pending deferred notifications
- **WHEN** the Messenger butler calls `deferred_notifications_list(status="pending")`
- **THEN** all pending deferred notifications for Messenger are returned with their `deliver_at` times

#### Scenario: Cancel deferred notification
- **WHEN** the Messenger butler calls `deferred_notification_cancel(id)` for a pending notification belonging to Messenger
- **THEN** the notification's status is set to `cancelled`
- **AND** it is NOT delivered at its scheduled time

### Requirement: Per-Channel Quiet Hours Override
The system SHALL support per-channel quiet-hours overrides through the `override_channels` JSONB field. When an override exists for `notify()`'s channel, the per-butler quiet-hours gate SHALL use its `quiet_hours_start` and `quiet_hours_end`; otherwise, it SHALL use the row's default quiet hours.

#### Scenario: Channel override applied
- **WHEN** delivery preferences have `override_channels={"email": {quiet_hours_start: "20:00", quiet_hours_end: "09:00"}}`
- **AND** `notify(channel="email", message="Report", priority="medium")` is called at 21:00 local time
- **AND** `batch_low_priority` is true
- **THEN** the email-specific quiet hours (20:00-09:00) apply and the notification is deferred

#### Scenario: Channel without override uses defaults
- **WHEN** delivery preferences have `override_channels={"email": {...}}`
- **AND** `notify(channel="telegram", message="Update", priority="medium")` is called during default quiet hours
- **AND** `batch_low_priority` is true
- **THEN** the default quiet hours (22:00-07:00) apply to the telegram notification

### Requirement: Owner Scheduling-Availability Storage

The system SHALL store owner scheduling-availability preferences as one owner-scoped `public.owner_scheduling_preferences` record, separate from per-butler `delivery_preferences`. The record includes the owner timezone and optional earliest/latest meeting times, allowed weekdays, and recurring no-meeting blocks.

#### Scenario: Scheduling preferences are owner-scoped, not per-butler

- **WHEN** owner scheduling-availability preferences are set
- **THEN** they are stored as a single owner-scoped record, NOT keyed by `butler_name`
- **AND** they are separate storage from the per-butler `delivery_preferences` notification quiet hours

#### Scenario: Set owner scheduling preferences

- **WHEN** the Messenger butler calls `scheduling_preferences_set(timezone="America/New_York", earliest_meeting_time="09:00", latest_meeting_time="18:00", meeting_days=["MO","TU","WE","TH","FR"], no_meeting_blocks=[{"start":"12:00","end":"13:00"}])`
- **THEN** the owner scheduling-availability record is upserted with those values

#### Scenario: Get owner scheduling preferences

- **WHEN** the Messenger butler calls `scheduling_preferences_get()`
- **THEN** the current owner scheduling-availability preferences are returned
- **AND** if no record exists, a response indicating no scheduling constraints is returned

#### Scenario: Invalid timezone rejected

- **WHEN** the Messenger butler calls `scheduling_preferences_set(timezone="Invalid/Zone")`
- **THEN** it returns an error indicating the timezone is not recognized

#### Scenario: Scheduling preferences do not change notification quiet hours

- **WHEN** owner scheduling-availability preferences are set
- **THEN** the per-butler `delivery_preferences` quiet-hours behavior for `notify()` is unaffected
- **AND** widening or narrowing notification quiet hours does not change the bookable meeting window, and vice versa

### Requirement: Scheduling-Availability Slot Filtering

The Calendar module SHALL apply configured owner scheduling-availability preferences in `_build_suggested_slots` and `calendar_find_free_slots` when generating candidate slots. If no preference record exists, those consumers SHALL apply no life-availability filtering.

#### Scenario: Slot ranking consumes the preferences

- **WHEN** a slot-ranking consumer (`_build_suggested_slots` or `calendar_find_free_slots`) builds candidate slots and an owner scheduling-availability record exists
- **THEN** candidate slots that start before `earliest_meeting_time`, end after `latest_meeting_time`, fall on a weekday not in `meeting_days`, or overlap a `no_meeting_blocks` interval are excluded
- **AND** when no record exists, slot ranking applies no life-availability filtering
