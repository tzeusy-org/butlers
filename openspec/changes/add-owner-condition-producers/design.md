## Context

`public.owner_conditions` reconciles through `butlers.core.owner_conditions.reconcile_snapshot`
(a facade over `butlers.core.condition_ledger`). A producer hands it a source string, a list of
`Observation(fingerprint, summary, metadata)`, `snapshot_complete`, and `initial_grace_seconds`.
`snapshot_complete=True` resolves every active episode of that source that is not observed;
`snapshot_complete=False` only confirms what it saw. Fingerprints come from
`compute_fingerprint(source, version, identity_facts)`; evidence (summary, metadata) never
enters identity. Producers on main today are Finance `insight-scan` (`finance:bill-overdue`,
`finance:spending-anomaly`; `roster/finance/jobs/finance_jobs.py`, `_reconcile_owner_conditions`:
in-process, complete snapshot, failures logged and swallowed, the model for this change), Health
`baseline_watch` (`health:baseline-deviation`, `src/butlers/jobs/health_baselines.py`; governed
by the open change `add-personal-baselines`), Relationship commitments (`relationship:commitment`
via `src/butlers/core/commitments.py`, `snapshot_complete=False`), and the Switchboard
`reconcile_owner_condition` broker for sessions
(`roster/switchboard/modules/owner_conditions_broker.py`). This change adds the calendar, Home
Assistant and finance-threshold producers. It adds no Health producer: that one exists, with
its own fingerprint, direction vocabulary and failure semantics, and is not restated here.

Everything below is grounded in code on main 7cfd3b301. Where a seam could not be verified it is
marked `[Unknown]` here and in the spec delta, each with a verify task in `tasks.md`.

## D1: Where the Calendar producer runs

The Calendar producer runs as a supervised loop in the dashboard-api process, modeled on
`calendar_sync_deadman` (`src/butlers/jobs/calendar_sync_deadman.py`, started from
`src/butlers/api/app.py`), not as a butler `dispatch_mode="job"` schedule.

- The conflict scan is cross-schema: `query_calendar_conflicts` takes a `DatabaseManager` and
  fans out over every calendar-enabled butler schema, then collapses duplicates with the
  workspace dedup rules. A butler job has a pool for its own schema plus `public` only, so it
  cannot see the other butlers' `calendar_event_instances`.
- There is no Calendar butler in `roster/`; Calendar is a module enabled on finance, health,
  general, lifestyle, messenger, travel and relationship.
- The deadman precedent already proves the pattern: cross-schema check in the API process,
  reconcile into a `public` ledger through the Switchboard pool, partial fan-out never resolves.
- The scan stays the single source of truth for "what is a conflict": the producer reuses
  `query_calendar_conflicts` unchanged, so the radar banner and the condition can never disagree
  about a candidate (provenance filter, all-day exclusion, RSVP/transparency filter, dedup).
- Adoption note, source prefix: `src/butlers/core/owner_conditions.py:36-45` documents
  `{origin_butler}:{category}`, and no `calendar` butler exists. `calendar:` is a non-butler
  producer prefix, justified because the loop belongs to no butler; nothing parses the prefix
  today, and a named alternative is `switchboard:calendar-overcommitment`. The loop also writes
  through the Switchboard pool, whereas the ledger MCP surface requirement describes "a
  deterministic scheduled job with its own database pool"; the adoption question is whether
  the owner accepts that deviation or prefers the alternative name.

## D2: Calendar lookahead, window, and config owner

- Lookahead N defaults to 7 days, bounded 1..90 (the scan endpoint rejects windows over 90 days).
- Window is `[local midnight today, local midnight today + N days)` in the owner timezone
  (`resolve_general_timezone`, `butlers.core.general_settings`). Starting at local midnight, not
  at `now`, keeps `overloaded_day` totals for today honest; a day that has fully passed leaves the
  window and its conditions resolve at the next complete scan.
- Config owner: process environment on the dashboard-api process, like
  `_CALENDAR_SYNC_DEADMAN_CHECK_INTERVAL_ENV` via `_resolve_positive_float_env`. Neither
  `butler.toml` nor `runtime_config` applies, since this is not a butler daemon;
  `docs/runtime/model-routing.md` covers model, session and concurrency fields only. The
  detector thresholds (`back_to_back_gap_minutes`, `overloaded_day_hours`) stay the scan's own
  defaults (15 min, 6.0 h) so the radar and the condition agree. `[Unknown]` Whether the owner
  wants these tunable from a settings surface: no such surface exists today.
