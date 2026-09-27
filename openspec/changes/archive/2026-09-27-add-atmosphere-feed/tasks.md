## 1. Shared context tables

- [x] 1.1 Migration `core_188`: `public.atmosphere_readings` (append-only
  successful fetches) + `public.atmosphere_feed_status` (singleton status
  row, updated on every attempt) + per-role grants.

## 2. Home location config

- [x] 2.1 Whitelist `home_coordinates` in
  `credential_store._ENTITY_INFO_NON_SECRET_ALLOWED_TYPES`.

## 3. Deterministic job

- [x] 3.1 `src/butlers/jobs/atmosphere.py`: resolve home location
  (env override, then `entity_info`), fetch Open-Meteo forecast +
  air-quality (keyless), parse, store.
- [x] 3.2 Honest degradation: not-configured skip (no fetch attempt), fetch
  failure recorded without raising, pollen-null-for-non-European-location
  treated as legitimate absence.
- [x] 3.3 Register `atmosphere_feed_refresh` in
  `scheduled_jobs.py::_HOME_DETERMINISTIC_JOB_HANDLERS` and
  `roster/home/butler.toml` (`*/30 * * * *`).
- [x] 3.4 Tests: `tests/jobs/test_atmosphere.py` (fetch-parse-store
  roundtrip, not-configured skip, fetch-failure degrade, pollen
  legitimate-absence vs presence).

## 4. Dashboard API

- [x] 4.1 `GET /api/home/atmosphere/current`: degraded-mode envelope
  (`configured`/`stale`/`source_error`), last-known-good values retained
  alongside the flags rather than zeroed.
- [x] 4.2 `PATCH /api/home/atmosphere/location`: owner provisioning via
  `upsert_owner_entity_info`.
- [x] 4.3 Tests: `tests/api/test_home_dashboard.py` (not-configured,
  healthy, degraded response shapes; location patch success/no-owner/
  validation).

## 5. Owner dashboard configuration and deferred follow-up beads

- [x] 5.1 Deferred: Atmosphere slice 2: flight-status connector (tracked in bu-60pwv6.15)
- [x] 5.2 Deferred: Atmosphere slice 3: SimpleFIN Bridge bank feed (tracked in bu-60pwv6.16)
- [x] 5.3 Deferred: Atmosphere slice 4: feed-vs-email reconciliation (tracked in bu-60pwv6.17)
- [x] 5.4 Dashboard panel for home location in the Home butler's existing
  Devices tab: controlled numeric coordinates, honest feed health, accessible
  loading/error/pending/success states, and scheduled-refresh wording.
- [x] 5.5 Deferred: Atmosphere consumer wiring: pre-conditioning, advisories, travel outlook (tracked in bu-60pwv6.18)
