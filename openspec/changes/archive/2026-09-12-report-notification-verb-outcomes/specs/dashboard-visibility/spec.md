## MODIFIED Requirements

### Requirement: Notification Audit Trail
The Notifications page (`/notifications`) SHALL provide a complete audit trail of every notification sent by any butler across all delivery channels. This surface is essential for verifying that user-facing communications were delivered successfully and diagnosing delivery failures.

#### Scenario: Notification stats bar
- **WHEN** the Notifications page loads
- **THEN** a four-card stats bar displays: Total Notifications (with bell icon), Sent count (green, with checkmark icon), Failed count (red, with X icon), and Failure Rate percentage (color-coded: green if 0%, amber if 0-10%, red if >10%)
- **AND** below the cards, a per-channel breakdown shows each channel name with its count as a badge

#### Scenario: Notification filter bar
- **WHEN** the operator interacts with the notification filter bar
- **THEN** five filter controls are available: Butler (free-text input), Channel (dropdown: All / Telegram / Email), Status (dropdown: All / Sent / Failed / Terminal failures / Pending / Read / Retried), Since (date input), Until (date input)
- **AND** a "Clear filters" button appears when any filter is active

#### Scenario: Terminal-failure filter and stats breakdown

- **WHEN** the Notifications page requests `GET /api/notifications?status=terminal_failed`
- **THEN** the list includes failed notifications that have no later sent notification
  with the same session, channel, and message
- **AND** it excludes failed attempts that later matching deliveries retried successfully
- **AND** the Status filter visibly names this predicate as `Terminal failures`
- **AND** `GET /api/notifications/stats`'s `failed` count and `by_butler` breakdown use
  that same terminal-failure predicate
- **AND** verdict links that name a bounded notification count preserve that response's
  exact `since` and `until` interval

#### Scenario: Notification feed table
- **WHEN** notifications are loaded
- **THEN** a table displays columns: Status (badge), Butler (source butler name), Channel (capitalized badge), Message (truncated to 60 chars), and Time (relative)
- **AND** failed notification rows receive `bg-destructive/5` background

#### Scenario: Notification-to-session and trace cross-links
- **WHEN** a notification has a `session_id`
- **THEN** a "Session {shortId}" link is displayed below the message, navigating to `/sessions/{session_id}?butler={source_butler}`
- **WHEN** a notification has a `trace_id`
- **THEN** a "Trace {shortId}" link is displayed below the message, navigating to `/ingestion?tab=timeline`
- **AND** both links are styled as primary-colored underlined text

#### Scenario: Notification status badges
- **WHEN** status is "sent"
- **THEN** a green "Sent" badge is rendered
- **WHEN** status is "failed"
- **THEN** a red destructive "Failed" badge is rendered
- **WHEN** status is "pending"
- **THEN** an amber outlined "Pending" badge is rendered

#### Scenario: Inline retry and escalate verbs on a failed row
- **WHEN** a notification row's `effective_status` is `failed`
- **THEN** the row's triage column SHALL offer inline "Retry" and "Escalate" verbs alongside "Mark read"/"Dismiss", calling `POST /api/notifications/{id}/retry` and `POST /api/notifications/{id}/escalate` respectively
- **AND** a row whose `effective_status` is `sent`, `read`, `retried`, or `escalated` SHALL NOT offer either verb -- there is nothing left to re-deliver
- **AND** both verbs are honest-pending: the clicked row reads "Retrying..."/"Escalating..." and both verbs stay disabled until the real round trip settles, with no optimistic status flip

#### Scenario: Retry and escalate outcomes are reported to the operator
- **WHEN** a retry or escalate call returns 200 and the new attempt's `status` is `sent`
- **THEN** a success toast names the channel the attempt landed on
- **WHEN** the call returns 200 but the new attempt's own `status` is `failed`
- **THEN** an error toast is shown carrying that attempt's `error` as its description -- a re-send that did not deliver SHALL NOT be reported as a success
- **WHEN** the call is rejected (409 for a row that is no longer `failed`, 422 for a channel with no alternate or a missing owner contact, 404, 503, or a transport error)
- **THEN** an error toast is shown carrying the endpoint's `detail` as its description, so a stale list still offering "Retry" on a row another tab already actioned explains itself instead of clearing silently

#### Scenario: Empty state with filter hint
- **WHEN** no notifications match the current filters
- **THEN** the empty state message reads "No notifications match the current filters. Try clearing the filters to see all notifications."
- **WHEN** no notifications exist at all (no filters active)
- **THEN** the empty state reads "Notifications will appear here as butlers send messages via Telegram, email, and other channels."
