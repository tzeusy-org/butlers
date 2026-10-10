## Authority and Scope

bu-q7vx1q.17 authorizes the paired additive contracts before implementation.
The dashboard reads projections; provider interaction stays with CalendarModule.
`self=true` denotes the provider copy, never a caller's authority to RSVP.

## Explicit Status

The old Google parser writes `needsAction` for explicit, absent and malformed
responseStatus. Keep AttendeeInfo and MCP payload defaults, track validity
separately while parsing, and persist a literal `response_status_explicit`
boolean in a separate ordered `attendee_status_provenance` companion binding
email and normalized status. Projected attendees remain exactly the unchanged
tool payloads. Only matching literal true plus exact needsAction
admits an unanswered self attendee. Unmarked legacy normalized/raw needsAction,
missing or malformed candidate status is unknown, omitted, and degrades admission.
No read retroactively repairs it. Known accepted/declined/tentative entries,
no-self, solo/self-organizer and cancelled entries remain excluded.

## Current Projection and Pagination

Use the existing 90-day half-open overlap window, provider-event source type,
fan-out status and deterministic source ownership. Deduplicate the complete
window and relevant same-origin copies using the workspace/radar strategy before
final window/status admission and keyset slicing; a stale unanswered duplicate
must not resurrect a newer accepted, cancelled or moved-outside-window copy.
Order by starts_at and instance UUID. The opaque cursor binds window and timezone
to that position; limit is 1..200. has_more/next_cursor describe remaining eligible
rows, not source completeness. Concurrent projection refreshes may alter a page,
as with the existing workspace keyset; no stale page cache is authoritative.

## Evidence and Failure

Organizer comes from event_metadata.organizer, then a labelled organizer attendee
fallback; absent organizer stays unknown. Exclude solo and self-organizer entries;
an external organizer or distinct other attendee establishes another party.
Conflict doors refer only to actual issue event IDs returned by the radar scan.
An unavailable scan never certifies conflict-free; any events/source ownership,
status admission or conflict read failure sets issues_available=false and names
safe degraded sources while keeping successful rows and observed issue doors.
Exceptions, SQL, provider URLs and credential-bearing strings are not diagnostics.

## UI

The page uses its visible range and timezone for the invitation query and keeps
data during refresh/error. The opener includes this source in calm admission.
Each native button opens the actual entry detail by ID; conflict buttons focus the
actual radar issue. The strip announces loading/degradation, labels unknown
organizer/conflict availability, offers bounded next pages, and earns empty only
after complete available reads. No mutation control is added.

## Rollback and Administration

Remove the endpoint/client/strip to roll back; leave projection truth intact.
Complete source/tests/read-only native preparation before genuine independent
review. Supported sync/archive/readback and resulting-head delivery are later
administrative work; no unchecked-task override or foreign native adoption.
