## Why

Weather/AQI/pollen is the cheapest cross-butler context feed in the ecosystem
(home pre-conditioning, health advisories, travel destination outlook)
behind Home's explicit air-quality promise (`roster/home/MANIFESTO.md`), but
nothing populates it today — Home only sees Home Assistant device state,
never ambient conditions (2026-07-25 JARVIS pursuit dossier, ranked move
#16, `docs/redesigns/2026-07-25-jarvis-pursuit.md`, bu-ep4ks.16).

This is slice 1 of a four-slice authoritative-source perception tier (bank
feed, flight status, atmosphere). Slices 2-4 (flight-status polling, SimpleFIN
bank feed, feed-vs-email reconciliation) are tracked as separate follow-up
work — this change lands the feed + table + schedule + one proving consumer
read surface only.

## What Changes

- **New shared context tables** (`public.atmosphere_readings` /
  `public.atmosphere_feed_status`, migration `core_188`): an append-only
  reading log plus a singleton status row that records every poll attempt
  (success or failure) so degraded state can be told apart from "not
  configured" without scanning the readings table.
- **New deterministic job**
  (`src/butlers/jobs/atmosphere.py::run_atmosphere_feed_refresh`,
  registered on the Home butler as `atmosphere_feed_refresh`, cron `*/30 * *
  * *`): fetches Open-Meteo's keyless forecast + air-quality APIs for the
  owner's configured home location. This is a zero-LLM context producer
  (mirrors `butlers.jobs.context_producers`), not a message-ingestion
  connector — there is no external "message" to classify or route, just an
  ambient feed to keep warm.
- **Home location config**: a new non-secret `entity_info` type
  `home_coordinates` (`"lat,lon"`), whitelisted in
  `credential_store._ENTITY_INFO_NON_SECRET_ALLOWED_TYPES` mirroring the
  `home_assistant_url` precedent (technical config, no predicate home in
  `entity_facts`).
- **Dashboard API** (`roster/home/api/router.py`):
  - `GET /api/home/atmosphere/current` — current conditions, with
    `configured`/`stale`/`source_error` degraded-mode flags per the
    CLAUDE.md "Degraded-Mode Response Envelope" convention. The proving
    consumer read surface for this slice.
  - `PATCH /api/home/atmosphere/location` — owner provisioning endpoint
    (stores `home_coordinates` via the existing `upsert_owner_entity_info`
    helper).
- **Owner dashboard configuration**: the existing Home butler Devices tab
  exposes a controlled latitude/longitude panel backed only by those two
  endpoints. It reports configured, unconfigured, stale, and source-error
  states without exposing the coordinates in global settings or adding a
  synchronous refresh action.

## Degraded-mode honesty

- No home location on file → `configured=false`. The job does not attempt a
  fetch and does not report this as an error (legitimate absence).
- Home location configured but the upstream request fails → `last_error` is
  recorded and `consecutive_failures` increments; no reading row is
  written; the job never raises.
- Pollen fields are `NULL` for non-European locations (Open-Meteo only
  forecasts pollen there) — `pollen_available=false` distinguishes this
  legitimate absence from a fetch failure.

## Deferred (owner must provision before slices 2-4 make sense)

- No API key is required for slice 1 (Open-Meteo forecast + air-quality are
  both keyless) — only the home location (`PATCH
  /api/home/atmosphere/location`) needs owner input.
- Flight-status polling (slice 2), SimpleFIN Bridge bank feed (slice 3, needs
  an owner-provisioned token), and feed-vs-email reconciliation (slice 4) are
  out of scope for this change.
