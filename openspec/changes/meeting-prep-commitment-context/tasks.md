## Tasks

### 1. Extend prep job with commitment query

- [x] 1.1 Add commitment-class `owner_conditions` query to
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

- [x] 2.1 Add `commitments` array to `CalendarPrepAttendee` (or equivalent) response model
      in `src/butlers/api/models/calendar_workspace.py`. Normalize absent field to
      empty list for backward compatibility with pre-commitment envelopes. Pass through
      in `query_calendar_prep` read model.

Acceptance:
- API response includes `commitments` per attendee
- Legacy envelopes (no `commitments` field) return `[]`
- Response model validates kind/direction enum values

### 3. Frontend prep rail commitment rendering

- [x] 3.1 Extend the prep rail component to render rule-separated commitment rows per
      attendee. Each row shows kind icon, direction indicator, summary text, deadline
      when present, and the established `L0` through `L3` escalation label. Visually
      emphasize commitments at `L2` or `L3`.

Acceptance:
- Commitment rows render for attendees with active commitments
- Empty state: no commitment section shown when list is empty
- Established `L0` through `L3` labels render; `L2` and `L3` are visually distinct
- Responsive layout: grid rows remain readable on narrow viewports; the commitment list
  does not use a flex-wrap chip layout
