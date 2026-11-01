## ADDED Requirements

### Requirement: Calendar Debrief Card
The dashboard SHALL expose `GET /api/relationship/meeting-debriefs/pending`, returning exactly what
the Relationship `meeting_debrief_pending` tool returns (same items, same order, same posture
filtering: attendees who are no longer `active` are left out and a debrief with no remaining
attendee is omitted). A failed read SHALL return the degraded envelope `debriefs_available: false`
with no items and a log line carrying no row content; it SHALL NOT read as an empty list of
answered debriefs.

The Calendar workspace page SHALL render a Debrief card listing each pending debrief with its batch
number, title, local time and attendees. The card SHALL be hidden when nothing is pending and SHALL
show a degraded note when the read is unavailable. Replying from the card SHALL send
`"<number>: <text>"` as an owner message to the Relationship butler through the existing pinned
dashboard conversation endpoint (`POST /api/butlers/relationship/conversations`), SHALL keep the
typed text and show an inline error on failure, and SHALL refetch the pending list on success. The
card adds no route, classification or answer form: the `meeting-debrief` session skill records the
answer.

ID: REQ-dashboard-api-071
Source: bu-q7vx1q.63; openspec change add-meeting-debrief task 3.1
Scope: v1-mandatory

#### Scenario: Pending debriefs match the tool
- **WHEN** the owner opens the Calendar page with two prompted, unanswered debriefs
- **THEN** the endpoint returns both in the order `meeting_debrief_pending` returns them
- **AND** an attendee whose posture is not `active` is not named

#### Scenario: Nothing pending hides the card
- **WHEN** the endpoint returns an empty list with `debriefs_available: true`
- **THEN** the Calendar page renders no Debrief card

#### Scenario: Unreadable list is not all-answered
- **WHEN** the endpoint returns `debriefs_available: false` or the request fails
- **THEN** the Debrief card shows a degraded note and no empty card

#### Scenario: Reply goes through the pinned conversation
- **WHEN** the owner sends "send Sam the deck by Friday" on a debrief
- **THEN** the dashboard posts `"[debrief:<debrief_id>] send Sam the deck by Friday"` to the Relationship pinned conversation, carrying the debrief's stable id because batch numbers restart at 1 per prompt batch
- **AND** the message SHALL NOT carry the calendar title, attendees or any other invite-controlled text, which stays tool data from `meeting_debrief_pending`
- **AND** the reply is reported sent only once the stream yields `dispatch_accepted` or `message_complete`; an `error` event or a stream that ends with neither is a failure
- **AND** on failure the text is kept with an inline error and a retry reuses the same message id, and on success the pending list is refetched

#### Scenario: Duplicate batch numbers do not misroute a reply
- **WHEN** two pending debriefs both show number 1
- **THEN** a reply from each card carries its own `debrief_id`
- **AND** the `meeting-debrief` skill matches by `debrief_id` when present and by number only otherwise
