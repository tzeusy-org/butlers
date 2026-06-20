## 1. Spec gate sign-off (bu-1ajgg9.1)

- [ ] 1.1 Confirm the owner approves the design: per-day contribution envelope shape (`calendar/overlay/<date>`), the four-specialist contributing set (finance, travel, relationship, health), and the `calendar.v_overlay_contributions` UNION view contract.
- [ ] 1.2 Confirm the no-LLM structured variant is the v1 target: no `summary` field in the v1 envelope; `bu-jdrkbj` (P4) tracks the deferred narrative layer.
- [ ] 1.3 Close `bu-1ajgg9.1` with approval (which unblocks `bu-xcd1cp` and downstream overlay-render / prep-rail / briefing-card beads).

## 2. Core migration — `calendar.v_overlay_contributions`

- [ ] 2.1 Add a core Alembic migration (next in chain after `core_134`) creating `calendar.v_overlay_contributions` as a UNION ALL view over `finance.state`, `travel.state`, `relationship.state`, and `health.state`, each filtered to `key LIKE 'calendar/overlay/%'` and annotated with a hardcoded `butler` string literal.
- [ ] 2.2 In the same migration, ensure the calendar reader role (`butler_calendar_rw`) exists (best-effort `CREATE ROLE`, matching `_ensure_role_exists` in `core_063`) and grant SELECT on each contributing specialist's `state` table to that role.
- [ ] 2.3 Add stub UNION term (`SELECT NULL::text AS butler, NULL::text AS key, NULL::jsonb AS value WHERE FALSE`) for any specialist whose `state` table is absent at migration time (matches `core_063` pattern for unavailable schemas).
- [ ] 2.4 Downgrade drops the view and revokes the SELECT grants.
- [ ] 2.5 Unit/migration test: view created in calendar schema; upgrade/downgrade round-trips; UNION view is not updatable (INSERT fails).

## 3. Overlay contribution jobs — specialist butlers

- [ ] 3.1 Implement `calendar_overlay_contribution` deterministic job for the **finance** butler: query `bills` (due dates in `[today, today+30d]`), `subscriptions` (renewal dates in window), write envelope under `calendar/overlay/<date>` for each date with entries, prune entries older than 30 days.
- [ ] 3.2 Implement `calendar_overlay_contribution` for the **travel** butler: query trips/flights for departures, arrivals, check-ins, check-outs in `[today, today+30d]`; write per-date envelopes; prune.
- [ ] 3.3 Implement `calendar_overlay_contribution` for the **relationship** butler: query entity birthdays, follow-ups, and tagged important dates in `[today, today+30d]`; write per-date envelopes; prune.
- [ ] 3.4 Implement `calendar_overlay_contribution` for the **health** butler: query appointments and medication reminders in `[today, today+30d]`; write per-date envelopes; prune.
- [ ] 3.5 Register the job in `_DETERMINISTIC_SCHEDULE_JOB_REGISTRY` for each specialist butler.
- [ ] 3.6 Add `calendar_overlay_contribution` schedule entry to each specialist butler's `butler.toml` with `dispatch_mode="job"` and cron `50 6 * * *`.
- [ ] 3.7 Unit tests: each job writes correctly shaped envelopes; `has_entries=false` for empty domain; upsert (re-run overwrites); prune removes stale entries.

## 4. `view=overlays` workspace projection endpoint

- [ ] 4.1 Add `"overlay_contribution"` to the `UnifiedCalendarSourceType` literal in `src/butlers/api/models/calendar_workspace.py`.
- [ ] 4.2 Widen the `view` query parameter in `get_workspace` to accept `overlays`; implement the query that reads `calendar.v_overlay_contributions` for the requested date range, parses each contribution envelope, and projects each entry into a `UnifiedCalendarEntry` (tagged `source_type="overlay_contribution"`, `editable=false`, with `kind`, `priority`, `source_butler`, and kind-specific `meta` in `entry.metadata`).
- [ ] 4.3 Add `has_domain_context: bool` to the workspace response envelope for the `overlays` view (true if the view was reachable and at least one specialist contributed for the requested range; false otherwise).
- [ ] 4.4 Fail-open: absent view, missing specialist table, or query failure returns `entries: []` with `has_domain_context: false`, never HTTP 500.
- [ ] 4.5 Tests: `view=overlays` returns projected entries for a date range; `has_domain_context` is true when contributions exist; fail-open on missing view; `"overlay_contribution"` entries never appear in `view=user` or `view=butler`.

## 5. Briefing day-card read-model

- [ ] 5.1 Document (in the `calendar-cross-domain-overlays` capability spec) that the day-briefing card (`bu-1ajgg9` "tomorrow at a glance") consumes the `view=overlays` endpoint for its domain context section.
- [ ] 5.2 Confirm the honest empty-state contract: `has_domain_context: false` renders "No domain context for this day" in the FE; the card section is never silently omitted.

## 6. Spec + validation

- [ ] 6.1 Author the `calendar-cross-domain-overlays` capability spec (this change's `specs/calendar-cross-domain-overlays/spec.md`).
- [ ] 6.2 Author the `module-calendar` spec delta (this change's `specs/module-calendar/spec.md`) adding `view=overlays` and `"overlay_contribution"` to `UnifiedCalendarSourceType`.
- [ ] 6.3 Run `openspec validate calendar-cross-domain-overlays --strict` and fix until green.
- [ ] 6.4 Quality gate: `ruff check`/`format --check` on touched `.py` files; targeted contribution-job and workspace tests; full `pytest` (excluding e2e) before merge.
