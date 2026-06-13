# Design

## Scope

One pipeline addition and two contract expansions. The HA connector gains a
deterministic post-filter classifier that promotes health-shaped sensor events
onto the existing `wellness` channel; the Switchboard channel/provider registry
admits the new pairing; the Health butler's wellness ingest dispatches per
provider. No new tables, no new processes, no new channels — the design rides
the policy-bypass route that already moves Google Health data into facts at
zero LLM cost (`roster/switchboard/migrations/007_wellness_route_rule.py`,
`src/butlers/modules/pipeline.py` fanout_mode `policy_bypass`).

Doctrine fit (about/heart-and-soul/vision.md): rule 4 — classification is a
static rule table in the daemon, no LLM in the loop; rule 7 — the connector
normalizes transport into a canonical payload and the Health butler never
learns how a reading arrived beyond `metadata.provider`; rule 3 — delivery
remains Switchboard-mediated MCP.

## Event flow

```
HA state_changed
  → domain allowlist / significance / discretion   (existing 3-layer filter)
  → [NEW] WellnessClassifier.classify(entity_id, device_class, unit, attributes)
      ├─ no match  → emit ingest.v1 on home_assistant            (unchanged)
      └─ match     → emit ingest.v1 on home_assistant            (unchanged)
                   → emit ingest.v1 on wellness, provider=home_assistant,
                     payload.raw.wellness_measurement = {…}      (new)
  → checkpoint advance (once per HA event, after all submissions)

Switchboard: sw_007 source_channel=wellness → route_to:health, policy bypass
Health: wellness_ingest dispatch on source.provider
  ├─ google_health   → existing resource-segment translation     (unchanged)
  └─ home_assistant  → normalized-payload translation → memory_store_fact
```

## Decisions

### ADR-1: Classification is metadata-driven, conservative by default

Rules match on HA physical metadata — `device_class`, `unit_of_measurement`,
and (only for compound families) entity-id tokens. Vendor and integration names
never appear in rules; a Withings cuff, an ESPHome scale, and an Oura bridge
all classify identically. Default rule table:

| device_class | unit | entity token | metric |
|---|---|---|---|
| — | `mmHg` | `systolic` | `blood_pressure_systolic` |
| — | `mmHg` | `diastolic` | `blood_pressure_diastolic` |
| `weight` or — | `kg` / `lb` (device_class `weight` required when unit-only is ambiguous) | — | `weight` |
| — | `bpm` | — | `heart_rate` |
| — | `mg/dL` / `mmol/L` | — | `blood_sugar` |
| — | `steps` | — | `steps` |

Deliberately excluded from defaults: `temperature`, `humidity`, bare `%`
(SpO2) — ambient sensors share those signatures and false positives write
wrong health facts. Owners extend via config (ADR-2).

Rejected: LLM classification (violates doctrine rule 4 and the zero-cost
requirement); entity allowlist (per-sensor setup is exactly what the owner
rejected); classifying on the Health-butler side (requires cross-schema reads
of `connectors.*` and duplicates the ingestion path).

### ADR-2: Config via connector env vars, matching the existing HA surface

The HA connector is env-configured (`HA_DOMAIN_ALLOWLIST`,
`HA_POLL_INTERVAL_S`, …; `HAConnectorConfig.from_env`,
`src/butlers/connectors/home_assistant.py:898-939`). The classifier follows:

- `HA_WELLNESS_PROMOTION_ENABLED` — default `true`.
- `HA_WELLNESS_RULES_EXTRA` — JSON list of
  `{device_class?, unit?, entity_token?, metric}` objects appended to the
  default table (how an owner opts in SpO2 or body temperature for a specific
  signature).
- `HA_WELLNESS_ENTITY_DENYLIST` — comma-separated entity_ids never promoted.

Rejected: a DB-backed rule table (no precedent for per-connector DB config in
the HA connector; YAGNI until a second consumer of the rules exists).

### ADR-3: Dual emission, unified checkpoint, per-channel dedup

