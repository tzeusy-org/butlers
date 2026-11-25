## Why

The commonest way time silently stops being true is an organizer moving, relocating or re-membering
a meeting the owner already accepted: the projection overwrites in place and nothing compares the
current row with what the owner agreed to. Reminders have the same flaw: "an hour before the
dentist" is stored as an absolute time and fires at a stale hour when the dentist moves. And an
owner's own drag of a Butlers-owned event in Google is silently reverted (bu-5ii4gj.10, calendar
pursuit run 26 move 10).

## What changes

- **Agreement snapshot.** When the owner's RSVP becomes accepted or tentative, the calendar module
  records what was agreed (start, end, location digest, join digest, other-attendee set digest) in
  `calendar_owner_agreements`, one row per provider identity and original start.
- **Changed since you said yes.** A deterministic job compares the current occurrence with the
  snapshot, using the connector's revision journal for the observed revisions, and reconciles one
  `owner_conditions` episode per changed occurrence. A proposer emits one premise-bound candidate
  per episode; a revert before send withdraws it and a revert after send amends it in place.
- **Occurrence probe.** `public.resolve_calendar_occurrence` answers open, closed or unknown so the
  premise is rechecked at send. Unknown delivers the candidate stamped "as of", never as rechecked.
- **Reminders that follow their event.** `reminder_create` accepts an anchor (provider identity,
  original start, offset). An anchored reminder rebases when its occurrence moves, retires when it
  is cancelled or declined, and the tick rechecks the anchor at fire.
- **The owner's gesture is an instruction.** A drag of a Butlers-owned one-off reminder or task in
  Google is adopted when the owner is the calendar's only writer, with a receipt and an undo card;
  any other editor, or an unreadable ACL, parks an owner card instead. The scenario that reverts
  silently is replaced.

## Slices (each its own PR, after this spec is reviewed)

- Changed since you said yes: A-S1 snapshot table and writer, A-S3 probe, A-S2 reconciler, A-S4
  proposer, A-S5 dashboard chip.
- Reminders: R-S1 anchor fields, R-S2 rebase and retire, R-S3 fire-time check, R-S4 owner-drag
  adoption.
- Gesture: S1 push-loop honesty is already on main (calendar-mirror-truth, core_377); S2 adoption
  with If-Match, S3 recurring-item question.

## Decisions to take before R-S4 and the gesture slices

The revision journal records no editor identity. Adoption therefore trusts the calendar's ACL: only
when the owner is its sole writer. Another writer, or an ACL read that fails, parks an owner card.
If the owner would rather adopt on the strength of the owner surface alone, that is a separate,
weaker rule and needs an explicit decision.

## Not in this change

No card for an immaterial edit (title or description alone), no auto-RSVP, no auto-decline, no
organizer messaging, no LLM summary of what changed, no change detection for events the owner
organised, no provider-native reminders, no anchors to non-calendar facts, no natural-language
"before X".

## Trust boundary

Title, location and attendee names in a card are third-party text: rendered through `inert_text`
and `defang_delivery_text`. Description text never reaches a card.
