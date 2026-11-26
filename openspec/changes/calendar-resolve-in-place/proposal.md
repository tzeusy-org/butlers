## Why

The conflict radar detects an overlap and cannot finish it. Its only door is a create-only proposal
that nothing produces, and its dismiss control hides the banner for one browser session, writes no
receipt and leaves the verdict still counting the overlap. The owner sees "overlap" and must find a
slot, reopen the event and edit it by hand (bu-5ii4gj.11, calendar pursuit run 26 move 11).

## What changes

- **Resolution options.** Each overlap issue carries one to five options compiled by a pure,
  deterministic function (no model): move a Butlers block, move an owner-organised event, decline or
  tentatively accept an invited meeting, ask the organizer for a new time with a typed template, or
  keep both. Every option names its exact tool call and its authority class.
- **Resolve in place.** `POST .../conflicts/{pair_id}/resolve` recomputes the options server side,
  refuses a stale digest with 409 and no write, runs the exact call as the owner's own submit and
  returns a receipt and an undo.
- **Keep both is a premise, not a hide.** `public.calendar_conflict_acknowledgements` records the
  owner's decision against both events' etags. An acknowledged pair resolves its owner condition and
  drops out of the radar and the verdict count until either event changes.
- **The Fix card goes.** `ConflictIssue.proposal_ids` and the "Fix card with proposal" behaviour are
  removed; the card renders the options as keyboard-addressable verbs.

## Out of scope

No model in the compiler, no auto-resolution, no series-wide edits, no free-text message to a third
party, no booking or spending, no dashboard event edit dialogs (bu-kf3n4i). `ask_organizer` is
parked until standing-reply templates are active (bu-jv1moa.28). Change plans and the Needs-you
queue are separate changes.

## Slices (each its own PR after this spec is reviewed)

R1 pure compiler and the read fields; R2 the resolve route, the acknowledgement table and the
suppression, with `proposal_ids` removed; R3 parked decline and tentative plus the keyboard verbs.
