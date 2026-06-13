# Proposal: Home Assistant wellness channel promotion

## Why

Health-shaped sensor readings that reach Home Assistant today (e.g. a Withings BPM
Connect blood-pressure cuff) stop at the raw layer. The HA connector archives every
allowlisted `state_changed` event into `connectors.home_assistant_history` and emits
it on the `home_assistant` channel, but nothing carries those readings into the
Health butler's fact store. Meanwhile the `wellness` channel already does exactly
this for Google Health: a seeded Switchboard rule (`sw_007`) routes
`source_channel='wellness'` envelopes to the Health butler over the policy-bypass
path (no LLM session per reading), and `roster/health/tools/wellness_ingest.py`
writes `measurement_*` facts mechanically.

The owner's stated requirement: raw data lands in a raw store, and health ingestion
is **agnostic** — adding a new health sensor to Home Assistant must require no code
change, no per-sensor config, and no LLM cost per reading. The rejected
alternatives are (a) the connector writing into a health schema directly (violates
transport/butler separation) and (b) a Health-butler cron that greps the raw table
for vendor-specific entities (cross-schema reach, per-vendor hardcoding, LLM cost).

## What Changes

- **HA connector** gains a deterministic wellness classifier: after the existing
  three-layer filter pipeline, each surviving `state_changed` event is matched
  against a metadata-driven rule table (`device_class` + `unit_of_measurement` +
  entity-token qualifiers — never vendor names). Matching events are emitted
  **twice**: on `home_assistant` (unchanged) and on `wellness` with
  `source.provider = "home_assistant"` and a normalized measurement payload
  (`metric`, `value`, `unit`, `valid_at`, `source_entity_id`).
- **Switchboard contracts**: `_ALLOWED_PROVIDERS_BY_CHANNEL["wellness"]` expands
  from `{google_health}` to `{google_health, home_assistant}`
  (`roster/switchboard/tools/routing/contracts.py`). RFC 0003 gains the matching
  amendment registering the `wellness/home_assistant` pairing.
- **Connector base spec**: amended to permit a connector to emit on a second
  channel for per-event classified promotion, with the checkpoint remaining keyed
  by `(provider, endpoint_identity)` only.
- **Health butler** `wellness_ingest` becomes provider-dispatching:
  `google_health` envelopes keep the existing resource-segment translation;
  `home_assistant` envelopes are translated from the normalized measurement
  payload into `measurement_{metric}` facts. Sender validation for the
  `home_assistant` provider pins on provider + payload shape (the sender identity
  is an HA entity_id, not an owner Google account — single-owner federation makes
  any configured HA endpoint the owner's).
- **Cross-provider idempotency**: HA-translated measurement facts use a
  provider-agnostic idempotency key over
  `(owner_entity, scope, predicate, valid_at)`, so the same physical reading
  delivered through two providers at the same timestamp stores exactly one fact
  (first-writer-wins).

## Out of Scope

- A dedicated Withings cloud connector (`wellness/withings`) — this change makes
  it unnecessary for HA-bridged devices; direct-cloud polling remains future work.
- Migrating the Google Health connector onto the normalized measurement payload
  contract (its resource-segment translation keeps working; convergence is a
  follow-up).
- Fuzzy time-window cross-provider dedup. Only exact `(predicate, valid_at)`
  collisions dedupe; near-miss timestamps from independent provider clocks are
  documented, not solved.
- Compound blood-pressure fact pairing. Systolic and diastolic arrive as separate
  HA entities and are stored as separate facts
  (`measurement_blood_pressure_systolic` / `_diastolic`); read-time pairing for
  dashboards/trends is a follow-up.
- Promoting ambient metrics (temperature, humidity, generic `%`) — excluded from
  the default rule table because room sensors share those signatures with body
  sensors; owners can opt specific entities in via config.
- Any UI/dashboard change.

## Risks

- **Misclassification.** A non-health sensor matching a rule (e.g. an aquarium
  monitor reporting mmHg) would pollute health facts. Mitigated by the
  conservative default rule table, an entity denylist knob, and per-event
  classifier metrics; the blast radius is wrong facts, which are supersedable.
- **Duplicate facts across providers.** Google Health predicates are disjoint
  from the default HA rule table today (sleep/activity/resting-HR vs.
  BP/weight/glucose), and the agnostic idempotency key removes exact-timestamp
  duplicates. Residual risk is near-miss timestamps; accepted and documented.
- **Replay amplification.** A checkpoint replay re-emits on both channels; the
  Switchboard dedup key includes the channel, so each channel dedupes
  independently. No new replay surface.
- **Paired delta atomicity.** Without the contracts.py pairing expansion, HA
  wellness envelopes are rejected by `IngestSourceV1` validation; without the
  health-side dispatch, they are rejected as non-owner senders. The change ships
  all three deltas as one atomic OpenSpec change; implementation sequencing is
  pairing first.

## Predecessor

Builds on the active `connector-google-health-multi-account` change (its
`butler-health` delta already generalized sender validation to the set of
health-scoped Google accounts — this change adds a sibling validation branch for
the `home_assistant` provider rather than modifying the Google branch) and on the
archived `2026-04-24-google-health-connector` design, which anticipated additional
providers registering under the `wellness` channel.
