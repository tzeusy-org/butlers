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
- **Trips get no blocks.** A stay more than 50 km from home gets no block, so the Oct 21–30 trip in Japan is mostly unaccounted. The calendar already holds the flights and the hotel stay.

The deep dive also found signals that the owner chose **not** to use:

- **Location and PC power suggest work from home.** Location put the owner at home at 11:00 on 28 of 29 non-travel weekdays. The study PC draws a steady ~90 W on weekdays from 09:00 to 20:00. The owner chose to keep work office-only.
- **The living-room PC peaks at 300–440 W during game sessions.** The owner chose that only Steam counts as Play.

## Owner decisions (2026-11-03, this change's authority)

1. **Home is never Work.** Work is inferred only at the registered work place, which keeps the earlier office-only decision. PC power and home presence sensors never make home time Work.
2. **Only Steam means Play.** Play comes from Steam's in-game status, with the daily Steam total as a fallback. PC power draw is never a lane source.
3. **The whole trip is Travel.** Waking time on a trip away from home counts as Travel.

Text that conforms to these decisions is approved by them. An independent reviewer checks conformance in each PR.

## What changes

- **Inferred sleep and a personal waking window.** A night with no watch sleep gets a low-confidence `inferred_sleep` block. The block covers the longest quiet run inside the owner's usual sleep window. The waking window used for untracked time and the gap interview comes from that day's sleep, else from the owner's 28-day median, else from 06:00–22:00.
- **Exercise needs effort.** A Google Health workout counts as Exercise when the owner started it by hand, when it is not a walk, or when it averages at least 4 km/h for at least 10 minutes. Any other walk becomes on-foot evidence. Away from home it joins the surrounding outing or travel block; at home it counts nothing.
- **Steam in-game status becomes timed Play.** The Steam connector stores each in-game start and stop. Chronicler projects minute-precision `play_session` episodes from them and suppresses the daily-total guess for a date that has sessions. A session while the owner's phone is away from home is evidence only.
- **Trip days are Travel.** A trip span runs while the owner's latest fix is more than 50 km from home, extended by overlapping calendar flight, hotel and trip blocks. `chronicler.situation_inferred` emits a `trip_block` over that span, minus sleep and owner answers. The gap interview asks nothing for a day spent mostly on a trip.

## Non-goals

- Work-from-home inference, PC-power lanes and home-presence lanes (owner decisions 1 and 2).
- Meals: no meal source exists (`health.meals` is absent). The gap interview's Eat answer remains the only source.
- ActivityWatch: it is not running on either PC (0 events). Owner-optional; this change does not depend on it.
- Wi-Fi SSID auto-learning: only 23 fixes in the trail carry an SSID, too few to matter yet.
- Changing the place clustering, anchors or precedence order from `chronicler-situational-inference`.

## Work items

Epic `bu-r3fmja`. Children: `.5` (sleep and waking window), `.6` (walks), `.7` (Steam sessions) and `.8` (trips). Each depends on `.1` (PR #5457).
