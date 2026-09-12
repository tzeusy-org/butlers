## 1. Dashboard API guard

- [x] 1.1 `_ha_source_available(pool)` helper in `roster/home/api/router.py`,
      reusing `_require_ha_source_healthy`/`HASourceUnmeasurableError` from
      `src/butlers/jobs/home.py`.
- [x] 1.2 Wire into `list_entities`, `get_entity`, `list_areas`,
      `get_snapshot_status`, `list_devices` — each response tags
      `ha_source_available` in whatever envelope shape it already returns
      (`PaginationMeta` extra field, a top-level model field, a per-row
      field, `DevicePaginationMeta`).
- [x] 1.3 A missing single entity or empty bare area list fails closed with
      503 during an outage because neither response shape can carry a
      degraded flag honestly.
- [x] 1.4 `get_energy` / `get_energy/top-consumers` check source health before
      cached sensor discovery and return 503 during an outage, including when
      the cached sensor list is empty and no live HA call would otherwise run.

## 2. Context producer guard

- [x] 2.1 `run_home_presence_context_producer` confirms `ha_source_health`
      before reading presence rows; an outage is treated as "no fresh
      presence data" (signal left untouched).

## 3. Briefing contribution guard

- [x] 3.1 `run_home_briefing_contribution` confirms `ha_source_health` before
      the device-alert / temperature-outlier scan; an outage adds a
      high-priority highlight and sets `ha_source_unmeasurable: true` on the
      job's return dict instead of a false all-clear.

## 4. Explicitly out of scope

- [x] 4.1 `src/butlers/jobs/health_ha_reader.py` left unguarded — its
      `ha_entity_snapshot` query runs against the health butler's own
      schema-scoped pool, where the table does not exist; the read already
      fails (caught, returns no entities) independent of any HA outage.
      Building a health-schema HA source is a separate feature decision.

## 5. Tests

- [x] 5.1 `tests/api/test_home_dashboard.py`: end-to-end guard proof on
      `/snapshot-status` (real `_require_ha_source_healthy` path, not mocked
      away) plus flag-propagation coverage across the other four endpoints.
- [x] 5.2 `tests/jobs/test_context_producers.py`: outage leaves `at_home`
      untouched even with a fresh-looking snapshot row.
- [x] 5.3 `tests/jobs/test_briefing.py`: outage skips the snapshot scan and
      sets `ha_source_unmeasurable`; healthy path still queries the snapshot.

## 6. Contract and verification

- [x] 6.1 Add spec deltas to `home-dashboard-extensions`, `context-bus`, and
      `cross-butler-briefing-contribution`.
- [x] 6.2 `openspec validate --strict` on the changed spec.
- [x] 6.3 Run `make lint` and the affected test files.
