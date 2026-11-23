## Why

`chronicler-situational-inference` (PR #5457) gives Chronicler a place-driven decision tree:

- At the work anchor → Work.
- At home → Rest.
- At an unregistered place → Social.

A deep dive on 2026-11-03 tested that tree against the owner's dev data for Sep 4 – Nov 3. Four inputs to the tree are wrong or missing, and these, more than the tree's shape, cause most of the remaining unaccounted time.

[Observed, dev database]:

- **Sleep has holes, and the waking window is not the owner's.**
  - The watch recorded no sleep on 3 of the last 14 nights. One of them was Oct 31 → Nov 1, behind the 11.3 h gap prompt on Nov 1.
  - The fixed 06:00–22:00 waking window does not match the owner. Their median bedtime is about 00:50 and their median wake time about 07:25. The window counts 06:00–07:25 sleep as unexplained, and it ignores 22:00–02:00, when most gaming happens.
- **"Exercise" is mostly ambling.** Google Health auto-detected 124 walks in 28 days, 95 h in total. 82 of them (76 h) average under 2.5 km/h; one was 88 minutes for 1.7 km. Exercise ranks above everything except sleep, so these walks carve about 3 h/day out of outings.
- **Steam play is placed by guesswork.**
  - The Chronicler Steam adapter knows only a daily playtime total, so it anchors each day's play to the poll time. Nov 1's 258 minutes were placed at 21:38–01:56.
  - The Steam connector already detects in-game changes every 5 minutes. On Nov 1 they came in two runs, about 13:15–16:51 and about 21:06–01:56. No table stores them, so Chronicler cannot read them.
- **Trips get no blocks.** A stay more than 50 km from home gets no block, so the Oct 21–30 trip in Japan is mostly unaccounted. The calendar holds the flights and the hotel stay, but it cannot be relied on to say which events are travel: they land under whichever butler schema projects them first.

The deep dive also found signals that the owner chose **not** to use:

- **Location and PC power suggest work from home.** Location put the owner at home at 11:00 on 28 of 29 non-travel weekdays. The study PC draws a steady ~90 W on weekdays from 09:00 to 20:00. The owner chose to keep work office-only.
- **The living-room PC peaks at 300–440 W during game sessions.** The owner chose that only Steam counts as Play.

## Owner decisions (2026-11-03, this change's authority)

1. **Home is never Work.** Work is inferred only at the registered work place, which keeps the earlier office-only decision. PC power, home presence sensors and the occupation, focus and screen inferences never make home time Work.
2. **Only Steam means Play.** Play comes from Steam's in-game status, with the daily Steam total as a fallback. PC power draw is never a lane source. Spotify, which the earlier taxonomy counted as Play, now counts toward no lane.
3. **The whole trip is Travel.** Every waking hour on a trip counts as Travel, including any workout. This places trips above exercise in the precedence order.

Text that conforms to these decisions is approved by them. An independent reviewer checks conformance in each PR.

## What changes

- **One fusion point.** `chronicler.situation_inferred` derives every place-dependent block in precedence order, so none of them overlap. Workouts, Steam sessions and Spotify become evidence, and the adapter turns the qualifying ones into blocks.
- **Inferred sleep and owner awake time.** A night with no watch sleep gets a low-confidence `inferred_sleep_block` over the longest quiet run inside the owner's usual sleep window. Untracked time and the gap interview measure the day minus its sleep, not a fixed 06:00–22:00 window.
- **Exercise needs effort.** A workout is Exercise when the owner recorded or started it, when it is not a walk, or when it averages at least 4 km/h for at least 10 minutes. The Google Health connector stops folding owner-started workouts into `auto`. A slow walk adds nothing itself; the movement, outing or home block around it decides its time.
- **Steam in-game status becomes timed Play.** The Steam connector stores each in-game change. Chronicler projects minute-precision `play_session` evidence and a `play_block` for the part not away from home. A date that has sessions has its daily-total guess suppressed.
- **Trip days are Travel.** A trip span runs from the departure fix before the owner goes more than 50 km from home to the first fix back within 50 km. A `trip_block` covers it, minus sleep and owner answers. The gap interview asks nothing for a day spent mostly on a trip.
- **Work only at the work place, end to end.** Occupation, focus and screen episodes become evidence that can corroborate a work block; they never count Work on their own.
- **Amends `chronicler-situational-inference`.** That change's lane taxonomy gains the new block types and drops Spotify from Play. Its "no block beyond 50 km" clause now defers to the trip rule.

## Non-goals

- Work-from-home inference, PC-power lanes and home-presence lanes (owner decisions 1 and 2).
- Meals: no meal source exists (`health.meals` is absent). The gap interview's Eat answer remains the only source.
- ActivityWatch: it is not running on either PC (0 events). Owner-optional; this change does not depend on it.
- Wi-Fi SSID auto-learning: only 23 fixes in the trail carry an SSID, too few to matter yet.
- Changing the place clustering or anchors from `chronicler-situational-inference`. The precedence order changes in one place only: trips now rank above exercise (decision 3).
- Calendar-derived trips: calendar events cannot be reliably attributed to travel today.

## Work items

Epic `bu-r3fmja`. Children: `.5` (sleep and awake time), `.6` (exercise effort), `.7` (Steam sessions and Spotify), `.8` (trips) and `.9` (home is never Work). Each depends on `.1` (PR #5457).
