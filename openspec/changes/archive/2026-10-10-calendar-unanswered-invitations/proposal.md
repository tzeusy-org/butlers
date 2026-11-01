## Why

The Calendar radar already excludes owner-declined events from load analysis,
but the owner has no read-only inbox for explicitly unanswered invitations.
Listing the parser's default RSVP would incorrectly turn missing provider data
into an invitation, and an unavailable source must not produce a quiet verdict.

## What Changes

- Add explicit provider-status provenance to projected attendee metadata while
  retaining the existing tool/model defaults and radar behavior.
- Add a bounded, cursor-paginated `GET /api/calendar/workspace/invitations` read
  over the current projection, with organizer and earned radar evidence doors.
- Add a compact invitations strip to the Calendar opener, with honest loading,
  partial, unknown, retained-data and clean-empty states.

## Capabilities

### Modified Capabilities

- `dashboard-api`: additive read-only invitation endpoint and Calendar strip.
- `module-calendar`: additive explicit-status projection provenance.

## Impact

No migration, backfill, provider request, RSVP write, recurrence change or new
mutation receipt. Legacy normalized `needsAction` without provenance remains
unknown. This does not deliver the parent invitation-response action or .18.

## Verification and Delivery

Use existing parser, projection, read-model, router and Calendar owning species;
add one real migrated-projection contract species where mocks cannot prove SQL.
Independent source review, fresh hosted and protected delivery remain required.
