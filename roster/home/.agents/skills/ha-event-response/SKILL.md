---
name: ha-event-response
description: Home Assistant event classification and response procedure for safety, environmental drift, automation failures, and routine changes.
version: 1.0.0
tools_required:
  - ha_call_service
  - ha_get_history
  - ha_list_entities
  - memory_recall
  - memory_store_fact
  - notify
---

# Home HA Event Response Skill

## Purpose

Load this skill when a routed Home Assistant event must be classified and
handled as a proactive home state change.

## HA Event Response Patterns

When the Home butler receives a routed ingestion event from the Switchboard with
`source.channel = "home_assistant"`, it processes the event as a proactive home state change
notification. The butler must determine the appropriate response using its HA tools and memory.

### Event Classification

Classify every HA-sourced event into one of four categories before responding:

| Category | Trigger | Response |
|---|---|---|
| **Safety-critical** | Lock/cover entity transitions to unexpected state (e.g. door unlocked at night, garage opened while away) | Notify owner immediately; store volatile fact |
| **Environmental drift** | Sensor reading falls outside owner's stored comfort preferences | Take corrective action if available, else notify owner |
| **Automation failure** | Automation triggered with unexpected outcome, or entity transitions to `unavailable` | Store volatile device_issue fact; include in next health check |
| **Routine acknowledgment** | Expected state change (lights on during normal hours, ordinary temperature fluctuation) | Store volatile fact silently for pattern analysis; do NOT notify |

### Safety-Critical Events

**Trigger conditions:**
- `lock` entity state changes to `unlocked` during nighttime hours or while the home is set to "away" mode
- `cover` entity (garage door, gate, skylight) opens unexpectedly
- Security-related `binary_sensor` triggers (motion in vacant areas, door/window sensor while armed)

**Required response:**
1. Notify owner immediately: `notify(channel="telegram", intent="proactive", message=<alert>)`
2. Store volatile fact with `importance >= 8.0`
3. Do NOT wait for owner confirmation; this is a push alert

**Memory fact:**
```python
memory_store_fact(
    subject="front-door-lock",          # entity-specific subject
    predicate="device_issue",
    content="Front door unlocked at 11:42pm — unexpected; owner notified",
    permanence="volatile",
    importance=9.0,
    tags=["security", "lock", "urgent", "alert"]
)
```

### Environmental Drift Events

**Trigger conditions:**
- Temperature sensor reading deviates from stored `comfort_preference` by more than the significance threshold
- Humidity sensor reading falls outside owner's preferred range
- Air quality sensor reaches an alert threshold

**Required response:**
1. Check memory for applicable `comfort_preference` facts: `memory_recall(subject=<room>, predicate="comfort_preference")`
2. Look for a matching scene or automation: `ha_list_entities(domain="climate")` or `ha_list_entities(domain="scene")`
3. **If corrective action is available:** Execute it silently (e.g., adjust thermostat), then store a `comfort_deviation` fact
4. **If manual intervention is needed:** Notify owner with context and suggested action

**Memory fact (corrective action taken):**
```python
memory_store_fact(
    subject="living-room",
    predicate="comfort_deviation",
    content="Temperature drifted to 78°F (preference: 71-73°F); thermostat auto-adjusted to 71°F",
    permanence="volatile",
    importance=5.0,
    tags=["temperature", "comfort", "auto-corrected"]
)
```

### Automation Failure Events

**Trigger conditions:**
- An `automation` entity triggers but the expected state change does not occur
- An entity transitions to `unavailable` or `unknown` state
- A `script` domain entity exits with an error state

**Required response:**
1. Store a volatile `device_issue` fact with appropriate failure tags
2. Do NOT notify the owner immediately for single occurrences; include in the next scheduled device health check
3. If the entity has been `unavailable` for more than one hour (check `ha_get_history`), escalate to owner notification

**Memory fact:**
```python
memory_store_fact(
    subject="basement-sensor",          # use the specific entity's subject
    predicate="device_issue",
    content="Entity transitioned to unavailable at 03:14am; first occurrence",
    permanence="volatile",
    importance=6.0,
    tags=["unavailable", "device", "maintenance", "health-check"]
)
```

### Routine State Change Acknowledgment

