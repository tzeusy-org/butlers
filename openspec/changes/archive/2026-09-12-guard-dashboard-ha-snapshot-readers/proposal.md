## Why

`add-ha-source-health-guard` (bu-8cdl1.12 Slice 1, merged in #3995) added the
`ha_source_health` table and guarded `_read_entity_snapshot` plus the three
`src/butlers/jobs/home.py` job entry points, but deliberately deferred four
other direct `ha_entity_snapshot` readers to keep that PR reviewable:
`roster/home/api/router.py`, `src/butlers/jobs/context_producers.py`,
`src/butlers/jobs/briefing.py`, and `src/butlers/jobs/health_ha_reader.py`.
Until every reader joins the guard, the trust-fix defect it exists to close
(an HA outage silently reading as a healthy house because `captured_at` is
re-stamped every snapshot cycle regardless of contact success) is only
partially closed.

This change (bu-8t4sc) guards the three sites that genuinely read the home
butler's live `ha_entity_snapshot`: the dashboard API, the `at_home` context
producer, and the home daily-briefing contribution job. It does not touch
`health_ha_reader.py`: that reader queries `ha_entity_snapshot` from the
health butler's own schema-scoped pool, where no such table exists (health
has no HA connector or `ha_source_health` analog of its own) — the query
always raises `UndefinedTableError`, is caught, and resolves to "no
environmental entities", which is not the same silently-healthy trust defect.
Building a health-schema HA source is a separate feature decision, out of
scope here, and is left as a discovered follow-up.

## What Changes

- `roster/home/api/router.py`: dashboard reads are graceful, not job-style
  fail-closed — an outage still returns the (possibly stale) snapshot rows
  so the page has something to show, with `ha_source_available=false` in the
  endpoint's existing envelope. A missing single entity or an empty bare area
  list returns 503 during the outage because those response shapes have no
  honest place to carry the degraded flag. The two energy endpoints also
  return 503 before cached sensor discovery; otherwise an empty cached sensor
  list could bypass the live HA call and still render as a truthful empty
  result. All paths reuse `_require_ha_source_healthy` rather than
  reimplementing source-health classification.
- `src/butlers/jobs/context_producers.py`: `run_home_presence_context_producer`
  now confirms `ha_source_health` before reading presence rows; an outage is
  treated the same as "no fresh presence data" (leaves `at_home` untouched
  rather than asserting or clearing it from untrustworthy rows).
- `src/butlers/jobs/briefing.py`: `run_home_briefing_contribution` now
  confirms `ha_source_health` before its device-alert and temperature-outlier
  scan; an outage adds a high-priority "Home Assistant is unmeasurable"
  highlight instead of silently reporting zero alerts, and the job's return
  dict carries a new `ha_source_unmeasurable` flag.
- OpenSpec deltas cover each changed behavior: dashboard snapshot readers in
  `home-dashboard-extensions`, the presence producer's no-write outage path in
  `context-bus`, and the Home contribution's explicit unmeasurable highlight
  in `cross-butler-briefing-contribution`.

## Impact

- Affected specs: `home-dashboard-extensions`, `context-bus`, and
  `cross-butler-briefing-contribution`.
- Affected code: `roster/home/api/router.py`, `roster/home/api/models.py`,
  `src/butlers/jobs/context_producers.py`, `src/butlers/jobs/briefing.py`.
- Affected tests: `tests/api/test_home_dashboard.py`,
  `tests/jobs/test_context_producers.py`, `tests/jobs/test_briefing.py`.
