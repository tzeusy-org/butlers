## Why

Day-close reconciliation ignored location (bu-2tsgqb.7, Chronicler review 2026-10-22):

- Oct 16: the recap called the SQ 800 flight to Beijing "unconfirmed" although OwnTracks had the owner at Changi until 01:33 and at Beijing airport from 08:14.
- Oct 21: a butler-written "Japan Trip: SIN -> Aomori" block was narrated as a "tentative travel account" while OwnTracks put the owner in central Beijing. The block was contradicted, and it was a butler's plan, not the owner's calendar.

`reconcile_day` let only rest-lane activity contradict an intent, and nothing marked who wrote a calendar block.

## What changes

- A pure travel location check (`travel_location.py`) weighs one-way travel intents (a flight, or a route block `A -> B`, 30 min to 24 h long) against accepted OwnTracks fixes. It compares an origin anchor around the departure with a destination anchor around the arrival:
  - same place (within 50 km): the intent is dropped as contradicted, with `evidence="location"`;
  - at least 300 km apart at a plausible speed: the intent is kept with a `consistent` location note, which states only the displacement and never that the trip was taken;
  - anything else: `unknown`.
- Anchors need two fixes at one place. Fixes flagged `trail_outlier` by the shared trail policy (bu-2tsgqb.6), fixes with accuracy worse than 1 km, naive timestamps and non-normal privacy rows are ignored.
- The day-close bundle tool reads OwnTracks `location` events over each travel window plus 6 h either side, so a flight crossing local midnight sees the neighbouring day's fixes.
- The calendar adapter copies the calendar module's `butler_generated`/`butler_name` marker into the episode payload. Reconciliation stamps `reconciliation.plan_producer` (`butler` from that flag or the `BUTLER:` title prefix, else `calendar`) on every intent, and the day-close prompt narrates butler plans as plans, never as the owner's schedule or attendance.

## External-data decision (recorded)

No geocoding. The check never resolves an airport or city name, so it cannot test an event's stated location or origin against the fixes. That needs the owner decision already recorded as a follow-up by clean-owntracks-location-trail (offline gazetteer vs remote geocoder).

## Non-goals

- Counting travel time or raising any activity confidence from location.
- Location checks for non-travel intents (a meeting's location), which need geocoding.
- Using health sleep or walking evidence as travel corroboration.
