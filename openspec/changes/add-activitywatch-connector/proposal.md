## Why

No connector observes any computer today. For a 10-12h weekday of desktop
work, the Chronicler's day reconstruction shows nothing — the single
largest coverage gap identified in the 2026-07-05 Chronicler workday-
visibility deep dive (`docs/plans/2026-07-05-chronicler-time-inference-
deep-dive.md`, epic bu-whhll, Tier 1 "gold standard" signal). ActivityWatch
is an open-source, local-first active-window/AFK tracker already suited to
this: it runs on the owner's machine(s) and exposes a local REST API with
window-focus and AFK-status history.

## What Changes

- **New connector** (`src/butlers/connectors/activitywatch.py`): a single-
  machine, poll-based connector (mirrors the Home Assistant / Spotify
  pattern) that discovers the local `aw-watcher-window` and `aw-watcher-afk`
  buckets via ActivityWatch's REST API, buckets each window-focus event into
  a coarse app-class (`ide` / `terminal` / `browser` / `other`), and submits
  `ingest.v1` envelopes to the Switchboard. No OAuth/account registry — the
  machine is identified via `ACTIVITYWATCH_MACHINE_ID` env var (one connector
  instance per machine), matching the Home Assistant connector's env-only
  pattern for local/self-hosted services.
- **New durable evidence table** (`connectors.activitywatch_events`,
  migration `core_154`): one row per window-focus event, carrying the raw
  window title and app name for forensic use, plus the connector-computed
  `app_class` and `is_afk` status. Read by the Chronicler adapter.
- **New Chronicler adapter**
  (`src/butlers/chronicler/adapters/activitywatch.py`,
  `activitywatch.window` source): projects `app_focus` point events
  (evidence layer) and `screen_episode` rollups (activity layer) with a
  per-app-class duration breakdown, so a future occupation-classifier can
  derive work-vs-not-work from `dominant_app_class` without re-reading raw
  evidence.
- **Switchboard registration**: `activitywatch` channel/provider pair (RFC
  0003 Amendment 2), a global LLM-classification skip rule (`sw_018`,
  mirrors OwnTracks/Home Assistant), and `activitywatch` added to the
  heartbeat protocol's `VALID_CONNECTOR_TYPES`.
- **Privacy**: window titles are captured only in the durable evidence table
  and are NEVER projected into `ingest.v1` envelopes, Chronicler point
  events, or episodes — only the derived `app_class` bucket (and, in `full`
  ingestion tier, the raw `app` process name in the envelope only) reach
  those surfaces. This satisfies "window titles default privacy=sensitive;
  app-class only in normal view" by simply never building a title-carrying
  projection path in v1.

## Deliberately Out of Scope (Follow-Ups)

This bead ships the connector + migration + Chronicler adapter core. The
following are explicitly deferred (see Discovered-Follow-Ups in the PR):

- **Browser-domain sub-bucketing.** The bead asks for "browser-by-domain"
  granularity; v1 classifies all browsers into one `browser` app-class.
  True domain-level bucketing requires correlating the separate
  `aw-watcher-web` browser-extension bucket (which carries `url`) with
  window-focus events by timestamp — a distinct, non-trivial adapter concern.
- **Dedicated "occupation" category.** `screen_episode` is mapped to the
  existing `"tasks"` category (Work lane) in `aggregations.category_for()`
  today — there is no dedicated `"occupation"` category yet (that lands
  with epic bu-whhll Tier 2, routine inference). `dominant_app_class` is
  carried in the `screen_episode` payload specifically so future work can
  refine work-vs-not-work without re-processing raw evidence.
- **Multi-machine deployment tooling.** One `docker-compose.yml` service
  block is added as a template; a second machine (e.g. a work laptop,
  pending employer policy per the gate sign-off) needs its own service block
  — not templated/parameterized here.
- **Retention purge.** Unlike OwnTracks, this evidence table has no
  automatic TTL purge task in v1 (not a base connector-spec requirement;
  OwnTracks's purge is bespoke to that connector).

## Impact

- Affected specs: new `connector-activitywatch` capability spec (this
  change).
- Affected code: `src/butlers/connectors/activitywatch.py`,
  `src/butlers/chronicler/adapters/activitywatch.py`,
  `alembic/versions/core/core_154_activitywatch_events.py`,
  `roster/switchboard/tools/routing/contracts.py`,
  `roster/switchboard/tools/connector/heartbeat.py`,
  `roster/switchboard/migrations/018_switchboard_activitywatch_skip.py`,
  `roster/chronicler/butler.toml`, `src/butlers/chronicler/jobs.py`,
  `src/butlers/scheduled_jobs.py`, `docker-compose.yml`,
  `observability/prometheus/prometheus.yml`,
  `about/legends-and-lore/rfcs/0003-switchboard-routing-and-ingestion.md`.
