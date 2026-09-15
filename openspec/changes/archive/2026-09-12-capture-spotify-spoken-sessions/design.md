## Context

Spotify's current-playback poll asks only for tracks and deliberately treats
`episode` items as idle. The existing music tracker persists an updatable
`track_names` summary to `connectors.spotify_listening_sessions`; putting
spoken content in that shape would mislabel podcasts and audiobooks as music.

The connector is the transport owner. Chronicler may later read an explicit
connector-owned surface, but this change must not add its adapter, projection,
API, UI, routing target, OAuth scope, transcript, or LLM behavior.

## Goals / Non-Goals

**Goals:**

- Request episode items from Spotify's current-playback API and classify their
  parent as `podcast`, `audiobook`, or `unknown_episode`.
- Track one spoken item at a time, close it deterministically on a different
  item or after the existing idle drain, and upsert bounded durable evidence.
- Submit one metadata-tier passive ingest envelope for each newly opened spoken
  session through the established policy/replay path, with a narrow global
  policy rule that pre-resolves `metadata_only` triage for that event family.
- Make the new source surface and its future deterministic projection contract
  explicit without implementing that projection.

**Non-Goals:**

- No new OAuth scope, recently-played episode backfill, direct Education or
  Chronicler route, dashboard, transcript, raw Spotify payload retention, or
  LLM classification/routing. The one narrow global policy rule is a
  deterministic bypass, not a new route or classifier behavior.
- No modification of the track `ListeningSession` shape, table, envelope, or
  context/digest/session-summary semantics.

## Decisions

### D1: Keep spoken state separate from music state

`SpokenSessionTracker` owns a `SpokenSession` whose identity is the active
episode ID, not a list of tracks. A first observation opens a session; the same
episode only advances its last-seen boundary; a different episode closes the
previous session and opens the next; no playback enters the same drain timeout
used by music; a later replay after a completed drain opens a new session.

This prevents a podcast or audiobook chapter from appearing in
`ListeningSession.track_names`. Reusing the music tracker was rejected because
its track aggregation intentionally merges changing item IDs into one music
session and has no content-kind parent model.

### D2: Normalize an allowlisted spoken record, not a raw API response

The connector derives `content_kind` from `item.show` (`podcast`),
`item.audiobook` (`audiobook`), or neither (`unknown_episode`). It records the
episode ID/name/URI, optional parent ID/name/URI, duration, observed start/end,
and a small fixed metadata object. It never stores descriptions, HTML,
transcripts, or `payload.raw`.

The passive `spotify.spoken_session` envelope carries `payload.raw = null` and
`ingestion_tier = metadata`; it is submitted through the unchanged connector
policy evaluator and replay buffer. A companion Switchboard migration seeds a
global `substring` rule on the stable `spotify:spoken:` external-event-id
prefix. That rule pre-resolves `metadata_only`, which is the existing contract
for persisting an envelope without LLM classification, butler routing, or
proactive notification. Music context, digest, and summary prefixes do not
match and remain unchanged. A metadata envelope was selected over a full raw
envelope to make capture useful without expanding data retention.

### D3: Give spoken evidence its own guarded connector surface

`connectors.spotify_spoken_sessions` uses a stable key
`spotify:<endpoint>:spoken:<started_at_ms>:<episode_id>`. The connector upserts
only mutable end/duration/metadata fields, so repeat polls and replay remain
idempotent. A core migration creates the table and indexes with
`IF NOT EXISTS`, conditionally grants connector DML to `connector_writer`, and
conditionally grants read-only access to `butler_chronicler_rw`.

This follows the existing Spotify music evidence pattern but intentionally
omits `track_names` and `raw_payload`. A separate table was selected over a
nullable content-kind column on music sessions because it preserves the
existing music contract and least-privilege source ownership.

## Risks / Trade-offs

- [Spotify omits both parent shapes] -> retain `unknown_episode` rather than
  guessing the media kind.
- [An evidence write fails] -> log the failure and preserve normal passive
  ingest submission, matching the existing music evidence failure posture.
- [Metadata grows unexpectedly] -> the migration constrains it to an object
  under a small byte limit and the writer constructs a fixed allowlist.
- [No future projection exists yet] -> the compatibility declaration marks the
  planned `chronicler_adapter` path explicitly; no source registry or adapter
  is added in this slice.

## Migration Plan

1. Apply the guarded core migration and the narrow Switchboard policy seed
   before deploying the connector code.
2. Deploy connector capture; duplicate poll/replay writes update the same
   evidence row and do not require a backfill.
3. Rollback stops connector writes first, then drops the dedicated table via
   migration downgrade. No historic music rows or other source surfaces change.

## Open Questions

None. Parentless episode payloads are deliberately captured as
`unknown_episode` rather than blocked or routed differently.
