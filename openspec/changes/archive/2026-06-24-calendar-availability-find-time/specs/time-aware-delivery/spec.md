## ADDED Requirements

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