**Trigger conditions:**
- Light state changes during expected hours (on during morning/evening, off at bedtime)
- Temperature fluctuation within normal daily range and within comfort preferences
- Expected automation triggers (scheduled scene activations, timed device changes)

**Required response:**
1. Store a volatile fact for pattern tracking
2. Do NOT notify the owner
3. Do NOT call any HA services (no corrective action needed)

**Memory fact:**
```python
memory_store_fact(
    subject="living-room-light",
    predicate="usage_pattern",
    content="Light turned on at 6:47am — consistent with morning routine pattern",
    permanence="volatile",
    importance=2.0,
    tags=["routine", "pattern", "light"]
)
```

### Interactive Response Mode: HA Event Handling

When an HA event arrives via a Telegram-like interactive channel (i.e., the ingest envelope
includes `source_channel = "telegram_bot"` re-routing from a user-facing follow-up), treat the
session as interactive. For purely system-generated HA events, the `source_channel` will be
`"home_assistant"` and the session is **non-interactive**; `notify()` is the only output path.

#### Non-interactive HA events (source_channel = "home_assistant")

Use the four response categories above. Do NOT produce conversational text output; it is discarded
in headless sessions. All owner-facing communication MUST go through `notify()`.

| Event category | Response mode |
|---|---|
| Safety-critical | React + Reply: emoji (🚨) plus alert message via `notify(intent="proactive")` |
| Environmental drift (auto-corrected) | Silent: store fact, no notify |
| Environmental drift (manual needed) | Affirm via `notify(intent="proactive")`: state problem and suggested action |
| Automation failure (first occurrence) | Silent: store fact, no notify |
| Automation failure (extended unavailability) | Affirm via `notify(intent="proactive")` |
| Routine acknowledgment | Silent: store fact only |

#### Example: Safety-critical HA event (non-interactive)

**Incoming event**: `lock.front_door` → `unlocked` at 11:42pm

**Actions**:
1. `memory_recall(subject="home-mode", predicate="away_status")` to check if away/nighttime mode
2. `memory_store_fact(subject="front-door-lock", predicate="device_issue", content="Front door unlocked at 11:42pm unexpectedly", permanence="volatile", importance=9.0, tags=["security", "lock", "urgent", "alert"])`
3. `notify(channel="telegram", intent="proactive", message="🚨 Front door unlocked at 11:42pm. Was this you? Reply ✅ to confirm or I can re-lock it for you.")`

#### Example: Environmental drift with corrective action (non-interactive)

**Incoming event**: `sensor.living_room_temperature` → `78°F` (comfort preference: 71-73°F)

**Actions**:
1. `memory_recall(subject="living-room", predicate="comfort_preference")` to get comfort range
2. `ha_list_entities(domain="climate", area="living_room")` to find thermostat
3. `ha_call_service(domain="climate", service="set_temperature", target={"area_id": "living_room"}, data={"temperature": 72})`
4. `memory_store_fact(subject="living-room", predicate="comfort_deviation", content="Temperature drifted to 78°F; auto-adjusted thermostat to 72°F", permanence="volatile", importance=5.0, tags=["temperature", "comfort", "auto-corrected"])`
5. (No notify; corrective action taken silently)

#### Example: Automation failure / entity unavailable (non-interactive)

**Incoming event**: `sensor.basement_motion` → `unavailable`

**Actions**:
1. `ha_get_history(entity_ids=["sensor.basement_motion"], start="<2 hours ago ISO>", end="<now ISO>")` to check how long it's been unavailable
2. If unavailable < 60 min: `memory_store_fact(subject="basement-motion", predicate="device_issue", content="Entity unavailable since 02:30am; monitoring", permanence="volatile", importance=6.0, tags=["unavailable", "device", "maintenance", "health-check"])`
3. If unavailable >= 60 min: same store + `notify(channel="telegram", intent="proactive", message="Basement motion sensor has been offline for over an hour. It may need attention.")`

#### Example: Routine acknowledgment (non-interactive, silent)

**Incoming event**: `light.kitchen` → `on` at 7:02am

**Actions**:
1. `memory_store_fact(subject="kitchen-light", predicate="usage_pattern", content="Light turned on at 7:02am — consistent with morning routine", permanence="volatile", importance=2.0, tags=["routine", "pattern", "light"])`
2. (No notify, no service call)
