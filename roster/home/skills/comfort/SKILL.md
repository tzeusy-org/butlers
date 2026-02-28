# Skill: Comfort Management

## Purpose

Define and maintain user comfort preferences across rooms, times of day, and seasons. Automatically
adjust home environment (temperature, humidity, lighting, air quality) to match stored preferences.
Monitor for deviations and proactively alert when conditions drift outside acceptable ranges.

## When to Use

Use this skill when:
- User is setting or adjusting comfort preferences for a room or time period
- User is asking about current environmental conditions in a room
- System detects environmental deviation from preferences and needs to alert user
- User wants to establish seasonal adjustments or recurring preferences

## Workflow

### Step 1: Establish Comfort Preferences

When user expresses a comfort preference (e.g., "I like the bedroom cooler at night"):

1. **Parse the preference**: Extract room, metric (temperature/humidity/lighting), desired value/range, and time context
2. **Call `environment_set_comfort_preference()`** with:
   - `room`: The room name (e.g., "bedroom", "living-room")
   - `time_period`: Optional time context (e.g., "night", "daytime", "morning", "all_day")
   - `preference`: JSON object with metrics and ranges
     - `temperature_min`/`temperature_max`: Preferred temperature range in °F
     - `humidity_min`/`humidity_max`: Preferred humidity range as % (30-60% is typical healthy range)
     - `brightness_min`/`brightness_max`: Preferred lighting brightness (0-100%)
     - `air_quality_index_max`: Maximum acceptable AQI (lower is better; <50 is good)
3. **Store a memory fact** using `memory_store_fact()`:
   - `subject`: Room name
   - `predicate`: `comfort_preference`
   - `content`: Plain text description of the preference
   - `permanence`: `stable` (these preferences persist long-term unless explicitly changed)
   - `importance`: 7-8 (comfort is important)
   - `tags`: Include room name and metric (e.g., `["bedroom", "temperature", "night", "comfort"]`)
4. **Confirm with user** via `notify()` (affirm mode):
   - Repeat back the preference clearly
   - Indicate when it will take effect (immediately, tonight, etc.)
   - Example: "Set bedroom night temperature to 68°F (67-69°F range). I'll start adjusting from tonight."

### Step 2: Monitor Environmental Conditions

Regularly (typically triggered by scheduled reports):

1. **Call `environment_get_reading()`** for each room with stored preferences
2. **Call `environment_get_comfort_preference()`** to get stored targets
3. **Call `environment_check_deviation()`** to detect out-of-range conditions
4. **If conditions are within range:**
   - No action needed; quietly maintain current settings
5. **If conditions deviate from preferences:**
   - Prepare to send proactive alert
   - Example deviation: "Bedroom is 74°F but you prefer 68°F"

### Step 3: Handle Deviations

When environment deviates from preferences:

1. **Determine severity**:
   - Minor (within 2°F or 10% RH of preference): Gentle suggestion via `notify()`
   - Moderate (within 5°F or 20% RH): Alert user and offer automatic adjustment
   - Critical (beyond 5°F or 20% RH): Immediate alert with action needed

2. **Send appropriate notification**:
   - Minor: "Bedroom is trending warmer than your preference. Want me to cool it to 68°F?"
   - Moderate: "Bedroom humidity is at 65% but you prefer 50-60%. Shall I run the dehumidifier?"
   - Critical: "Living room temperature dropped to 62°F — well below your 70°F preference. Adjusting now."

3. **Store the deviation** as a volatile memory fact:
   - `subject`: Room name
   - `predicate`: `comfort_deviation`
   - `content`: Description of what deviated, when, and why (if known)
   - `permanence`: `volatile`
   - `importance`: 6-7 (depends on severity)
   - `tags`: Room, metric, severity level

4. **Take corrective action** (if user has previously authorized automatic adjustments):
   - Call device commands to adjust HVAC, humidifier, lights, etc.
   - Confirm action: "Adjusted bedroom temperature. Now cooling to 68°F."

### Step 4: Respond to Comfort Queries

When user asks about current conditions (e.g., "Is the bedroom too warm?"):

1. **Call `environment_get_reading()`** for the requested room
2. **Call `environment_get_comfort_preference()`** to get targets
3. **Compare current vs. preferred** and respond via `notify()` (answer mode):
   - Provide current readings with context
   - Indicate if conditions match preference or deviate
   - Suggest adjustments if needed
   - Example: "Bedroom is at 71°F and 52% humidity — both in your comfort range. Your target is 68-72°F."

### Step 5: Adjust Preferences Seasonally

When seasonal changes occur or user updates preferences:

1. **Parse the update**: "I want the bedroom warmer in winter, around 70°F"
2. **Call `environment_set_comfort_preference()`** with seasonal time_period (e.g., "winter", "summer")
3. **Update memory fact** or create new seasonal variant
4. **Confirm with user** and explain when it takes effect

## Key Behaviors

### One Metric Per Session

Keep comfort sessions focused on a single metric or room:
- Good: "Set bedroom temperature to 68°F at night"
- Avoid: "Set bedroom to 68°F, living room to 72°F, humidity to 50%, and lights to 80%"

### Conservative Bounds

Always set preference ranges (not single values) to allow for natural fluctuation:
- Good: `temperature_min=67, temperature_max=69` (68°F preference with ±1°F tolerance)
- Avoid: `temperature=68` (exact match unrealistic)

### Time-Aware Preferences

Use time contexts to create recurring preferences:
- Morning (6am-9am): bright lighting, cool temperature for wakefulness
- Daytime (9am-6pm): moderate lighting, moderate temperature
- Evening (6pm-10pm): dimmer lighting, warming temperature
- Night (10pm-6am): minimal/no lighting, cool temperature for sleep

### Avoid Over-Automation

Do not automatically adjust without user consent first. Always offer suggestions and wait for ✅
before making changes, unless user has explicitly granted standing authorization for a specific metric.

## Exit Criteria

- `environment_set_comfort_preference()` was called (for preference setting)
- `environment_get_reading()` and `environment_check_deviation()` were called (for monitoring)
- `memory_store_fact()` was called to persist the preference or deviation
- User has been notified of the preference, condition, or adjustment via `notify()`
- Session exits without starting a new skill/workflow

## Common Failure Modes and Recovery

### User Gives Vague Preference ("Make it more comfortable")
- Ask for specifics via follow-up: "Which room? And which metric — temperature, lighting, or humidity?"
- Example: "Bedroom" + "Too warm" → Set temperature preference lower

### User's Preference Conflicts with Another User's
- Store both preferences with identifiers
- Implement compromise scheduling (e.g., primary user's preference 7am-9pm, secondary's 9pm-7am)
- Alert both parties to conflicts

### Device Cannot Fulfill Preference
- Alert user: "Bedroom target is 68°F but thermostat reports it can only cool to 70°F"
- Suggest alternatives or technical troubleshooting
