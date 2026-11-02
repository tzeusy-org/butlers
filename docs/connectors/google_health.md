# Google Health connector: workout edits and deletions

Reference notes for how the connector's workout (`exercise`) resource handles upstream changes.
Spec: `openspec/changes/ingest-google-health-workouts/specs/connector-google-health/spec.md`.

## Edits supersede with provenance

- The workout envelope keeps `external_event_id` `google_health:<user>:workout_session:<id>` and
  uses the idempotency key `google_health:<user>:workout:<id>:<digest>`. `<digest>` is a 16-hex
  SHA-256 prefix over the normalized session fields (start, end, duration, activity type,
  detection, calories, distance, average heart rate, and `updateTime` when the API returns one).
- An unchanged re-poll has the same digest, so ingest dedupes it as before.
- An edited session has a new digest, so it arrives as a new envelope. Health ingest writes a new
  `workout_session` fact, marks the earlier active fact `superseded` (`invalid_at` set), sets the
  new fact's `supersedes_id`, and adds a `memory_links` `supersedes` edge. Ingest deletes nothing; the superseded version is retained
  until the standard superseded-fact purge (`memory_purge_superseded`, 7 days by default).
- Every version shares `metadata.workout_session_key` (the bare per-session key). Chronicler keys
  the workout episode on it, so an edit updates the one episode.
- Known edge: an edit back to earlier values without an upstream `updateTime` reproduces an older
  digest and dedupes to the superseded fact. The edit is not applied until the data changes again.
- Concurrency: a per-session advisory lock serializes versions and replays; only rows written
  before the new fact are retired, conditionally, and `invalid_at` is the new fact's write time.
- Rollout: a re-polled, unchanged session whose stored fact predates versioned keys (bare key, no
  `workout_session_key`) is recognised by identical `valid_at`, content and metadata and writes
  nothing. An edited one supersedes the legacy fact normally.

## Deletions are not inferred

Findings (checked 2026-11-03):

- The [data management guide](https://developers.google.com/health/data-management) says list
  calls return new data and changes to existing data, and that `reconcile` deduplicates overlapping
  records across devices. It documents no tombstone, deleted flag, or changes feed for either call.
- The [dataPoints reference](https://developers.google.com/health/reference/rest/v4/users.dataTypes.dataPoints)
  documents `batchDelete` (up to 10000 points) but does not say whether a deleted point is surfaced
  or only omitted from `list` or `reconcile`.

Absence from a windowed response is therefore not evidence of deletion, and the connector changes
nothing for an absent session. If Google documents a positive deletion signal, handle it as a
superseding "retracted" fact with provenance.
