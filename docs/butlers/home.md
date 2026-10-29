# Home Butler

Orchestrates the smart home through Home Assistant: comfort preferences per room, scenes composed
in conversation, energy awareness, and device health. It is the only butler allowed to actuate
Home Assistant, and it asks before anything destructive.

- **Identity and scope:** [`roster/home/MANIFESTO.md`](../../roster/home/MANIFESTO.md)
- **Required behavior:** [`butler-home` spec](../../openspec/specs/butler-home/spec.md) and
  [`module-home-assistant` spec](../../openspec/specs/module-home-assistant/spec.md)
- **Schedules, modules, and port:** [`roster/home/butler.toml`](../../roster/home/butler.toml)

## Physical actuation

`ha_call_service` runs under the physical-risk boundary of
[RFC 0028](../../about/legends-and-lore/rfcs/0028-home-physical-actuation-contract.md):
consequential calls park for owner approval, and every attempt writes a receipt to
`home.ha_command_log`, the authoritative actuation ledger. A receipt is `succeeded` only after a
live read-back confirms the requested state; any uncertainty after sending is `unverified` and
needs owner attention. An unverified or crashed approved attempt is never blindly retried under
the same approval; the owner reconciles the device and issues a new approved action.

Automatic reversible execution requires explicit same-domain entity targets and the repository's
service-specific data key/type/bounds subset. Area/device/label/all or absent targets, selectors
hidden in data, unlisted or invalid parameters and climate temperatures outside inclusive16–28C
(equivalent60.8–82.4F) park for exact owner approval. Climate auto-execution requires consistent
known unit context from the existing entity cache; missing units require approval, never a Celsius
guess. Range/mode updates also require approval. The full subset is in the module specification.
Argument snapshots reject NUL or surrogate code points in string values and object keys before
parking or sending; representable text stays exact. A parked action records a categorical reason
and exact original arguments. An explicit target
combined with a selector in data is ambiguous and cannot be silently merged even after approval;
create a new unambiguous action. A deliberately approved area/device/all command sends once but
remains unverified when no explicit target can be observed. No retry or receipt-success shortcut
is created. The fixed numerical subset is conservative gating, not a stored comfort preference.

## Home Assistant source health

`home.ha_entity_snapshot` is a last-known-state cache, not proof that Home Assistant is reachable.
Readers treat the snapshot as unmeasurable whenever the `home.ha_source_health` lease is missing,
in error, or expired: list endpoints keep last-known rows flagged `ha_source_available=false`,
reads that cannot distinguish absence from an outage return 503, presence signals are left
untouched, and the Home briefing reports the outage instead of an all-clear. Operator triage:
[Troubleshooting](../operations/troubleshooting.md#symptom-home-dashboard-says-home-assistant-is-unavailable).

## Safety sentinel

Leak, smoke, carbon monoxide and gas alerts do not depend on a model. On the dashboard (**Devices
→ Safety sensors**) the owner declares each `binary_sensor` that guards against a hazard and the
name to use in alerts; Home never guesses a role from a device class or from Home Assistant's own
name for the device. A declared sensor that stays on for 60 seconds opens an owner condition
(source `home:safety`) and sends one high-priority message through the notify path, which
quiet hours and do-not-disturb do not hold. Two triggers feed the same ledger: the websocket
event hook (seconds) and the `home_safety_sentinel` job every five minutes, so they never
double-alert. If delivery fails the condition stays open and is retried, and a still-active alarm
re-sends as the condition ages. A sensor that reads `unavailable`, or a Home Assistant source
that has gone quiet, never clears an open alarm. The sentinel is read-only: it never calls a Home
Assistant service. Code: `src/butlers/jobs/home_safety.py`; requirement: `home-deterministic-jobs`
spec, "Home Safety Sentinel Job".

## Private person mappings

The dashboard's **Devices → Person mappings** form, behind dashboard owner-auth, is the only
supported way to associate an exact `person.<slug>` Home Assistant ID with an existing person
entity. Submission is an all-or-nothing batch: exact existing pairs are no-ops and any conflict
refuses the whole batch. It never queries Home Assistant, infers identity from names, creates an
entity, or exposes a mapping read surface, and it returns only an opaque receipt and counts.
Remap, delete, and rollback are separate owner-authorized operations. Route:
`src/butlers/api/routers/home_person_mappings.py`.

## Proposed realtime feature specifications

The shared [event-response contract](../../openspec/changes/specify-realtime-feature-contracts/specs/event-response-contract/spec.md)
is owner-adopted. Two domain feature specifications are under review; their numerical profiles
and behavior are proposed and do not describe currently shipped features:

- [Appliance handoffs](../../openspec/changes/specify-home-appliance-handoff/proposal.md): explicit
  washer/dishwasher cycle evidence opens one unload/transfer task; only owner Done completes it.
  Monitoring and fixed reminders require no model calls. Stop watching preserves a fresh Done-only
  action for an existing task. Actual appliance mappings must pass commissioning before enrollment.
- [Comfort sessions](../../openspec/changes/specify-home-comfort-sessions/proposal.md): observe a
  room band for a finite period, optionally applying one exact authorized thermostat setpoint.
  Fresh room observations establish attainment; command acknowledgement does not. Manual override,
  source uncertainty and Stop remain explicit. Expiry does not automatically restore a setpoint.

Both reuse existing Home authority, canonical owner delivery and deterministic infrastructure.
Their design documents contain concrete initial runtime profiles and behavior-executing
verification matrices; they claim no measured live latency or token savings.

## Implementation Notes

- Recorder statistics go through `connectors/home_assistant_statistics.py::HAStatisticsClient` and
  the WebSocket command `recorder/statistics_during_period`. Energy consumption aggregates the
  per-period `change`, never cumulative `sum`; provider errors stay bounded and sanitised.
- Transport readiness is stronger than WebSocket authentication: health stays degraded, with REST
  fallback available, until every required `subscribe_events` acknowledgement succeeds. One task
  owns reconnects and shutdown awaits it.
- The broad `source_channel=home_assistant` skip covers noisy events, not deterministic wellness
  measurements: classify the wellness carve-out before that policy and advance the shared checkpoint
  only after the wellness submission succeeds.
- Weight history recovery queries `/api/history/period` with explicit significance and
  initial-state flags, keeps historical attributes and a per-entity cursor, dedupes equal
  timestamps, logs failures content-blind, and awaits its polling task on shutdown.
- `run_device_health_check` (`src/butlers/jobs/home.py`) treats stateless domains (`button.*`,
  `conversation.*`, `tts.*`, IR/RF blasters, Zigbee2MQTT gesture `*_action_*` sensors) as healthy at
  `unknown` (`is_steady_state_unknown`). `select_due_issues` throttles repeat alerts via state key
  `home:health_check:last_alerted` and the `realert` threshold (default 24h); returned counts still
  reflect current state.
- Actuation outcomes: only a connection-establishment failure or an HTTP rejection is `failed`. A
  timeout or reset after dispatch, or an unparseable 2xx, is `unverified` and still attempts live
  read-back.

## Related Pages

- [Health Butler](health.md) -- reads Home Assistant sensors (read-only) for health correlation
- [Switchboard Butler](switchboard.md) -- routes home automation messages here
