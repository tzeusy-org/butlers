## 1. Sleep and awake time (bu-r3fmja.5)

- [ ] 1.1 Pure sleep-window and quiet-run detection in `butlers.chronicler.situation`, with tests for Nov 1 (no watch sleep) and Nov 2 (watch sleep present)
- [ ] 1.2 `inferred_sleep_block` emission and supersession in `chronicler.situation_inferred`, derived before every lower-precedence span
- [ ] 1.3 Awake time (day minus sleep, with the median and 22:00–06:00 fallbacks) replaces the fixed window in untracked totals and in the gap interview's block choice

## 2. Exercise effort (bu-r3fmja.6)

- [ ] 2.1 Google Health connector records `detection = active` for `ACTIVELY_MEASURED` (REQ-connector-google-health-017)
- [ ] 2.2 `workout_episode` projected as evidence with `payload.effort`; a migration re-layers history
- [ ] 2.3 `exercise_block` emission for effortful workouts outside trips; lane taxonomy and `aggregations.py` mappings updated

## 3. Steam timed play and Spotify (bu-r3fmja.7)

- [ ] 3.1 Core Alembic migration: `connectors.steam_status_history`, with a SELECT grant to `butler_chronicler_rw`
- [ ] 3.2 Steam connector inserts status changes (REQ-connector-steam-001)
- [ ] 3.3 `steam.status_history` adapter projects `play_session` evidence; contracts, flags and the schedule are registered
- [ ] 3.4 `play_block` emission for the home or unknown-location part of each session; the daily total steps aside on dates with sessions
- [ ] 3.5 Spotify listening episodes become evidence (adapter and history migration); the `music → play` mapping is removed

## 4. Trips (bu-r3fmja.8)

- [ ] 4.1 Trip-span derivation from fixes (departure fix, 72 h silence rule)
- [ ] 4.2 `trip_block` emission ahead of exercise, place and play blocks; `trip_block` mapped to Travel
- [ ] 4.3 Gap interview skips trip days

## 5. Home is never Work (bu-r3fmja.9)

- [ ] 5.1 Occupation, focus and screen Work rows drop the parts that overlap the owner at home; tests on an at-home `occupation_block`
