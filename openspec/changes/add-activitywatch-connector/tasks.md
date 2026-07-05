## 1. RFC / registration prerequisites

- [x] 1.1 RFC 0003 Amendment 2: register `activitywatch` `SourceChannel` /
  `SourceProvider` and the `activitywatch/activitywatch` pairing in
  `roster/switchboard/tools/routing/contracts.py`.
- [x] 1.2 Register `activitywatch` in `VALID_CONNECTOR_TYPES`
  (`roster/switchboard/tools/connector/heartbeat.py`).
- [x] 1.3 Migration `sw_018`: global ingestion-policy skip rule for
  `source_channel='activitywatch'` (mirrors sw_006 / sw_010).

## 2. Durable evidence table

- [x] 2.1 Migration `core_154`: `connectors.activitywatch_events` table +
  indexes + grants (`connector_writer` write, `butler_chronicler_rw` read).

## 3. Connector

- [x] 3.1 `src/butlers/connectors/activitywatch.py`: poll-based connector,
  bucket discovery, app-class classification, AFK-interval matching,
  ingest.v1 envelope construction, durable evidence persistence.
- [x] 3.2 Standard connector obligations: heartbeat, Prometheus metrics,
  health endpoint, filtered-event buffer + policy gate, replay-queue drain,
  timestamp checkpoint via cursor_store.
- [x] 3.3 Bounded first-run backfill (`ACTIVITYWATCH_MAX_BACKFILL_DAYS`).
- [x] 3.4 `docker-compose.yml` service block + Prometheus scrape target.
- [x] 3.5 Tests: `tests/connectors/test_activitywatch_connector.py`.

## 4. Chronicler adapter

- [x] 4.1 `src/butlers/chronicler/adapters/activitywatch.py`:
  `app_focus` point events (evidence layer, app-class + duration only) and
  `screen_episode` rollups (activity layer, per-app-class breakdown +
  `dominant_app_class`) with carryover continuation across adapter runs.
- [x] 4.2 Register `activitywatch.window` in
  `src/butlers/chronicler/contracts.py` (`INITIAL_SOURCES`).
- [x] 4.3 Wire the adapter as a Chronicler scheduled job (`jobs.py`,
  `scheduled_jobs.py`, `roster/chronicler/butler.toml`).
- [x] 4.4 Map `(activitywatch.window, screen_episode)` to the `"tasks"`
  category (Work lane) in `aggregations._CATEGORY_MAP` + `_D1_PAIRS` test
  fixture (required by `test_all_supported_sources_have_non_other_category`).
- [x] 4.5 Tests: `tests/chronicler/test_activitywatch_adapter.py`.

## 5. Deferred (see proposal.md "Deliberately Out of Scope")

- [ ] 5.1 Browser-domain sub-bucketing via `aw-watcher-web` correlation.
- [ ] 5.2 Dedicated "occupation" category (refining beyond "tasks") once
  Tier 2 (routine inference) lands.
- [ ] 5.3 Multi-machine `docker-compose.yml` templating.
- [ ] 5.4 Evidence-table retention purge task.
