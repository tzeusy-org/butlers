## Tasks

### 1. Extend prep job with commitment query

Add commitment-class `owner_conditions` query to
`run_relationship_calendar_prep_contribution()` in
`src/butlers/jobs/calendar_prep.py`. For each resolved attendee, query
`public.owner_conditions` filtered by `metadata->>'class' = 'commitment'` and
`metadata->>'counterparty_entity_id' = attendee.entity_id`, active episodes
only, ordered by escalation_level DESC, capped at `MAX_COMMITMENTS_PER_ATTENDEE`.
Add `PrepCommitment` TypedDict and extend `PrepAttendee` with `commitments` field.
Fail-open: wrap query in try/except, log warning, default to empty list.

Acceptance:
- Prep envelope includes `commitments` per attendee with correct fields
- Empty list when no commitments exist
- Fail-open on query failure (existing context unaffected)
- Escalation-first ordering respected
- Cap enforced

### 2. Extend API response models

Add `commitments` array to `CalendarPrepAttendee` (or equivalent) response model
in `src/butlers/api/models/calendar_workspace.py`. Normalize absent field to
empty list for backward compatibility with pre-commitment envelopes. Pass through
in `query_calendar_prep` read model.

Acceptance:
- API response includes `commitments` per attendee
- Legacy envelopes (no `commitments` field) return `[]`
- Response model validates kind/direction enum values

### 3. Frontend prep rail commitment rendering

Extend the prep rail component to render commitment chips per attendee. Each chip
shows kind icon, direction indicator, summary text, and deadline when present.
Visually emphasize commitments at escalation level >= 2.

Acceptance:
- Commitment chips render for attendees with active commitments
- Empty state: no commitment section shown when list is empty
- High-escalation commitments visually distinct
- Responsive layout (chips wrap correctly on narrow viewports)
