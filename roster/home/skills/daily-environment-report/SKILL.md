# Skill: Daily Environment Report

## Purpose

Generate and send a daily home environment report every morning at 8am. Check temperature,
humidity, air quality, and lighting levels across all rooms, compare against the user's stored
comfort preferences, and flag any out-of-range conditions with actionable recommendations.
Deliver via `notify(intent="send")` to the owner's preferred channel.

## When to Use

Use this skill when:
- The `daily-environment-report` scheduled task fires (cron: `0 8 * * *`, daily at 08:00)
- User requests "send me the morning home report" or similar

## Workflow

### Step 1: Discover Rooms and Sensors

1. Call `ha_list_areas()` to get all configured Home Assistant areas (rooms).
2. Call `ha_list_entities(domain="sensor")` to get all sensor entities.
3. Group sensors by area. For each area, identify relevant sensors:
   - **Temperature**: entities with `temperature` in name/entity_id
   - **Humidity**: entities with `humidity` in name/entity_id
   - **Air quality / CO2**: entities with `air_quality`, `co2`, `pm25`, `voc` in name
   - **Illuminance / lighting**: entities with `illuminance`, `lux`, `light_level` in name

### Step 2: Read Current Conditions Per Room

For each room with sensors, call `ha_get_entity_state()` on each relevant sensor:

```python
ha_get_entity_state(entity_id="sensor.living_room_temperature")
ha_get_entity_state(entity_id="sensor.living_room_humidity")
ha_get_entity_state(entity_id="sensor.bedroom_temperature")
# etc. for all rooms with sensors
```

Collect readings as a room-by-room map:
```
{
  "living_room": {"temperature": 72, "humidity": 48, "co2": 650},
  "bedroom": {"temperature": 68, "humidity": 52},
  "kitchen": {"temperature": 74, "humidity": 55, "air_quality": "good"},
  ...
}
```

### Step 3: Retrieve Comfort Preferences from Memory

For each room, retrieve stored preferences:

```python
memory_recall(subject=<room_name>, predicate="comfort_preference")
```

Also check for time-specific preferences (morning context):
```python
memory_recall(subject="morning", predicate="comfort_preference")
```

If no preferences are stored for a room, use healthy default ranges:
- Temperature: 68-76°F
- Humidity: 30-60%
- CO2: <1000 ppm
- AQI: <50

### Step 4: Compare Readings Against Preferences

For each room and metric, evaluate:

- **Within range**: OK — note it briefly
- **Minor deviation** (e.g., ±2°F, ±10% RH): Flag with soft suggestion
- **Moderate deviation** (e.g., ±5°F, ±20% RH, CO2 1000-1500 ppm): Flag with recommendation
- **Critical condition** (e.g., temperature <60°F or >85°F, CO2 >1500 ppm, AQI >100): Alert prominently

### Step 5: Generate Actionable Recommendations

For each flagged condition, compose a concrete recommendation:

- Low humidity: "Bedroom humidity at 25% — below your 30% minimum. Consider running the humidifier."
- High CO2: "Kitchen CO2 at 1200 ppm — open a window or run the ventilation fan."
- Temperature deviation: "Living room at 65°F — below your 68°F minimum. The thermostat may need
  adjustment."

Limit to 3 most important recommendations to avoid overwhelming the user.

### Step 6: Store Deviation Facts in Memory

For any out-of-range conditions, store as volatile memory:

```python
memory_store_fact(
    subject=<room_name>,
    predicate="comfort_deviation",
    content="<metric> at <value> — outside preference range of <min>-<max> at 8am",
    permanence="volatile",
    importance=6.0,
    tags=[<room_name>, <metric>, "morning-report", "deviation"]
)
```

### Step 7: Compose and Send the Report

Format the report as a room-by-room summary:

```
Morning Home Report — [Day, Date]

Living Room: 72°F, 48% humidity — comfortable
Bedroom: 65°F, 52% humidity — temperature below your 68°F target
Kitchen: 74°F, 55% humidity, CO2: 1200 ppm — ventilation recommended
[Other rooms...]

Alerts:
  Bedroom is 3°F below your preferred 68°F. Want me to raise the thermostat?
  Kitchen CO2 at 1200 ppm — open a window or run the fan.

All other rooms are within your comfort range.
```

Send via:

```python
notify(
    intent="send",
    subject="Morning Home Report — [Day, Date]",
    message=<formatted_report>,
    request_context=<session_request_context>
)
```

Use `intent="send"` — this is a scheduled proactive delivery, not a reply.

## Exit Criteria

- `ha_list_areas()` called to discover all rooms
- `ha_list_entities(domain="sensor")` called to discover sensors
- `ha_get_entity_state()` called for each relevant sensor per room
- `memory_recall()` called to retrieve stored comfort preferences per room
- Readings compared against preferences; deviations classified by severity
- Deviation facts stored via `memory_store_fact()` for out-of-range conditions
- Report composed and sent via `notify(intent="send")`
- Session exits — no interactive follow-up in this session

## Common Failure Modes

### No Sensors Available for a Room
- Skip that room in the report (do not fabricate readings).
- Note in the report: "[Room]: no sensors configured."

### Sensor Returns Unavailable / Unknown State
- Skip that metric for that room.
- If it was working previously, store a volatile fact:
  `memory_store_fact(subject=<room-sensor>, predicate="device_issue", content="sensor offline during morning report", permanence="volatile", ...)`

### No Comfort Preferences Stored
- Use healthy default ranges and note at the end of the report:
  "Preferences not yet configured for all rooms. You can set room preferences by telling me
  what temperature, humidity, or lighting you prefer in each room."
