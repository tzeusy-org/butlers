## Why

Chronicler does not work out where the owner is or what they are doing, even when the evidence makes it obvious (owner report, 2026-11-03; epic bu-r3fmja). On 2026-11-02 the gap interview said "about 4.8h of unaccounted waking time — was that a work day?". On 2026-11-01, a Sunday, it said 11.3h. OwnTracks showed the owner at home for most of both days, apart from short outings. Findings from the dev database:

- The phone reports in significant-change mode, about 13 fixes a day, and stays silent while stationary. Place clustering ends a stay at any gap over 60 minutes. Only 8 `place_episode` rows exist, from 2,532 fixes. On Nov 2 the owner was home from 02:54 to 12:44 with no fix in between, so that stay was never seen.
- Chronicler cannot resolve a home or work place. `public.owner_places` is empty, `OWNTRACKS_PLACE_REFERENCES` is unset, and no OwnTracks regions are defined. Every place label is `place_unknown`.
- Work is inferred only from mined two-hour routine windows. All five routines are disabled, and location is never consulted.
- Every `place_episode`, wherever it is, counts as Rest.
- The gap question asks about a work day even on a Sunday. On a day with no occupation block, tapping "Work day" records nothing.

## Owner decisions (2026-11-03, this change's authority)

1. Work location is systemic. It comes from an address registered as work in the memory graph, converted separately to latitude/longitude. Assume the owner works in the office full time.
2. The gap interview asks about the single largest unexplained block, with lane buttons. The answer becomes a counted correction.
3. A long stay at an unregistered place counts as Social.
4. Geocoding uses OneMap for Singapore addresses and OpenStreetMap Nominatim for the rest. Only addresses the owner registered are sent, never the location trail.

Text that conforms to these decisions is approved by them. An independent reviewer checks conformance in each PR.

## What changes

- **Stays survive sparse reporting.** Place clustering bridges a silent gap when the fixes on both sides fall in the same place. A stay ends at the first fix away from the place, bounded by a bridge cap. Place episodes become evidence ("where"), not counted activity.
- **Anchor places.** One deterministic resolver gives Chronicler the owner's home and work places. Sources, in order: the owner place registry, then the memory graph (owner `works-at` → organization `has-address`; the owner's own `has-address`, or a `rental-location`/`lives-at` place's address), then the legacy env references. For home only, a fallback infers the owner's usual overnight place. No coordinate reaches a model.
- **Geocoding in Relationship.** Relationship geocodes only the owner's anchor addresses. It stores the result in `relationship.address_geocodes`, keyed by the `has-address` fact and the hash of the address text. Chronicler reads that table.
- **Situational activity precedence.** A new deterministic adapter, `chronicler.situation_inferred`, turns where-segments into activity blocks under a fixed precedence: sleep > exercise > travel > at the work place → Work > game play → Play > at another place → Social > at home with nothing else → Rest. A block never re-counts time a higher-precedence activity already owns.
- **Gap interview asks about one block.** The prompt names the longest unexplained waking block and what location says about it. Its buttons are Work, Social, Play, Eat, Rest and Dismiss. A lane answer writes an owner-attested activity block. The existing occupation confirm flow is kept for days with an inferred occupation block.

## Non-goals

- Reverse-geocoding fixes to city names (still bu-2tsgqb.10).
- Writing to `public.owner_places` (Home's editor), or changing its readers (bu-jv1moa.7 owns that cutover).
- The live presence reducer and situation slots (bu-jv1moa.17 and bu-dhloi1.9). This change is retrospective only.
- Inferring work from home-office signals. The owner works in the office, so work at home is not inferred.
- Notifications. The gap interview stays the single opted-in daily prompt.
