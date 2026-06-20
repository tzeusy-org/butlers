# Tasks — Health Dashboard Overview Redesign

Ordered by the backend-epic dependency graph (brief §3.6): #1–#5 deliver a fully deterministic,
zero-LLM Overview; #6–#7 (LLM briefing + correlation) are a deferrable follow-on. No schema
migration anywhere.

## 1. Wire orphaned reads (zero backend change)

- [ ] 1.1 Consume `GET /api/health/measurements/trend` in the measurements trend rule-list (currently zero FE consumer)
- [ ] 1.2 Consume `GET /api/health/measurements/latest` for the Overview KPI strip (weight, blood_pressure, heart_rate, blood_sugar)
- [ ] 1.3 Consume `GET /api/health/measurements/sources` for data-freshness chips

## 2. Dashboard dose-logging route (new write)

- [ ] 2.1 Add `POST /api/health/medications/{id}/doses` writing a `took_dose` fact (no new table); add `DoseCreateRequest` model
- [ ] 2.2 Invalidate the per-owner briefing cache on dose write
- [ ] 2.3 Wire the dashboard dose-logging affordance into the Medications rule-list rows

## 3. Nutrition summary route (new read)

- [ ] 3.1 Add `GET /api/health/nutrition/summary?start=&end=` over existing meal facts; add response model
- [ ] 3.2 Consume it for the Meals page right-column daily totals

## 4. Frequency-expected adherence route (new read)

- [ ] 4.1 Lift `_frequency_to_doses_per_day` (`health_jobs.py:83`) into a shared helper used by both route and job
- [ ] 4.2 Add `GET /api/health/medications/{id}/adherence?window_days=` returning `{expected_doses, taken_doses, skipped_doses, adherence_rate}`
- [ ] 4.3 Replace the client-side naive taken/total ratio with the adherence endpoint in the Medications rows

## 5. Insight reader on the Switchboard (new read; powers the deterministic attention list)

- [ ] 5.1 Add `GET /api/switchboard/insights?butler=health&status=pending&limit=` on the Switchboard (runs under the broker role that already holds SELECT — no grant migration)
- [ ] 5.2 Filter by `origin_butler` and default `status=pending`; return `{id, category, priority, message, metadata, created_at, status, expires_at}`
- [ ] 5.3 Consume it for the Overview AttentionList (no auto-refresh; manual refresh only)

## 6. Health Overview page (new landing) — Dispatch composition

- [ ] 6.1 Add the `/health` index route in `router-config.tsx`; repoint nav from `/health/measurements` → `/health` (six sub-paths unchanged)
- [ ] 6.2 Build `pages/HealthOverviewPage.tsx` as a two-column editorial (`1.4fr / 1fr`); collapses to one column on narrow viewports
- [ ] 6.3 Compose DateEyebrow + Display headline + Voice briefing with BriefingStatus pill (left), KPI strip (4 cells), AttentionList (right), ButlerMark in `--category-4`
- [ ] 6.4 Empty attention index collapses to one serif-italic line (no decoration); absent KPI reads em-dash (no fake numbers)

## 7. Reframe the six sub-pages to Dispatch

- [ ] 7.1 Measurements: lead with trend rule-list; fix chart tabs to `weight, blood_pressure, heart_rate, blood_sugar, temperature, spo2, steps` (drop `glucose/sleep/oxygen`); bridge chart hex to `--category-4` via computed CSS var
- [ ] 7.2 Medications (replace): card grid + nested DoseLog → Dispatch rule-list; dose history → detail/expand; preserve Active/All filter + adherence semantics + create/edit Dialog
- [ ] 7.3 Conditions: Table/Card → rule-list (status-dot / condition+status / onset / →)
- [ ] 7.4 Symptoms: Table → rule-list with 6px severity glyph (`--severity-low/medium/high`); keep name + date filters
- [ ] 7.5 Meals: Card → day-grouped rule-list with right-column daily totals
- [ ] 7.6 Research: Table → rule-list with in-place expansion + Topics index
- [ ] 7.7 Remove shadcn `Card` shells from the six `*Page.tsx`; replace with `<Page archetype="list">` + Eyebrow/Display
- [ ] 7.8 Update/extend `pages/HealthPages.view-only.test.tsx` assertions (do not silently delete)

## 8. Auto-refresh carve-out (binds the universal 30s rule)

- [ ] 8.1 Keep `refetchInterval: 30_000` on deterministic hooks (measurements, medications, conditions, symptoms, meals, research, latest, trend, adherence, nutrition-summary)
- [ ] 8.2 Add `use-health-briefing` and the insight-feed hook WITHOUT `refetchInterval`; rely on 5-min TTL cache + manual refresh via the status pill

## 9. LLM Voice briefing route (deferrable; gated on #5)

- [ ] 9.1 Add `GET /api/health/briefing` mirroring `/api/dashboard/briefing`: templated-only default, LLM elaboration behind a cost flag, per-owner 5-min TTL cache, never raises
- [ ] 9.2 Extend `voice_lint_passes` with the non-diagnostic rules (diagnosis/advice blocklist, causation guard, no-celebration, no-future-tense, reference-range-not-verdict); on failure → templated fallback
- [ ] 9.3 Owner-only access (403 for non-owner); wire `use-health-briefing` to render the headline/elaboration + honesty pill

## 10. Cross-signal correlation in the insight-scan job (deferrable; gated on #5 + `home` MCP)

- [ ] 10.1 Change the `insight-scan` cron from `0 7 15 * *` (monthly) to `0 7 * * 1` (weekly) in `roster/health/butler.toml`
- [ ] 10.2 Extend `health_jobs.py` to fan out Home Assistant env reads via cross-butler MCP/Switchboard (never a direct `home.*` DB read)
- [ ] 10.3 Emit correlation candidates (HA env ↔ sleep/symptoms; adherence dip → flare; measurement drift) with co-occurrence-only framing that passes the voice-lint; submit via `propose_insight_candidate()`
- [ ] 10.4 Confirm correlation runs only in the scheduled job — never live-on-GET

## 11. Validation

- [ ] 11.1 `openspec validate health-dashboard-overview-redesign --strict` passes
- [ ] 11.2 Frontend gates green (`tsc`, `vitest`, `eslint .`); e2e for the new `/health` route
- [ ] 11.3 Backend `make check` green for the four new routes + the extended insight-scan job
