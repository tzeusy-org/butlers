## Why

The departure card (bu-jv1moa.8, merged as #5789/#5790) tells the owner what was left insecure and
stops there. Three promises from the same outcome were deferred to bu-jv1moa.29 and have no
specification:

- a one-tap fix for a specific item the card names, not a bundle;
- an arrival card of what changed while the owner was away;
- a way to say what happened to a fix that was offered (waiting, done, failed, cancelled), so a card
  never claims an effect that did not occur.

The earlier immediate-lane idea (a faster lane for flight delays and safety) was explicitly excluded
from the owner's approval of the timed lane (bu-pyg3o1, choice A). It needs its own exact-text
adoption, so this change does not write it.

## What changes

- Specify per-item **parked fixes**: an exact stored Home Assistant call, declared by the owner for
  one watched item, parked through the existing approvals module when the departure card names the
  item. One fix per item, no "secure all" action, always owner-approved regardless of risk class.
- Specify **cancellation**: a pending fix is rejected when the owner returns, the leave is retracted
  or the item is already secure. A cancelled fix never claims an effect was undone, and an executed
  fix is never rolled back automatically.
- Specify the **arrival card**: one read-only timed insight per confirmed enter edge, listing what
  opened, closed or was done while the owner was away, with each fix's actual outcome.
- Specify a **prepared-action status resolver**: a closed, content-blind status for a parked Home
  action, taken from the approval row and the actuation receipt, usable by cards and by the timed
  lane's premise check.
- Record the **immediate-lane boundary**: nothing in this change uses or defines an immediate lane;
  what adopted doctrine already says about urgency is restated, and everything else is an owner gate.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `home-deterministic-jobs`: adds REQ-home-deterministic-jobs-008 through 012 (spec delta in this
  change). IDs 002 through 007 belong to `specify-home-away-safety`.

## Impact

Future owners:

- Home migrations (a fix declaration table, an arrival evidence table), the Home deterministic job
  module, owner-only Home API routes, the Home dashboard departure-watch panel;
- a definer read function for the Switchboard premise probe, following `public.resolve_home_departure`.

Unaffected:

- RFC 0028's risk map and receipts, and the approvals state machine (no new status);
- the departure card's read-only behavior until a fix is declared and the fix flag is on;
- the urgent sub-cycle, quiet hours, DND, sleep and budgets.

Status: **specification proposed, not adopted or implemented**. Tracking: `bu-jv1moa.29`. Base:
`a38cf00fac`. No producer, schedule, flag, button or lane is activated by this change.