- Cadence default: 1800 s, same as the deadman loop.
- Adoption note, supervision: `REQ-infrastructure-reliability-001` says the dashboard lifespan
  supervises exactly one named task for each current loop and lists them (`secrets_lifecycle`,
  `model_verify`, ..., `calendar_sync_deadman`, `external_deadman`, `restore_drill`;
  `openspec/specs/infrastructure-reliability/spec.md`). The RFC 0007 readiness `supervisors`
  projection consumes that loop health. A new calendar loop therefore requires amending that
  list at adoption; this draft deliberately adds no MODIFIED block.

## D3: Calendar qualifying issues, severity, identity

- Qualifying kinds: `overlap` and `overloaded_day`. `back_to_back` never qualifies: it is a
  comfort signal (severity `info` for 2 events), not overcommitment.
- Severity mapping: the ledger has no severity column, so severity is evidence, not identity.
  Only issues with `severity == "warning"` qualify; today both qualifying kinds always emit
  `warning`. The mapping is therefore an explicit filter, so a future `info` overlap would not
  open a condition without a spec change.
- Identity (version 1): `overlap` keyed on the radar's canonical pair id
  (`overlap_pair_id(entry_id_a, entry_id_b)`, order-independent UUID5, also the key proposals use
  via `calendar_event_proposals.source_event_id`); `overloaded_day` keyed on the owner-local
  date. One overlap pair is one condition, so a three-way pile-up is three conditions; accepted
  because it matches the radar's own issue list and proposal join.
  `[Unknown]` Whether `calendar_event_instances.id` stays stable across a provider resync of the
  same recurring instance. If it does not, a resync would resolve and reopen pair conditions;
  verify before adoption (task 1.1). Related seam: `load_dedup_rules` and
  `load_keep_separate_keys` fail open to the default strategy while `available` stays true, so a
  dedup-store outage can change which copy survives a cluster and therefore which `instance_id`
  feeds `overlap_pair_id`; a "complete" scan could then resolve and reopen pair conditions.
  Task 1.1 covers both.
- Grace: `initial_grace_seconds` defaults to 3600, mirroring Finance.

## D4: Resolution, concurrency, partial sync

- Auto-resolution: a complete scan that no longer observes an issue resolves it (event moved,
  cancelled, declined, made transparent, or the day left the window).
- Explicit owner resolution through `resolve_owner_condition` is unchanged. By the ledger's
  existing recurrence rule a still-observed condition opens the next episode on the following
  complete scan. This draft does not suppress that: ledger semantics are a non-goal. Adoption
  question for the owner: whether an owner "resolve" on an unchanged calendar should hold (it
  would need a ledger change and is out of scope here).
- A scan is complete only when `CalendarConflictScan.available` is true. `available` is false on
  a total fan-out failure and on a partial per-butler failure, so a partial scan reconciles with
  `snapshot_complete=False` and never resolves by omission.
- Overlapping scans (two API replicas, or a tick overlapping a restart) are safe: the engine
  takes a transaction-scoped advisory lock per source and fingerprints are deterministic, so two
  scans of the same state produce confirmations, not duplicates.
- `[Unknown]` Whether a provider source whose sync stopped but whose rows are still present can
  hide a cancellation; `calendar_sync_deadman` covers sync death independently. Verify whether the
  producer should additionally require fresh `source_freshness` before a complete scan (task 1.2).

## D5: Threshold crossings are job-time, not write-time

The bead says "ingestion-time"; today's seams are scheduled deterministic jobs that read stored
state, not write-path hooks. The two threshold producers (Home and Finance) therefore run inside existing jobs
and detect a crossing on the next run (latency = job cadence). A write-time hook (Home Assistant
entity upsert at `roster/home/modules/__init__.py`, transaction ingestion) is not specified: it would add a second writer per source and a lock-ordering surface.
[decision] Adoption question: accept cadence latency (4 h home, daily finance) or
fund write-time hooks as a separate change.

## D6: Home

