## Why

The owner clock knows only the present, so a trip day's load, quiet hours and cards are computed in
the home zone (bu-5ii4gj.5, run 26 move 5).

## What changes

- A pure `projected_owner_zone(instant, legs, home_zone)` over owner-confirmed itinerary legs and a
  pinned airport and city to zone table (no outside geocoder).
- Later slices consume it: the overcommitment radar's off-hours-in-local-zone observation, day-load
  bucketing and the meeting card's dual time.

## Not in this change

No consumer yet, no migration, no change to the present-tense owner clock.
