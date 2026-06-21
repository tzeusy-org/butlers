## 1. Spec gate sign-off

- [ ] 1.1 Confirm the owner approves the contract: the `calendar/overlay/<date>` envelope shape, the four-specialist contributing set (finance, travel, relationship, health), and the `calendar.v_overlay_contributions` UNION view mirroring `general.v_briefing_contributions` (core_063).
- [ ] 1.2 Confirm the no-LLM structured variant (option A) is the v1 target: no `summary` field in the v1 envelope; no LLM in the overlay/prep-rail/briefing read path; the batched pre-rendered narrative layer is deferred (`bu-jdrkbj`, P4).
- [ ] 1.3 Confirm REUSE-not-rebuild: contribution jobs register in the existing `_DETERMINISTIC_SCHEDULE_JOB_REGISTRY`; no parallel scheduler/dispatch/view mechanism is introduced.
- [ ] 1.4 Run `openspec validate calendar-cross-domain-overlays --strict` green and land this gate before the foundation split (`bu-xcd1cp`) and downstream render/prep-rail/briefing beads begin.

## 2. View + grants migration (split unit: view)

- [ ] 2.1 Add a core Alembic migration (next in chain after `core_136`) creating `calendar.v_overlay_contributions` as a UNION ALL view over `finance.state`, `travel.state`, `relationship.state`, and `health.state`, each filtered to `key LIKE 'calendar/overlay/%'` and annotated with a hardcoded `butler` string literal — mirroring `core_063_v_briefing_contributions.py`.
- [ ] 2.2 Ensure the calendar reader role (`butler_calendar_rw`) exists best-effort (matching `_ensure_role_exists` in `core_063`) and grant SELECT on each contributing specialist's `state` table to that role.
- [ ] 2.3 Reuse the `core_063` optional-schema guard: `_state_table_exists` via `to_regclass`; emit a NULL-returning stub UNION term (`SELECT NULL::text AS butler, NULL::text AS key, NULL::jsonb AS value WHERE FALSE`) for any specialist whose `state` table is absent at migration time.
- [ ] 2.4 `downgrade()` drops the view and revokes the SELECT grants (reversible, auditable).
- [ ] 2.5 Migration test: view created in `calendar` schema; upgrade/downgrade round-trips; UNION view is not updatable (INSERT/UPDATE/DELETE fails); empty-when-none (zero rows before any job runs).

## 3. Per-butler contribution jobs (split units: job-finance / job-travel / job-relationship / job-health)

- [ ] 3.1 Implement `calendar_overlay_contribution` deterministic job for **finance**: query bills (due in `[today, today+lookahead]`) and subscription renewals; write `bill_due` / `subscription_renewal` entries under `calendar/overlay/<date>`; prune entries older than the retention window.
- [ ] 3.2 Implement `calendar_overlay_contribution` for **travel**: query trips/flights for `departure` / `arrival` / `check_in` / `check_out` in the window; write per-date envelopes; prune.
- [ ] 3.3 Implement `calendar_overlay_contribution` for **relationship**: query `birthday` / `important_date` / `follow_up` in the window; write per-date envelopes; prune.
- [ ] 3.4 Implement `calendar_overlay_contribution` for **health**: query `appointment` / `medication_reminder` in the window; write per-date envelopes; prune.
- [ ] 3.5 Register `calendar_overlay_contribution` in the existing `_DETERMINISTIC_SCHEDULE_JOB_REGISTRY` (`src/butlers/scheduled_jobs.py`) under each of `finance`, `travel`, `relationship`, `health` — do NOT build a parallel registry.
- [ ] 3.6 Add a `calendar_overlay_contribution` schedule entry to each contributing specialist's `butler.toml` with `dispatch_mode="job"`, `job_name="calendar_overlay_contribution"`, and a fixed cron.
- [ ] 3.7 Unit tests per job: correctly-shaped envelopes; `has_entries=false` for an empty domain date; upsert (re-run overwrites); prune removes stale entries; zero LLM calls.

## 4. Workspace overlay projection (split unit: render)

- [ ] 4.1 Add `"overlay_contribution"` to the `UnifiedCalendarSourceType` literal in `src/butlers/api/models/calendar_workspace.py`.
- [ ] 4.2 Widen the `view` query parameter in `get_workspace` to accept `overlays`; read `calendar.v_overlay_contributions` for the requested `[start, end]` range, validate each envelope's `butler` against the view's source column, and project each entry into a `UnifiedCalendarEntry` (`source_type="overlay_contribution"`, `editable=false`, with `kind`, `priority`, `source_butler`, and the entry's `meta` in `metadata`).
- [ ] 4.3 Add `has_domain_context: bool` to the `view=overlays` response envelope (true only when the view was reachable AND at least one specialist contributed for the range).
- [ ] 4.4 Fail-open: absent view, missing specialist table, or query failure returns `entries: []` with `has_domain_context: false`, never HTTP 500.
- [ ] 4.5 Tests: `view=overlays` projects entries for a range; `has_domain_context` true when contributions exist; fail-open on missing view; `"overlay_contribution"` never appears in `view=user` / `view=butler`; no LLM is invoked.

## 5. Meeting-prep rail read (split unit: prep-rail)

- [ ] 5.1 Add the prep-rail read endpoint returning a selected event's prep context (attendees, relationship notes, last-met) sourced from precomputed contribution data — NOT a direct `SELECT ... FROM relationship.*` at request time and NOT a per-open LLM session.
- [ ] 5.2 Honest empty-state: when prep contributions for the event do not exist (coverage `bu-xgz7g.1` / `bu-mcz0o9` not yet built), return a structured empty payload, never HTTP 500.
- [ ] 5.3 Tests: prep payload is populated from contribution data; empty payload returned (not an error) when no contribution exists; no cross-butler live read and no LLM call in the path.

## 6. Day-briefing card read (split unit: briefing)

- [ ] 6.1 Add the day-briefing card read returning a structured "tomorrow at a glance" payload from the cached overlay view (grouped by butler/kind), with NO per-open LLM call.
- [ ] 6.2 Honest empty-state: `has_domain_context: false` ⇒ the card renders "No domain context for this day" rather than being silently omitted.
- [ ] 6.3 Tests: day-card payload assembled from the view; structured (not prose); honest empty-state; no LLM call in the path.

## 7. Spec validation + quality gate

- [ ] 7.1 Author the `calendar-overlay-aggregation` capability spec (this change's `specs/calendar-overlay-aggregation/spec.md`) and the `dashboard-api` read-surface delta (this change's `specs/dashboard-api/spec.md`).
- [ ] 7.2 Run `openspec validate calendar-cross-domain-overlays --strict` and fix until green.
- [ ] 7.3 Quality gate: `ruff check`/`format --check` on touched `.py` files; targeted contribution-job, migration, and workspace tests; full `pytest` (excluding e2e) before merge.
