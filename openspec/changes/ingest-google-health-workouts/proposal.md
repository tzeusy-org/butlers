## Why

The vision marker "health data maintained without manual entry" and the Chronicler
promise "when did I last go running?" have no producer: the connector spec defers
workouts (REQ-connector-google-health-015) and GPS-plus-resting-HR inference cannot
recognise exercise. The owner has already consented to the `activity_and_fitness`
read scope, which covers the Google Health v4 `exercise` data type. This change uses
that consent; it adds no scope, account, or connector.

## What Changes

- The connector adds a `workout` resource bundle polling `exercise` data points
  (reconcile endpoint, trailing window, per-resource cursor) and emits one
  `workout_session` wellness envelope per recorded session.
- Health `wellness_ingest` maps the `workout_session` resource segment to the
  `workout_session` predicate with a metadata contract.
- The Chronicler workout adapter treats a device-detected session (`detection=auto`)
  as weaker evidence than an owner-logged one.
- REQ-connector-google-health-015 replaces "Workout ingestion remains deferred"
  with a source contract; the chronicler-source-compatibility workout scenario and
  RFC 0014 Amendment 1 are updated to match.

## Deferred slices

- Upstream edit/delete reconciliation (supersede with provenance): the public v4
  reference does not document reconcile tombstone semantics, so this is not claimed.
- Weight and body-fat into health measurements with owner-dominance (slice 2).
- "Last <activity>" answers from episodes with an evidence door (slice 3).
- `predicate_registry` seed for `workout_session`: deferred behind the open
  `mem_013` migration to avoid a forked Alembic chain; the predicate is accepted
  as a novel predicate meanwhile.

## Impact

`src/butlers/connectors/google_health.py`, `roster/health/tools/wellness_ingest.py`,
`src/butlers/chronicler/adapters/google_health.py`, and the two specs above.
