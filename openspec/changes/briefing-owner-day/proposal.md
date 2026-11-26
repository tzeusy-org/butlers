## Why

The briefing and calendar-overlay jobs read the owner's day in a fixed UTC+8, against RFC 0038's
no-default-zone rule (bu-5ii4gj.5.1, a sibling of the meeting card and calendar prep fix).

## What changes

- Every briefing contribution, the aggregation job and every overlay job reads the owner's day through
  one resolution of the owner's configured zone (the existing SGT fallback when none is usable).
- The contribution key, the overlay key, the day windows and the date bucketing follow that zone.
- For an owner in Singapore, or with no saved zone, every key is unchanged, so the cutover is a no-op.

## Not in this change

The SGT fallback itself (a census item, bu-enbrs2) and the owner clock (a trip's zone) stay as they are.
