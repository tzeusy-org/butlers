# Connectors

> **Scope:** External transport adapters that ingest messages into the system.
> **Belongs here:** Connector architecture overview, per-connector profiles (setup, config, ingestion flow, cursors).
> **Does NOT belong here:** Switchboard routing logic (see [Architecture](../concepts/switchboard-routing.md)), module internals.

- [Overview](overview.md) — connector architecture, responsibilities, what connectors must/must not do

### Connector Profiles
- [Telegram Bot](telegram-bot.md) — Telegram bot connector
- [Telegram User Client](telegram-user-client.md) — Telegram MTProto user client
- [Gmail](gmail.md) — Gmail IMAP/API connector
- [Gmail Ingestion Policy](gmail-ingestion-policy.md) — email ingestion filtering rules
- [Heartbeat](heartbeat.md) — connector health monitoring
- [Live Listener](live-listener.md) — audio live listener connector
- [OwnTracks](owntracks.md) — phone location setup, evidence cadence, and privacy notes
- [WhatsApp](whatsapp.md) — WhatsApp bridge ownership, pairing, and operator setup
- [Attachment Handling](attachment-handling.md) — file/media attachment processing
- [Metrics](metrics.md) — connector metrics and statistics

### Capture manifests (evidence before acknowledgement)

Every connector commits a durable evidence row before it acknowledges an event, advances its
checkpoint or submits it to Switchboard. `switchboard.message_inbox` is a routing buffer, never
the evidence copy. Gmail, WhatsApp, Telegram bot and Telegram user client carry a manifest in
their own page; the sources below have no page of their own.

- **Google Drive.** Provider offers: the Drive changes feed (file metadata, no content). We keep:
  one `connectors.evidence_journal` row per submitted change (`source = google_drive`, metadata
  tier, `raw = null`, so ids and headers only), committed before the submit and before the page
  token commits. If the write fails, nothing is submitted, nothing is parked, and the page token holds. Provider
  retention and backfill: the changes feed is re-readable from a saved page token.
- **Google Health.** Provider offers: the Google Health API samples per resource (steps, heart
  rate, sleep, workouts) with their own timestamps. We keep: one `connectors.evidence_journal`
  row per submitted sample (`source = wellness`), committed before the submit. If the
  write fails, the sample is not consumed and the poll checkpoint does not advance; a policy-blocked
  sample writes no row. Provider retention and backfill: samples are re-readable over the API's own
  history window.
- **OwnTracks.** Location points live in `connectors.owntracks_points`, written before the submit.
  Transitions and waypoints, which have no table, go to `connectors.evidence_journal` before the
  submit (see [OwnTracks](owntracks.md)).
- **Spotify.** Listening sessions live in `connectors.spotify_listening_sessions`, whose
  `raw_payload` column now holds the session summary's `payload.raw` (the same value the envelope
  carries); an in-progress session is updated in place.
