# Dashboard Visibility

## MODIFIED Requirements

### Requirement: Notification Audit Trail

The Notifications page (`/notifications`) SHALL provide a complete audit trail of every
notification sent by any butler across all delivery channels. This surface is essential
for verifying that user-facing communications were delivered successfully and diagnosing
delivery failures.

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
