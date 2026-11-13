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
double-alert. Both judge the 60-second hold from the sensor's last state change (`last_changed`),
so an attribute-only update never makes an old alarm look new. If delivery fails the condition
stays open and is retried, and a still-active alarm re-sends after 15 minutes, 1 hour and 4 hours,
then daily, while the sensor stays on; a duplicate send between the two triggers is prevented by
keeping the delivery marker across confirmations. A sensor that reads `unavailable`, or a Home
Assistant source that has gone quiet, never clears an open alarm. The sentinel is read-only: it never calls a Home
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

## Owner places

The dashboard's **Devices → Places** panel, behind dashboard owner-auth, edits the owner place
registry `public.owner_places` (core migration `core_303`): named places with a kind
(home/work/airport/other), a centre at no more than five decimals, a 50-2000 m radius, other
names, exact Wi-Fi SSIDs and an optional Home Assistant zone. Edits replace the whole row under
compare-and-swap (a stale changed edit is `409`); pausing keeps a place but drops it from
resolution; removal is a soft delete that never reuses the place id. Refusals are content-blind,
the generic audit middleware never reads these bodies, and the explicit audit row records only
operation, outcome and versions. Only the Travel and Chronicler runtime roles can read the table
(row security) and no runtime role can write it. Deterministic readers use
`butlers.owner_places.load_active_owner_places`; no coordinate or SSID reaches a model.

Not yet cut over: the commuting ETA producer and the OwnTracks place-cluster and SSID projections
still read `OWNTRACKS_PLACE_REFERENCES` and `chronicler/owntracks/ssid_places`. A later slice
imports those once and moves the readers to the registry. Routes: `roster/home/api/router.py`
(`/api/home/places`); requirement: `butler-home` spec, "Owner place registry and dashboard
editor".

## Owner presence entities

The **Devices → Who counts as you** panel, behind dashboard owner-auth, edits
`home:presence:owner_entities`: the Home Assistant entity ids (any domain, including room sensors)
that the presence producer treats as the owner. `GET/PUT /api/home/settings/presence/owner-entities`
implement the adopted `home-presence-configuration` contract
(`openspec/changes/specify-home-presence-owner-entity-configuration/`): exact ids only (no trimming,
case-folding or name lookup), at most 50, each already present in `ha_entity_snapshot` (unseen ids are
one count-only `422 INVALID_REFERENCE`); writes serialize on a fixed advisory lock and compare-and-swap
the state row's version (identical list is a no-op at any version, a stale changed list is `409`); a
malformed stored value is a blind `503`, never an empty success. The generic audit middleware never
reads the PUT body; one allowlisted audit event records counts, versions and outcome. A PUT is a
settings receipt only: the next scheduled `run_home_presence_context_producer` run reads the list.

These guarantees cover only these two routes. The generic state API and the `state_*` MCP tools can
still read and overwrite the same key without this validation or lock.

## Owner presence reducer

`butlers.perception.presence` is a pure reducer; `butlers.jobs.home_presence_reducer` is its Home
adapter. Observations are typed and coordinate-free (side, opaque place reference, opaque key). The
Home Assistant input is the owner entities' `home`/`not_home` rows in `ha_entity_snapshot`, gated
by `ha_source_health`; the OwnTracks input is a connector-owned typed feed that a later slice
supplies (Home has no read on `connectors.owntracks_points` and never will). Confirmed leave/enter
transitions are written to the canonical `owner.place` slot and timeline as source `home.dots`; no
presence tables exist. Thresholds are named constants (3 minute quiet period, 2 distinct fixes,
100 m accuracy ceiling, 50 m margin floor, 10 minute retraction window, 5 minute heartbeat).
Reducer state lives in memory, so a restart makes the next confirmed side an `establish` preceded
by an `unknown` assertion, never a fabricated leave. A restart therefore costs one confirmation
window and any pending candidate, and a leave that happens while the reducer is down is not recorded. Activation (the `home.dots` profile with a
finite freshness maximum above the heartbeat, the owner subject, a `place` reference per home
place, the evidence references from `evidence_ref`, a writer login and the schedule) is a
separate owner-authorized act; until then nothing runs. `GET /api/home/presence/transitions`
(owner-only) lists confirmed edges with `late` and `retracted` flags.

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

- Recorder statistics go through `butlers.home_assistant_client.HAStatisticsClient` and
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
  reflect current state. Best-effort, it also reconciles owner-condition source
  `home:device-threshold`: identity `{entity_id, issue}` (`battery` or `offline`), only `warning`
  and `critical` qualify, and severity and battery percentage are evidence. A non-numeric battery
  is carried forward `confirm_only`; an unmeasurable HA source or empty snapshot skips the
  reconcile, and a ledger failure never breaks the job. A numeric reading with an old HA
  `last_updated` counts as measured, since HA bumps it only when the value changes.
- Actuation outcomes: only a connection-establishment failure or an HTTP rejection is `failed`. A
  timeout or reset after dispatch, or an unparseable 2xx, is `unverified` and still attempts live
  read-back.

## Related Pages

- [Health Butler](health.md) -- reads Home Assistant sensors (read-only) for health correlation
- [Switchboard Butler](switchboard.md) -- routes home automation messages here

### Communication audience and physical risk

Notify, TTS, persistent notifications and explicitly messaging/announcement
services additionally classify their complete target selectors as communication.
Target selectors are separate from message/title and other service data; both
remain safety-critical in the exact parked command. Ownership uses the existing
identity resolver's `other` handles (`home_assistant:notify:<service>` or
`home_assistant:<selector>:<id>`), never friendly names or inferred household
ownership. Missing, bulk, shared or unresolved targets require owner approval.

This adds no device registry and never downgrades physical risk. Unknown notify
and TTS services remain protected even when their targets resolve to the owner;
a standing rule cannot bypass a consequential or protected physical call.

Legacy notify `data.target` and unresolved area, label or floor selectors do not
inherit an owner-mapped service handle's identity. They remain unresolved
communication audiences while the independent protected-actuation gate remains
in force. Home Assistant's [legacy notify handler](https://github.com/home-assistant/core/blob/dev/homeassistant/components/notify/legacy.py)
forwards a generic service's explicit target to its notification platform.


### Shared Home Assistant transports

`butlers.home_assistant_client` owns the shared WebSocket authentication exchange,
closed dictionary/coalesced-list decoding, recorder statistics and REST factory.
Connector and module reconnect loops keep their own sessions, subscriptions,
message IDs, caches and keepalive tasks. One-shot statistics owns and closes its
own session; it does not share the persistent connection unless its caller
explicitly supplies the existing command sender.

Malformed frames are withheld from diagnostics. Authentication refusal retains
fixed configuration guidance rather than provider-supplied text. The older
module authentication scenario's server-message wording does not describe this
existing privacy boundary; this refactor preserves the content-free mapping.
The shared client adds no service-call WebSocket command, actuation authority,
POST retry or approval/receipt bypass.

REST fallback and history recovery keep an absolute 30-second per-call budget
around the caller-owned HTTPX client. HTTPX phase timeouts alone do not provide
that total bound. These migrated aiohttp owners keep redirects enabled and environment proxies off;
existing HTTPX owners keep their prior environment policy, TLS and timeout
settings. The module retains its long-lived client; jobs and settings probes
close their per-call clients. Missing job credentials still omit Authorization.
