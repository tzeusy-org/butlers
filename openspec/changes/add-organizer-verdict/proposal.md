## Why

An owner block on a sender stops only the Switchboard envelope. A blocked or unknown organizer's
invitation still sits in the invitation inbox, counts toward day load, raises conflict-radar
issues and triggers meeting prep, the meeting card and the debrief. Invitation titles and
descriptions are third-party text, so an invitation from a stranger is also an injection channel
(bu-5ii4gj.6, calendar pursuit run 26 move 6).

## What changes

- One pure function, `organizer_verdict`, computes a verdict per organizer from server-verified
  provider fields plus two owner-owned lookups passed in as values: `owner`, `known_entity`,
  `known_domain`, `first_contact`, `blocked`, or `unknown` when an input could not be read. Closed
  lure badges accompany it. It never reads a title, description or any free text.
- The calendar projection stamps `organizer_verdict`, `lure_badges` and `verdict_version` into the
  event's metadata. A bounded, idempotent sweep (keyed on event id and `verdict_version`) stamps
  existing rows and re-stamps after a verdict input changes.
- Two SECURITY DEFINER functions give the calendar-hosting butler roles the two cross-schema reads
  they do not have: identity class of an email, and whether connector or global ingestion policy
  blocks a sender. A failed read yields `unknown`, never a known verdict.
- Quarantine: a `needsAction` invitation from a `first_contact`, `blocked` or `unknown` organizer
  (or an unstamped one) holds no time and reaches no prompt as text. The owner's own accept always
  wins.

## Deferred

- Readers honoring the stamp: the invitations read model and route, day load, conflict radar,
  prep, meeting card, debrief and household context (slice S2).
- Vouch and Block owner verbs (slice S3).
- The connector's fail-open on a policy evaluation error (slice S4).
