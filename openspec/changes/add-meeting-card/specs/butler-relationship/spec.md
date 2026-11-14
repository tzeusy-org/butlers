## ADDED Requirements

### Requirement: Meeting card job

The Relationship butler SHALL provide a deterministic, zero-LLM `meeting_card_push` job that
proposes at most one timed insight per `(event_id, start_at)` for each non-cancelled, non-all-day,
non-transparent, non-butler-generated calendar occurrence that the owner did not decline, that
starts within the next 20 minutes, and that has at least one other attendee after the owner's own
entry, any address that resolves to the owner, room resources and declined guests are removed.
The job SHALL queue the card on the
insight broker's timed lane with `deliver_after` equal to the run time and `expires_at` equal to
the meeting's start, and SHALL NOT send anything itself. The card SHALL declare that it passes the
`meeting` and `focused` context signals: an owner in a meeting or focused is who it is for, so
those two do not hold it, while `dnd`, `sleeping` and quiet hours still do. The timed lane SHALL
honor that declaration only for this producer's category, SHALL never honor it for `dnd` or
`sleeping`, and SHALL hold every other timed candidate for every suppressing signal exactly as
before. On each run the job SHALL withdraw, through the broker's withdraw path and never by
deletion, every pending card whose meeting no longer has a live occurrence at the start the card
was queued for. The job SHALL be inert while `MEETING_CARD_LIVE` is off, which is its default.

ID: REQ-butler-relationship-007
Source: heart-and-soul/vision.md (timely insights without being asked); RFC 0026 Moment Prep
Scope: v1-mandatory

#### Scenario: Meeting with a guest is carded once

- **WHEN** the job runs twice inside the 20-minute window of a meeting with one other attendee
- **THEN** one card is queued and the second proposal is reported as a duplicate

#### Scenario: Moved meeting

- **WHEN** a carded meeting moves to a new start time inside the window
- **THEN** the earlier card is withdrawn and one new card is queued under the new start

#### Scenario: Cancelled meeting

- **WHEN** a meeting with a pending card is cancelled or removed
- **THEN** its pending card is withdrawn and nothing is queued

#### Scenario: Meeting and focused do not hold the card

- **WHEN** the owner's context is `meeting` or `focused` and a card is due
- **THEN** the card is delivered
- **AND** any other timed candidate due at the same time is held by that signal

#### Scenario: Dnd and sleeping still hold the card

- **WHEN** the owner's context is `dnd` or `sleeping`
- **THEN** the card is held and expires at its meeting's start unsent

#### Scenario: Excluded meetings

- **WHEN** the job runs over a solo block, a meeting whose only other attendee is the owner's own
  other address, a meeting whose only guests declined, a room-only invite, a meeting the owner
  declined, a transparent block and a butler-generated event
- **THEN** none of them is carded

#### Scenario: Dark by default

- **WHEN** `MEETING_CARD_LIVE` is off
- **THEN** the job returns without reading or writing

### Requirement: Meeting card content

The card SHALL be a fixed template with no LLM: the meeting title and local start time, the
location, each named attendee with the date they last met, the commitments the owner owes them
and those they owe the owner, and a link to the dashboard Calendar page when a dashboard origin
is configured. It SHALL read only the Relationship prep envelope `calendar/prep/<event_id>`. A
missing envelope, or one whose `event_starts_at` is not the occurrence's start, SHALL yield a
minimal card that says "No prep context yet." and names the attendees without prep fields.

Title, attendee names and email fallbacks, commitment summaries and the location are third-party
or invite text and SHALL be stripped of markup and control characters, truncated and defanged
(`.`, `:` and `@` shown as `[.]`, `[:]` and `[@]`), because insight delivery sends the text as
written and no inert-link rendering runs on it. A location SHALL never render as a link: a URL
SHALL show only its defanged host.
An attendee whose posture is not `active`, or who resolves to the owner, SHALL NOT be named and
SHALL contribute no prep lines; the meeting is still carded.

ID: REQ-butler-relationship-008
Source: about/heart-and-soul/security.md (third-party content; owner-notify inert rendering)
Scope: v1-mandatory

#### Scenario: Prep present

- **WHEN** the envelope names an attendee with a last-met date, a commitment the owner owes and one owed to the owner
- **THEN** the card shows the attendee with the last-met date, "You owe" and "Waiting on" lines

#### Scenario: Prep missing or stale

- **WHEN** no envelope exists, or its `event_starts_at` differs from the occurrence start
- **THEN** the card says "No prep context yet." and shows no last-met or commitment lines

#### Scenario: Invite and person text is defanged

- **WHEN** the title contains a URL, an attendee's name is a bare email address or reads like an instruction, or a commitment summary contains a link
- **THEN** each renders as plain text with no clickable URL, address or host

#### Scenario: Conference link and location are inert

- **WHEN** the location is `https://zoom.us/j/123?pwd=secret`
- **THEN** the card shows only the defanged host and no path, query or clickable link

#### Scenario: Non-active attendee is withheld

- **WHEN** the only other attendee has posture `memorial` or `no_contact`
- **THEN** the card is queued without that person's name or prep lines
