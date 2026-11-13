## Context

Baseline: main `53832dfda9` and the departure card (#5789/#5790, bu-jv1moa.8).

What exists:
- The leak, smoke, CO and gas sentinel (`src/butlers/jobs/home_safety.py`, source `home:safety`), with .18's `last_changed` debounce, its faster ladder and `notified_at` preservation.
- The owner condition ledger (`butlers.core.owner_conditions`).
- The HA source-health lease (five minutes, evaluated by PostgreSQL).
- The `home.sensing` situation kind, registered in `scripts/init-db.sql` with writer role `perception_sensing_writer`. Nothing writes it.
- The `owner.place` slot and timeline, written by the presence reducer (.17). The departure card reads them through `BUTLERS_PERCEPTION_READER_DSN`. Neither the reducer schedule nor the reader login is activated.
- `departure_watch`, which declares what to check at departure (owner label plus secure states).
- Privileged perception source custody: `perception_write` refuses any subject not registered in `perception_private.sources` (`scripts/init-db.sql`, the `health.dependencies` profile block is the precedent).

Not used: the HA transition record behind `ha_get_recorded_history`. It has no freshness history, and a steady reading writes no transition, so a dwell cannot be computed from it.

Goals:
- Make the three unshipped promises exact.
- Reuse every store above.
- Never let blindness, a missing threshold or uncertain presence read as all clear.

Non-goals:
- Any Home Assistant service call, prepared action or model.
- A second presence inference.
- Mode-based (`house_unattended`, `hosting`) behaviour.
- House-sitter messaging.
- Changing the leak/smoke/CO/gas sentinel or the departure card.

## Coverage

| Promise | Producer (new, deterministic) | Reader(s) consumed | Condition owner | Declaration |
|---|---|---|---|---|
| Home sensing blind while away | Home `home.sensing` projection writer, plus `home:sensing-blind` in the away-safety job | HA source-health lease; `ha_entity_snapshot` readability of declared entities, grouped by role; per-role blind streaks; `owner.place` slot and timeline (owner reader) | `public.owner_conditions`, source `home:sensing-blind`, fingerprint `(sensing_group, leave_edge_id)` | the one installation sensing group (privileged registration, P2); `away_safety_settings` singleton: `blind_after_minutes` |
| Freezer temperature | away-safety job, freezer branch | HA lease; `ha_entity_snapshot` current reading and `last_updated`; the freezer's qualifying streak | source `home:freezer`, fingerprint `(entity_id)` | new `temperature_watch` rows (role `freezer`) |
| Door left open while away | away-safety job, away-door branch | HA lease; `ha_entity_snapshot` state and `last_changed`; `owner.place` slot and timeline | source `home:away-door`, fingerprint `(entity_id, leave_edge_id)` | existing `departure_watch` rows, plus a nullable `away_alert_after_minutes` column (NULL means not armed) |
| One-shot card at departure (exists, .8) | `home_departure_watch` | unchanged | `home.departure_cards` evidence, timed insight lane | `departure_watch` |

How the two departure-related producers share the work:
- .8 says once, at the departure, what was not secure.
- `home:away-door` is a ledger condition for an item that stays not secure for the owner's duration into the absence, with re-sends until it is secured or the owner returns.
- Both read one declaration table, so a door is declared once. The away producer queues no departure card. The departure job opens no condition.

`home.sensing` is the RFC 0038 projection ("Home deterministic health projection"). The away-safety job computes it once per run, writes the slot as `perception_sensing_writer`, and uses the same in-process value for the blind check. Other consumers (situation reads, `/now`) read the slot. No second health table exists.

## Configuration

Thresholds are owner-confirmed values. There are no defaults; a missing value means `unconfigured`. The dashboard may show an example, but it never stores one. The bounds below are validation bounds only, not suggestions.

| Value | Where | Bounds | Unconfigured when |
|---|---|---|---|
| freezer `max_c` | `temperature_watch` | -30.0 to 0.0 °C | row absent |
| freezer `hysteresis_c` | `temperature_watch` | 0.5 to 10.0 °C | row absent |
| freezer `dwell_minutes` | `temperature_watch` | 5 to 240 | row absent |
| freezer `max_reading_age_minutes` | `temperature_watch` | 10 to 360 | row absent |
| freezer `unit` | `temperature_watch` | `°C` or `°F`; bounds converted | row absent |
| `away_alert_after_minutes` | `departure_watch` column | 30 to 1440 | NULL (item not armed) |
| `blind_after_minutes` | `away_safety_settings` | 5 to 1440 | row absent |
| sensing group | `home.sensing` perception profile | one fixed group per installation, registered by a privileged act (P2) | profile has no subject: `home.sensing` is `unconfigured` |

The `away_alert_after_minutes` floor is 30 minutes because that is the departure card's whole delivery window (`DEPARTURE_WINDOW` in `src/butlers/jobs/home_departure.py`, confirmed leave + 2 to + 30 minutes). A door listed on the card therefore never also opens an away-door condition while the card may still be delivered.

The sensing group is not owner data. `perception_write` refuses a subject that `perception_private.sources` does not list, so a group a dashboard route generated could never be written. The group's subject, `sensing_group` source reference and `max_age_seconds` are registered once, by the privileged act in P2, as the `health.dependencies` profile is. The owner's routes edit only `blind_after_minutes` and which declarations the group covers; nothing in Home creates or re-registers the group.

Declaration rules:
- `temperature_watch` accepts only `sensor.*` entities, at most 8 rows, with an owner label of 1 to 80 characters.
- All writes go through owner-authenticated `PUT`/`DELETE` routes under `/api/home/`, with the safety-watch audit pattern: the generic audit middleware never reads bodies, and refusals are content-blind.
- No MCP tool reads or writes them.
- The `home:thresholds:*` "use default if no stored threshold" rule does not apply to these values.

## Semantics

Freshness and unknown:
- A run that cannot establish the HA lease is `source_unmeasurable`. It opens nothing and resolves nothing by omission.
- An entity reading `unavailable`, `unknown` or missing is unknown for that entity.
- A freezer snapshot whose `last_updated` is older than `max_reading_age_minutes`, or a reading that is non-numeric or in a unit other than the declared one, is unknown.
- Unknown is carried forward through `confirm_only`, exactly as the sentinel does.

Streaks (freezer dwell and per-role blindness):
- Dwell is judged from consecutive runs, not from a history store.
- The owner condition ledger has no pre-open state, so Home keeps each streak's start in its own table (`home.away_safety_streaks`, keyed by kind and entity or role). When a condition opens, the streak start is copied into its metadata as `detected_since`.
- A run that does not qualify deletes the streak. The next qualifying run starts a new one.
- The dwell is met when `now - streak_start >= dwell`.

Blind:
- `home.sensing` (the slot) is computed per run:
  - lease not healthy: state `unmeasurable`;
  - healthy and every declared entity unreadable: `known/unavailable`;
  - some unreadable: `known/degraded`;
  - all readable: `known/available`;
  - group profile not registered, or no declared entity: `unconfigured`.
- Blindness is per role, not per slot state. Roles: each declared `safety_watch` role (`leak`, `smoke`, `co`, `gas`), `freezer`, and `away_door` (armed `departure_watch` items).
- A role is blind in a run when the lease is not healthy, or when every declared entity of that role is unreadable. A partial loss within a role is not blind: the slot is `degraded` and each unreadable entity stays unknown in its own producer.
- Why per role: `unknown` is `confirm_only`, so a sensor that dies before anything opened sends nothing from its own producer. Without per-role blindness, losing every leak and smoke sensor while the freezer still reads would leave the slot `degraded`, open no blind condition, and the away owner would hear nothing.
- The blind dwell is the owner's `blind_after_minutes`. There is no code constant. Each role keeps its own streak.
- The blind condition needs the away premise and at least one role blind for `blind_after_minutes`. It names every role currently past the dwell, by role, never by entity label. While it stays open, a change in that set updates the roles it names; it does not open a second condition.
- It resolves `sight_restored` after one complete run with no blind role.
- Blind runs never resolve by omission. Blindness is reported as "cannot see", never as clear.

Away premise:
- Away requires all of:
  - `owner.place` known, inside its `fresh_until`, and `outside`;
  - the latest non-retracted confirmed transition is a leave that is not late;
  - the slot and that transition agree.
- Anything else is `presence_unknown`: opens nothing, resolves nothing except through an item's own secure reading, and re-sends say "away status unknown".
- Owner return is the latest non-retracted, non-late confirmed enter after the leave edge. It resolves the away conditions as `owner_returned`. A retraction of the leave resolves them as `premise_retracted`. Neither sends a message.
- Every away-door or blind send re-reads the premise immediately before sending. If the owner is no longer confirmed away, nothing is sent and the next reconcile applies the rules above.

Dwell and hysteresis:
- Away door: not secure (the `departure_watch` secure-state rule) continuously since `max(last_changed, leave.confirmed_at)` for `away_alert_after_minutes`.
- Freezer:
  - A run qualifies when the lease is healthy, `last_updated` is within `max_reading_age_minutes`, the unit matches, and the reading is above `max_c`.
  - Open: `now - streak_start >= dwell_minutes`.
  - A known reading at or below `max_c` ends the streak. An unknown run ends it too, and carries an open condition forward.
  - Resolve: a known, fresh reading at or below `max_c - hysteresis_c`.

Event identity:
- `home:freezer` is per entity. A recurrence after resolution is a new episode under the ledger's recurrence rule.
- `home:away-door` and `home:sensing-blind` are per absence: the fingerprint includes the leave edge id, so a new absence is a new condition.

Repeat and escalation:
- Opt-in ladder for these three sources only, as the ledger runs it today (no engine change):
  - the ladder is relative to the opening: the ledger sets `next_reescalate_at` when the condition opens and advances rungs whether or not a send was delivered;
  - rungs at 1 hour after opening, then 4 hours later, then every 24 hours.
- Other sources keep their own grace and the shared 1/3/7-day advance.
- The ladder is code-set; it is not an owner threshold.
- `notified_at` is preserved across concurrent confirmations, as in .18.

Quiet hours:
- Delivery is the routine `_send_notify` path. A suppressed send records `suppressed` and leaves `notified_at` unset.
- The first send is held: the retry rule (an undelivered condition older than 2 minutes) sends it on the first run after the suppression lifts. Never lost.
- An escalation rung that falls inside quiet hours is skipped, not queued. The ledger has already advanced, so the next re-send is the next rung after the window. This is the honest cost of routine delivery; P1 is where a bypass would change it.
- Nothing here breaks quiet hours until P1 below is answered.

Retraction and removal:
- Removing a declaration, or clearing its duration, resolves as `watch_removed` on the next complete healthy run.
- Editing a `departure_watch` row while away keeps no arming snapshot: the next run re-evaluates the item against the edited row. A condition the edit no longer supports resolves as `watch_removed`; a newly supported one opens under the same absence fingerprint.
- Turning the feature flag off stops runs and leaves open conditions as they are. They are not resolved, because the facts did not change.

Message text:
- Messages use the owner's label, a fixed phrase and numbers only. They never include Home Assistant's `friendly_name` or other feed text.
- Freezer: `Freezer <label>: <t> °C, above <max> °C for <n> min`.
- Away door: `<label> still <state> <n> min into your absence`.
- Blind: `Home cannot see <roles> since <hh:mm>; those alerts cannot reach you`, where `<roles>` lists only the blind declared roles in fixed words (leak, smoke, CO, gas, freezer, doors). A role with no declared entity is never named, so the message never claims coverage the owner did not set up.

## Response profile `away-safety-v1` (REQ-event-response-contract-002)

| Field | Value |
|---|---|
| Source population | declared `temperature_watch`, armed `departure_watch` and `safety_watch` rows (the last for per-role blindness only); the HA lease; the `owner.place` slot and timeline |
| Timestamp trust | Home-module-persisted `last_changed`/`last_updated`; PostgreSQL-evaluated lease; perception `confirmed_at`/`fresh_until` |
| Maximum observation age | lease 5 min; freezer `max_reading_age_minutes`; presence `fresh_until` |
| Polling | `home_away_safety` job, `dispatch_mode = "job"`, every 2 minutes; no websocket hook in v1 |
| Fallback | unknown is carried forward (above); never resolved by omission |
| Ordering and gaps | consecutive-run streaks; any non-qualifying run ends the streak, so a gap can delay an open but never fabricate one |
| Dwell | owner values above |
| Response deadline | opened within dwell + 4 min (two job periods) of the first qualifying run under a healthy source. A later open records `late_detection` with its lag (`opened_at - detected_since - dwell`) in the condition metadata |
| Inference | none (REQ-event-response-contract-001, 004 not applicable) |
| Measurement | condition metadata keeps `detected_since`, `opened_at`, `notified_at` and the suppression or failure reason, separately |

## Scenario matrix

Every row's outcome is owner-visible: as a message, or as the condition and producer status on the dashboard. No row calls a Home Assistant service.

| Case | Freezer | Away door | Sensing blind |
|---|---|---|---|
| Healthy, no hazard | nothing opens; an open condition resolves on hysteresis | nothing opens; an open condition resolves `secured` | slot `available`; nothing opens |
| Confirmed hazard | opens after dwell, routine send | opens after duration while away, routine send | opens after blind dwell while away, routine send |
| Stale or unavailable feed (lease expired, entity unavailable, unit mismatch, old reading) | unknown: no open, no resolve, streak ends; an open one re-sends "sensor not reporting" | unknown: same | this *is* the blind input: lease expired makes every role blind; a whole role unreadable makes that role blind (slot may be `degraded`); opens if away, naming the roles |
| Partial loss within one role | unknown for that entity | unknown for that entity | not blind: slot `degraded`, nothing opens |
| Unconfigured | status `unconfigured`, silent | item not armed, silent | status `unconfigured`; slot `unconfigured` |
| Presence unknown or conflicting | not presence-dependent | `presence_unknown`: no open; open carried forward with "away status unknown" | same as away door |
| Late leave | n/a | no premise | no premise |
| Retracted leave | n/a | resolves `premise_retracted`, no message | resolves `premise_retracted`, no message |
| Owner returns | n/a | resolves `owner_returned`, no message | resolves `owner_returned`, no message |
| Owner returns between premise read and send | n/a | premise re-read before send: nothing sent; resolves `owner_returned` | same |
| Item open at departure | n/a | the departure card is the first message; the condition opens no earlier than 30 min after the leave confirmation (duration floor) | n/a |
| Flapping (around the limit, or a door opened and closed) | hysteresis plus streak: a dip ends the streak and nothing opens; an open one stays open until below the hysteresis | a door closed before the duration opens nothing; reopening restarts from `last_changed` | a role that reads again ends its streak |
| Delivery fails | stays open without `notified_at`; retried after 2 min | same | same |
| Quiet-hours suppression | first send held by the retry and sent after the window; an escalation rung inside the window is skipped, and the next re-send is the next rung | same | same |
| Crash between open and send | next run: undelivered and older than 2 min, so it sends once | same | same |
| Concurrent runs, or a run concurrent with the departure job | runs serialize on a transaction advisory lock; a second run returns `busy`; the ledger fingerprint keeps one episode | same; the departure job writes only `departure_cards` | same |
| Declaration removed | resolves `watch_removed` on a complete healthy run | same | same (settings removed: blind `unconfigured`, resolves) |

## Owner prerequisites

- **P1 (pending, owner decision): may any of these three producers break quiet hours or sleep?**
  - bu-u33sb7 q3 = A lets only safety sentinels, the owner's break-through rules and owner-flagged obligations do so.
  - bu-dhloi1.3 implements only `home_safety` (leak/smoke/CO/gas) as a code-set producer cause.
  - Options:
    - A: never;
    - B: a per-declaration owner flag set at creation, implemented as a new code-set cause beside `home_safety`;
    - C: classify all three as safety sentinels.
  - Agent recommendation: B, default off.
  - Until it is answered and implemented, REQ-home-deterministic-jobs-007 holds: routine only.
- **P2 (activation acts, owner-authorized, not in the implementation PR):**
  - presence reducer schedule and `home.dots` profile (.17);
  - perception owner reader login for Home (shared with .8);
  - a `perception_sensing_writer` login for Home;
  - registering the one installation sensing group in the `home.sensing` profile (subject, `sensing_group` source reference and `max_age_seconds` above the 2-minute job period), as a privileged act like the `health.dependencies` profile block in `scripts/init-db.sql`;
  - the `home_away_safety` schedule;
  - the feature flag.
- **Deferred (not asked now):** whether `hosting` or a declared house-sitter should hold away-door and blind conditions. In v1 the owner being away is enough. Mode-aware holds belong to .21.

## Implementation packet

One cohesive PR behind `HOME_AWAY_SAFETY_LIVE = False`:

1. Migrations:
   - `home_007`: `temperature_watch`, `away_safety_settings` (`blind_after_minutes` only), `away_safety_streaks`, and `departure_watch.away_alert_after_minutes` with its CHECK (30 to 1440);
   - downgrade drops them;
   - next free home revision after the departure card's `home_006`.
2. Producer: `src/butlers/jobs/home_away_safety.py`.
   - One job, `home_away_safety`, registered and unscheduled.
   - The pure classifiers (sensing state, per-role blindness, freezer run qualification, away premise, away door) are separate from the I/O.
   - The `home.sensing` writer, for the registered group only.
   - The ledger reconcile per source, with `confirm_only` for unknown and the opt-in ladder.
   - Routine delivery with the retry rule. `_send_notify` must return its outcome (sent, suppressed or failed) so the producer can set `notified_at` only on a delivered send; today it does not.
   - An away-premise re-read immediately before each away-door or blind send.
3. Presence read:
   - Extend the departure card's reader with a "latest confirmed `owner.place` transition" query and a slot read.
   - No second reader or DSN.
4. API and dashboard: owner-only routes and Devices panels for freezers, the per-item away duration and blind settings, with an `unconfigured` status display. The blind panel shows the group as unconfigured until the P2 registration exists and offers no way to create it.
5. Docs: `docs/butlers/home.md`, `roster/home/AGENTS.md` (no tool, no skill, no session control), and this change's spec merged.

### Test targets

Extend the existing owners first; add a new file only where none exists.

| Target | Kind | Covers |
|---|---|---|
| `tests/jobs/test_home_away_safety.py` (new, mirrors `test_home_safety_sentinel.py`) | pure + real PG | every scenario-matrix row; per-role blindness (leak and smoke dark while the freezer reads opens one condition naming both); streak ends on unknown and on a cold reading; premise re-read before send; `late_detection` recorded and validated against the deadline; no HA POST (the `no_ha_posts` fixture from the departure tests); model-free |
| `tests/jobs/test_home_safety_escalation_ladder.py` | extend, real PG | the new opt-in ladder, open-relative; a rung inside quiet hours is skipped, not queued; `home:safety` and the other sources unchanged |
| `tests/integration/test_condition_ledger_opt_ins_roundtrip.py` | extend, real PG | `notified_at` preservation for the three new sources |
| `tests/jobs/test_home_safety_last_changed.py` | extend | away-door duration judged from `last_changed`, not `last_updated` |
| `tests/jobs/test_home_departure_card.py` | extend, real PG | the departure job ignores `away_alert_after_minutes` and the away producer queues no card |
| `tests/perception/test_perception_postgres.py` | extend, real PG | `home.sensing` written by `perception_sensing_writer` for the registered group, refused for an unregistered subject and for other roles; `unmeasurable` vs `unavailable` |
| `tests/api/test_home_away_safety_config.py` (new, mirrors `test_home_safety_watch.py`) | API | owner-only, bounds (away duration below 30 refused), content-blind refusal, `watch_removed`, no route creates a sensing group |
| `tests/migrations/test_home_away_safety_migrations.py` (new) | unit + real PG | CHECKs, column, downgrade |

Expected delta for the implementation PR: about `Tests: +52 ~5 -0` (the `~5` includes the `_send_notify` callers whose expectations change with its return value). About two thirds are real-PG and run CI-first where Docker is absent. The .18 survivors (`test_home_safety_last_changed.py`, `test_home_safety_escalation_ladder.py`, the opt-ins roundtrip) are extended, not duplicated.
