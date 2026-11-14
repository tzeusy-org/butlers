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
- **Holds.** The card is time-critical, so `meeting` and `focused` must not hold it; `dnd`,
  `sleeping` and quiet hours still do. A candidate declares `metadata.passes_context`, and the timed
  lane honors it only for `(relationship, meeting-card)` (`timed.CONTEXT_PASS_ALLOWED`) and never
  for `dnd` or `sleeping`; every other candidate is held by every signal as before. "Away" is not
  a broker signal and none is invented here. Nothing in this job reads the owner's posture.
- **Reschedule.** Each run withdraws pending cards whose event has no live occurrence at the
  start in the card's key (moved, cancelled or deleted) through
  `broker.withdraw_pending_candidates`, the premise recheck's guarded pending-to-withdrawn
  transition and `withdrawn` attention event, then queues the moved meeting's new card. A
  send-time premise would need a definer function for the Switchboard to read the Relationship
  calendar tables, so it is not added.
- **Owner alias.** An attendee address that resolves to the owner is the owner for eligibility:
  a meeting whose only other address is the owner's own gets no card.
- **Third-party text** (title, names, summaries, location) is stripped of markup and control
  characters and truncated. A location is never a link: a URL shows its host defanged, other text
  has its separators defanged.
- **Reads** are the relationship pool's own `calendar_events`, `calendar_event_instances`,
  `relationship.entity_facts`, `public.entities` and `state`; no cross-schema view.

## Alternatives rejected

- Queue at T-60 and let the lane time it: the card would outlive a reschedule without a premise.
- Read the merged prep view for message topics: needs a new grant for the Relationship role.

## Activation conditions (notes, not built)

- Schedule `meeting_card_push` at least as often as `insight_timed_subcycle`, so a card is queued
  before the sub-cycle that could send it.
- A meeting the owner declines after its card was queued is not withdrawn today (only a moved,
  cancelled or deleted occurrence is); that withdrawal comes with activation.
