## 1. Sleep and awake time (bu-r3fmja.5)

- [ ] 1.1 Pure sleep-window and quiet-run detection in `butlers.chronicler.situation`, with tests for Nov 1 (no watch sleep) and Nov 2 (watch sleep present)
- [ ] 1.2 `inferred_sleep_block` emission and supersession in `chronicler.situation_inferred`
- [ ] 1.3 Awake time (day minus sleep) replaces the fixed window in untracked totals and in the gap interview's block selection

## 2. Exercise effort (bu-r3fmja.6)

- [ ] 2.1 Effort rule in the `google_health.measurements` workout projection
- [ ] 2.2 Migration re-layering existing auto-detected slow walks; tests on the 1.7 km / 88 min case

## 3. Steam timed play (bu-r3fmja.7)

- [ ] 3.1 Core Alembic migration: `connectors.steam_status_history`, with a SELECT grant to `butler_chronicler_rw`
- [ ] 3.2 Steam connector inserts status changes (REQ-connector-steam-001)
- [ ] 3.3 `steam.status_history` adapter projects `play_session` evidence; contracts, flags and the schedule are registered
- [ ] 3.4 `chronicler.situation_inferred` emits `play_block` for the home or unknown-location part of each session; the daily total steps aside on dates with sessions

## 4. Trips (bu-r3fmja.8)

- [ ] 4.1 Trip-span derivation from fixes, plus the travel-schema calendar extension
- [ ] 4.2 `trip_block` emission; the "no block beyond 50 km" rule of `chronicler-situational-inference` is retired
- [ ] 4.3 Gap interview skips trip days

## 5. Archive order

- [ ] 5.1 Archive `chronicler-situational-inference` before this change. Then drop its scenario clause "a stay more than 50 km from home SHALL produce no situational block" (REQ-butler-chronicler-016), which REQ-butler-chronicler-021 replaces.