- Host job: `device_health_check` (`src/butlers/jobs/home.py`, `run_device_health_check`, every
  4 h per `roster/home/butler.toml`). It already classifies battery and offline issues from
  `ha_entity_snapshot` with thresholds from the state store (`home:thresholds:battery`, loaded by
  `_load_battery_thresholds`).
- Source `home:device-threshold`; identity `{entity_id, issue}` where `issue` is `battery` or
  `offline`, so one device can hold both. Offline issues are keyed without a level: the
  `critical` or `warning` level is evidence, so an offline device escalating from warning to
  critical confirms the same episode.
- Qualifying severities: `critical` and `warning` only, for both issue kinds. `classify_battery`
  also returns `info` (default threshold 30%, `home.py:611-617`) and the job appends `info`
  battery issues to its list, but an `info` battery does not qualify. A battery recovering from
  15% to 25% therefore stops being observed and resolves.
- Unmeasured battery: when a battery entity's state is `unavailable`, `unknown` or otherwise
  not numeric, `float(state)` fails (`home.py:2165-2168`) and the job produces no battery issue.
  The producer SHALL NOT read that absence as recovery: it carries the entity's active battery
  condition forward with `confirm_only` and resolves it only on a numeric reading above the
  warning threshold. (The same entity may separately open an offline condition.)
- Empty snapshot: the job returns early on an empty `ha_entity_snapshot`
  (`no_entity_snapshot`, `home.py:2129-2136`). The producer does not reconcile there, since an
  empty complete snapshot would resolve every condition.
- Degraded: `_require_ha_source_healthy` raising `HASourceUnmeasurableError` already skips the
  job today; the producer reconciles nothing in that case (no snapshot at all), so an HA outage
  never resolves a condition.
- Hysteresis and debounce: none beyond the job's own cadence and `select_due_issues`
  throttling. A battery that recovers above the warning threshold resolves; flapping reopens the
  next episode and the ledger keeps the history. `[Unknown]` Whether device battery sensors report
  a stale `last_updated` that should count as unmeasured rather than low; verify against
  `is_steady_state_unknown` and the snapshot's `last_updated` (task 2.1).
- Privacy: the level exists only for battery (the percentage); an offline issue's value is the
  state string, and hours offline are not kept.

## D7: Health (removed)

Dropped on review. Health's baseline-deviation producer already exists
(`CONDITION_SOURCE = "health:baseline-deviation"`, reconcile in `run_baseline_watch`), and the
open change `add-personal-baselines` governs it. The section number is left vacant so D8 and the
requirement IDs stay stable.

## D8: Finance

- Host job: `insight-scan`, the existing producer's job, extended with a third source.
- Source `finance:budget-threshold`; identity `{category, period, period_start}` from
  `budget_status()` items. Severity (`warning` or `exceeded`) is evidence, not identity, so a
  warning that crosses into exceeded confirms and updates the same episode instead of opening a
  second one (the insight candidate dedup key folds severity in; the ledger deliberately does
  not).
- Config owner: the owner-configured budgets table; `budget_status` already applies each
  budget's own warn and alert thresholds. No new setting.
- Running totals read the same transaction set as the existing alert (`deleted_at` guard
  included), so a late or backdated transaction moves the total on the next scan; no separate
  out-of-order handling.
- Degraded: `budget_status()` has no degraded return shape (`roster/finance/tools/budgets.py`):
  it raises, returns items, or returns `{"items": [], "count": 0}` when no budgets are active.
  A raise skips this source's reconciliation (or passes `snapshot_complete=False`) and never
  resolves; an empty result is a genuine complete snapshot and resolves, since the budgets were
  removed or deactivated. Today `budget_status()` is not wrapped in `run_insight_scan`, so a
  raise aborts the rest of the scan; the skip-this-source rule needs that wrapper.
- Placement: `run_insight_scan` returns early in sections 1 and 2 and on a `filtered` candidate
  submission in section 3. A reconcile placed after the budget section is skipped on those runs,
  which is safe (nothing resolves) but adds latency. Reconciling from `budget_result` before the
  candidate loop would avoid it; [decision] the early-exit latency is accepted and the producer
  keeps the simpler placement after the budget section.
- Privacy: category, period and utilisation percent plus spent and budget amounts, matching what
  the existing finance conditions and the budget insight already show the owner.