Promoted events emit on **both** channels: `home_assistant` consumers (home
butler, chronicler, history) see no behavior change; `wellness` is additive.
The checkpoint stays keyed by `(provider, endpoint_identity)` — one HA event is
one checkpoint unit regardless of how many envelopes it produced. The
Switchboard dedup key includes the channel
(`event:{channel}:{provider}:{endpoint_identity}:{external_event_id}`,
connector-base-spec), so a replay re-emitting both envelopes dedupes
independently per channel. The wellness envelope reuses the HA
`external_event_id` (`ha:{entity_id}:{time_ms}`) — disambiguation comes from
the channel, not the id.

The connector-base spec is amended to state explicitly that a connector MAY
emit one event on a second channel when per-event classification warrants it,
and that checkpoint/heartbeat/health-state remain connector-level (one WS
connection, one transport state — none of those are per-channel).

### ADR-4: Normalized measurement payload; Health side dispatches on provider

The wellness envelope from HA carries:

```json
"payload": {
  "raw": {
    "wellness_measurement": {
      "metric": "blood_pressure_systolic",
      "value": 120,
      "unit": "mmHg",
      "valid_at": "2026-06-12T14:30:00+00:00",
      "source_entity_id": "sensor.withings_systolic_blood_pressure",
      "device_class": null
    },
    "...full HA event context as today..."
  },
  "normalized_text": "Blood pressure (systolic): 120 mmHg"
}
```

`translate_wellness_envelope` (`roster/health/tools/wellness_ingest.py`)
dispatches on `source.provider`:

- `google_health` — existing path, byte-for-byte unchanged (resource-segment
  parsing of `external_event_id`, `_RESOURCE_TO_PREDICATES`).
- `home_assistant` — reads `wellness_measurement`, derives predicate
  `measurement_{metric}`, `valid_at` from the payload, writes one fact with
  `metadata = {provider, source_entity_id, unit, value}`.

Sender validation differs per provider. Google Health envelopes carry an owner
Google identity and validate against health-scoped accounts (active
`connector-google-health-multi-account` delta — untouched). HA envelopes carry
`sender.identity = <entity_id>` (a device, not a person); under the
single-owner federation rule (vision.md rule 1) any HA endpoint configured in
this instance is the owner's, so validation pins `source.provider ==
"home_assistant"` plus a well-formed `wellness_measurement` payload, and
rejects everything else with a labeled metric.

Rejected: making Google Health emit the normalized payload too (bigger blast
radius, separate change); a generic per-provider plugin registry (two providers
do not justify the indirection — a two-arm dispatch is honest).

### ADR-5: Provider-agnostic idempotency for HA-translated facts

Today the auto-generated temporal-fact idempotency key includes
`source_episode_id` (`src/butlers/modules/memory/storage.py:223-259`), so the
same physical reading via two providers stores two facts. The HA translator
passes an **explicit** key:

```
sha256("wellness|{owner_entity_id}|{scope}|{predicate}|{valid_at_iso}")[:32]
```

— no provider, no episode. Exact `(predicate, valid_at)` collisions across
providers (and HA replays) resolve to one fact, first-writer-wins, via the
existing `(tenant_id, idempotency_key)` no-op check
(`storage.py:1109-1119`). Google Health keys are not migrated in this change:
its predicates are disjoint from the default rule table
(sleep/activity/resting-HR/HRV/SpO2/breathing/VO2max vs.
BP/weight/glucose/HR/steps), and its daily-summary facts use date-granularity
`valid_at` that cannot collide with per-reading timestamps. The one genuine
overlap candidate (`steps`) differs semantically (daily total vs. counter
reading) and therefore in `valid_at` granularity; documented, accepted.

### ADR-6: Compound blood pressure stays decomposed in v1

Systolic and diastolic arrive as two HA entities with independent
`state_changed` events; pairing them into one
`{"systolic": x, "diastolic": y}` fact requires a time-window join with
held-state — disproportionate machinery for v1. They are stored as two facts
with a shared `valid_at` (devices report both legs of one measurement with one
timestamp), so read-time pairing is a `GROUP BY valid_at` away when a dashboard
needs it. Follow-up bead, not this change.
