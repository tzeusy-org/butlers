---
name: interactive-response
description: Home-butler response-mode selection and worked examples for scenes, comfort, device status, energy, and confirmations.
version: 1.0.0
tools_required:
  - ha_activate_scene
  - ha_call_service
  - ha_get_entity_state
  - ha_get_statistics
  - ha_list_entities
  - memory_recall
  - memory_store_fact
  - notify
---

# Home Interactive Response Skill

## Purpose

Load this skill when an interactive home message needs a deliberate response
mode or a worked scene, comfort, status, energy, health-check, setup, or
confirmation pattern.

### Response Mode Selection

1. **React**: Emoji-only acknowledgment
   - Use when: The action is simple and self-explanatory
   - Example: User requests scene execution → React with ✅

2. **Affirm**: Brief confirmation message
   - Use when: Need a short confirmation with key information
   - Example: "Scene 'Movie Night' activated: lights dimmed, blinds closed."

3. **Follow-up**: Proactive question or observation
   - Use when: Need more information or can offer recommendations
   - Example: "I've set comfort preferences for your bedroom. Should I apply the same settings to the living room?"

4. **Answer**: Substantive response to a question
   - Use when: User asked a direct question about home status or data
   - Example: "Living room is at 72°F and 45% humidity, both in your comfort range. Bedroom is 70°F."

5. **React + Reply**: Combined emoji + message
   - Use when: Immediate acknowledgment plus substantive feedback
   - Example: React with ✅ then "Door locked. All windows closed. Security system armed."

### Complete Examples

#### Example 1: Scene Activation (Affirm)

**User message**: "Activate movie night"

**Actions**:
1. `ha_activate_scene(entity_id="scene.movie_night")`
2. `memory_store_fact(subject="movie-night-scene", predicate="usage_pattern", content="user triggered movie night scene", permanence="standard", importance=4.0, tags=["scene", "movie-night"])`
3. `notify(channel="telegram", message="Scene 'Movie Night' activated — lights dimmed to 20%, blinds closed, sound system on.", intent="reply", request_context=...)`

#### Example 2: Comfort Query (Answer)

**User message**: "What's the temperature in here?"

**Actions**:
1. `ha_list_entities(domain="sensor", area="living_room")` to find temperature/humidity sensors
2. `ha_get_entity_state(entity_id="sensor.living_room_temperature")` returns 72°F
3. `ha_get_entity_state(entity_id="sensor.living_room_humidity")` returns 48%
4. `memory_recall(subject="living-room", predicate="comfort_preference")` to get target range
5. `notify(channel="telegram", message="Living room is at 72°F and 48% humidity — both in your comfort range. Your target is 71-73°F.", intent="reply", request_context=...)`

#### Example 3: Preference Setting (Affirm)

**User message**: "I like the bedroom cooler at night, around 68 degrees"

**Actions**:
1. `memory_store_fact(subject="bedroom", predicate="comfort_preference", content="user prefers 68°F in bedroom at night", permanence="stable", importance=7.0, tags=["bedroom", "temperature", "comfort", "night"])`
2. `ha_call_service(domain="climate", service="set_temperature", target={"area_id": "bedroom"}, data={"temperature": 68})`
3. `notify(channel="telegram", message="Set bedroom night temperature to 68°F (67-69°F range). I'll adjust the thermostat tonight.", intent="reply", request_context=...)`

#### Example 4: Device Status with Alert (Answer)

**User message**: "Are all the lights off?"

**Actions**:
1. `ha_list_entities(domain="light")` to get all light entities with current state
2. Parse result: living room light (on, brightness 77/255 ≈ 30%), bedroom light (off), kitchen light (off)
3. `notify(channel="telegram", message="Living room light is still on at 30% brightness. Other lights are off. Want me to turn it off?", intent="reply", request_context=...)`

#### Example 5: Energy Report (Answer)

**User message**: "What's my energy usage today?"

**Actions**:
1. `ha_list_entities(domain="sensor")` to find energy monitoring sensors (look for entities with `energy` or `kwh` in the name)
2. `ha_get_statistics(statistic_ids=["sensor.energy_consumption_kwh"], start="<today 00:00 ISO>", end="<now ISO>", period="hour")` to get hourly energy totals
3. `ha_get_statistics(statistic_ids=["sensor.hvac_energy", "sensor.water_heater_energy"], start="<today 00:00 ISO>", end="<now ISO>", period="day")` for the per-device breakdown
4. `notify(channel="telegram", message="Today's usage: 18 kWh. Top consumers: HVAC (40%), Water heater (25%), Kitchen appliances (15%). Usage is 8% below your weekly average.", intent="reply", request_context=...)`

#### Example 6: Proactive Device Health Check (Follow-up)

**User message**: "Run the device health check"

**Actions**:
1. `ha_list_entities()` to survey all entities; look for any with state `unavailable` or `unknown`
2. `ha_list_entities(domain="sensor")` to find battery sensors; look for names containing `battery`
3. `ha_get_entity_state(entity_id="sensor.basement_sensor_battery")` returns 15%
4. `memory_store_fact(subject="basement-sensor", predicate="device_issue", content="battery at 15% — critical; needs replacement soon", permanence="volatile", importance=8.0, tags=["maintenance", "urgent", "battery"])`
5. `notify(channel="telegram", message="Health check complete. One device needs attention: basement sensor battery is at 15%.", intent="reply", request_context=...)`

#### Example 7: Scene Setup (Affirm + Follow-up)

**User message**: "Create a bedtime scene that cools the bedroom to 68 and dims all lights"

**Actions**:
1. `ha_list_entities(domain="light")` to discover all light entity IDs
2. `ha_call_service(domain="climate", service="set_temperature", target={"area_id": "bedroom"}, data={"temperature": 68})`
3. `ha_call_service(domain="light", service="turn_on", target={"area_id": "all"}, data={"brightness_pct": 10})`
4. `ha_call_service(domain="scene", service="create", data={"scene_id": "bedtime", "entities": {"climate.bedroom": {"state": "cool", "temperature": 68}, "light.living_room": {"state": "on", "brightness": 26}, "light.bedroom": {"state": "on", "brightness": 26}}})`
5. `memory_store_fact(subject="bedtime-scene", predicate="scene_preference", content="user created bedtime scene with temperature 68 and dim lighting", permanence="standard", importance=6.0, tags=["scene", "bedtime", "automation"])`
6. `notify(channel="telegram", message="Bedtime scene created — bedroom cooling to 68°F, all lights dimmed to 10%. Want me to schedule it for 10pm every night?", intent="reply", request_context=...)`

#### Example 8: Destructive Action Confirmation (Follow-up)

**User message**: "Disable the movie night automation"

**Actions**:
1. Instead of immediately disabling, ask for confirmation via `notify()`
2. `notify(channel="telegram", message="Disable 'Movie Night' automation? React with ✅ to confirm or ❌ to cancel.", intent="reply", request_context=...)`
3. Wait for user confirmation before calling `ha_call_service(domain="automation", service="turn_off", target={"entity_id": "automation.movie_night"})`
