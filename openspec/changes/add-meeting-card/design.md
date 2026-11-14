## Context

Precedents: `home_departure` (dark timed card), `meeting_debrief` (occurrence selection and
attendee resolution). Both are reused, not copied: selection helpers come from `meeting_debrief`,
queueing is `propose_insight_candidate(lane="timed")`.

## Decisions

- **Proposed inside the lead window only.** The job runs every few minutes once activated and
  queues a card for a meeting starting in (1 min, 20 min], with `deliver_after = now` and
  `expires_at = start`. A card is therefore never queued early and never delivered after the
  meeting began, so no premise probe is needed.
- **Dedup key** `relationship:meeting-card:<event_id>:<start epoch>`; the timed lane's unique
  dedup index makes a second proposal a `duplicate`.
- **Posture.** The attendee block (names, last met, commitments) lists only `active` people. A
  non-`active` or owner-resolved attendee is not named; the meeting still counts as having other
  attendees, so the card carries title, time and place only.
- **Holds.** The timed lane holds for `dnd` and `sleeping` and for quiet hours; `meeting` and
  `focused` do not hold it. Nothing in this job reads the owner's posture.
- **Third-party text** (title, names, summaries, location) is stripped of markup and control
  characters and truncated. A location is never a link: a URL shows its host defanged, other text
  has its separators defanged.
- **Reads** are the relationship pool's own `calendar_events`, `calendar_event_instances`,
  `relationship.entity_facts`, `public.entities` and `state`; no cross-schema view.

## Alternatives rejected

- Queue at T-60 and let the lane time it: the card would outlive a reschedule without a premise.
- Read the merged prep view for message topics: needs a new grant for the Relationship role.
