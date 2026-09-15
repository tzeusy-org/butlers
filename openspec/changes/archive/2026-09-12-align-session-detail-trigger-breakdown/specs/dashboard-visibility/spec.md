## ADDED Requirements

### Requirement: Sessions Verdict Withholds Partial Trigger Attribution

The sessions failure verdict SHALL distinguish incomplete trigger attribution
from scalar aggregate degradation. It SHALL retain a truthful failed-session
count while refusing to name a trigger source as the dominant cluster when
`trigger_breakdown_degraded_sources` is non-empty.

#### Scenario: Partial trigger breakdown cannot support a trigger-dominance claim
- **WHEN** the failed-session aggregate has one or more
  `trigger_breakdown_degraded_sources` and matching scalar failures
- **THEN** the verdict names that trigger attribution is unavailable from the
  affected source or sources
- **AND** it retains the scalar failure count
- **AND** it SHALL NOT render a `clustered on {trigger_source}` claim or a
  trigger-filter link derived from the partial breakdown

#### Scenario: Complete trigger breakdown keeps existing trigger attribution
- **WHEN** the failed-session aggregate has trigger buckets and an empty
  `trigger_breakdown_degraded_sources` list
- **THEN** the verdict may use the most concentrated trigger source as its
  existing cluster label and link

## MODIFIED Requirements

### Requirement: Session Detail Full Page
The `SessionDetailPage` (`/sessions/:id`) SHALL provide a full-page view of a
single session. It serves as the deep-link target for session references from
other surfaces (notifications, timeline).

#### Scenario: Global session fetch accepts legacy butler query state
- **WHEN** the URL is `/sessions/{id}` with or without a legacy
  `?butler=<name>` query parameter
- **THEN** the page SHALL ignore the query parameter and use the global
  endpoint (`getSession(id)`) for the same cross-butler lookup
- **AND** it SHALL NOT select or require a butler-scoped session-detail
  endpoint

#### Scenario: Breadcrumb navigation
- **WHEN** the session detail page loads
- **THEN** a breadcrumb trail shows: Sessions (link to `/sessions`) > `{id.slice(0, 8)}` (current page)

#### Scenario: Full metadata display
- **WHEN** the page renders a session
- **THEN** it shows: Butler (link to `/butlers/{butler}`), Trigger Source (badge), Started, Completed, Duration, Model (if present), Tool Calls count (if present, showing array length or string representation), and Tokens in/out (if present)

#### Scenario: Error display
- **WHEN** the session has an `error` field
- **THEN** an "Error" card renders with `text-destructive` title and the error in a preformatted block with `bg-destructive/10` background

### Requirement: Loading and Error States
All visibility surfaces SHALL handle loading and error states consistently to prevent operator confusion.

#### Scenario: Skeleton loading states
- **WHEN** data is loading for any table (Sessions, Notifications, Audit Log)
- **THEN** skeleton rows are displayed with animated placeholder bars matching the column layout
- **WHEN** data is loading for the Timeline
- **THEN** 8 skeleton rows with timestamp, badge, and text placeholders are shown
- **WHEN** data is loading for the Topology
- **THEN** a `h-96` animated pulse placeholder is shown

#### Scenario: Empty states
- **WHEN** no data matches the current view (after loading completes)
- **THEN** a centered empty state message is shown with a descriptive title and explanation
- **AND** the message varies by surface (e.g. "No sessions found" with "Sessions will appear here as butlers process triggers and scheduled tasks.")

#### Scenario: Error states
- **WHEN** the session detail API call fails
- **THEN** a destructive-styled error message is shown without suggesting a
  `?butler=` remedy, because the global session lookup is authoritative
- **WHEN** the notification feed fails to load
- **THEN** a destructive-styled message reads "Failed to load notifications. Please try refreshing the page."
