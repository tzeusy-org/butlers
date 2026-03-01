# Skill: Weekly Energy Digest

## Purpose

Generate and send a weekly energy efficiency digest every Sunday at 9am. Analyze device energy
consumption from the past 7 days, identify top consumers, compare against stored baselines,
and compose a structured summary with recommendations. Deliver via
`notify(channel="telegram", intent="send")`.

## When to Use

Use this skill when:
- The `weekly-energy-digest` scheduled task fires (cron: `0 9 * * 0`, Sundays at 09:00)
- User requests "send me the weekly energy report" or similar

## Workflow

### Step 1: Discover Energy Sensors

1. Call `ha_list_entities(domain="sensor")` to get all sensor entities.
2. Filter results for energy-related entities — look for entity IDs or friendly names containing
   `energy`, `power`, `kwh`, `consumption`, `watt` (case-insensitive).
3. Build a list of `statistic_ids` for the top-level energy meter and per-device sensors
   (e.g., `["sensor.energy_total_kwh", "sensor.hvac_energy", "sensor.water_heater_energy",
   "sensor.kitchen_energy"]`).

### Step 2: Retrieve Weekly Energy Data

Call `ha_get_statistics()` to get the past 7 days of energy data:

```
ha_get_statistics(
    statistic_ids=<energy_sensor_ids>,
    start=<7 days ago, ISO 8601, midnight>,
    end=<now, ISO 8601>,
    period="day"
)
```

This returns daily aggregated statistics (min, max, mean, sum) per sensor.

For each device sensor, also call with `period="week"` for a single aggregate total:

```
ha_get_statistics(
    statistic_ids=<per_device_sensor_ids>,
    start=<7 days ago, ISO 8601, midnight>,
    end=<now, ISO 8601>,
    period="week"
)
```

### Step 3: Retrieve Baselines from Memory

Call `memory_recall(subject="energy", predicate="energy_baseline")` to retrieve stored baseline
consumption patterns.

Also search for device-specific baselines:
- `memory_recall(subject="hvac", predicate="energy_baseline")`
- `memory_recall(subject="water-heater", predicate="energy_baseline")`
- Any device baselines stored previously

### Step 4: Compute Top Consumers and Trends

From the weekly statistics:

1. **Rank devices by total consumption** (sum over the week). Identify the top 5 consumers.
2. **Calculate percentage share** of each device relative to total consumption.
3. **Compare against baselines**:
   - Is this week's total higher or lower than typical?
   - Are any devices consuming 20%+ above their baseline? (anomaly)
   - What was the peak consumption day?
4. **Identify anomalies** (flag if present):
   - Device consuming 2x or more than baseline (high severity)
   - Device consuming 20-50% above baseline (medium severity)
   - Unexpected always-on consumption (low but persistent)

### Step 5: Generate Recommendations

Based on top consumers and anomalies, compose 2-3 actionable recommendations:

- High HVAC usage: "Consider lowering the heating setpoint by 2°F at night"
- Water heater anomaly: "Water heater ran longer than usual — check for leaks or tank issues"
- Standby waste: "TV and chargers in standby mode use ~5W continuously"

Limit to 2-3 recommendations. Prioritize by impact.

### Step 6: Store Energy Patterns in Memory

After analysis, persist key findings:

```python
memory_store_fact(
    subject="energy",
    predicate="energy_baseline",
    content="Weekly consumption: <X> kWh total. Top consumers: HVAC (<Y>%), water heater (<Z>%)",
    permanence="standard",
    importance=6.0,
    tags=["energy", "weekly-digest", "baseline"]
)
```

If an anomaly was detected:

```python
memory_store_fact(
    subject=<device_name>,
    predicate="energy_spike",
    content="<device> consumed <X> kWh this week — <Y>% above baseline",
    permanence="volatile",
    importance=7.5,
    tags=["energy", "anomaly", <device_name>]
)
```

### Step 7: Compose and Send the Digest

Format the digest as a structured message:

```
Weekly Energy Digest — [Date range, e.g. "Feb 17-23"]

Total: [X] kWh  ([+/-Y]% vs. typical)
Peak day: [Weekday] with [Z] kWh

Top Consumers:
1. HVAC — [X] kWh ([Y]%)
2. Water Heater — [X] kWh ([Y]%)
3. Kitchen Appliances — [X] kWh ([Y]%)
4. Washer/Dryer — [X] kWh ([Y]%)
5. Lighting — [X] kWh ([Y]%)

[Anomaly alert if present:]
  Water heater ran 6h longer than usual on Tuesday — possible tank issue.

Recommendations:
• [Recommendation 1]
• [Recommendation 2]
[• Recommendation 3 if applicable]

Savings vs. baseline: [X] kWh saved / [Y]% below average (or: usage is on target)
```

Send via:

```python
notify(
    channel="telegram",
    intent="send",
    subject="Weekly Energy Digest — [Date range]",
    message=<formatted_digest>,
)
```

Use `intent="send"` — this is a scheduled proactive delivery, not a reply.

## Exit Criteria

- `ha_list_entities(domain="sensor")` called to discover energy sensors
- `ha_get_statistics()` called for weekly period to retrieve consumption data
- `memory_recall()` called to retrieve stored energy baselines
- Top 5 consumers ranked; anomalies identified
- 2-3 recommendations generated
- `memory_store_fact()` called to update energy baseline and any anomaly facts
- Digest composed and sent via `notify(channel="telegram", intent="send")`
- Session exits — no interactive follow-up in this session

## Common Failure Modes

### No Energy Sensors Found
- Alert via `notify(channel="telegram", intent="send")`: "Could not find energy sensors in Home Assistant. Weekly digest
  unavailable. Check that energy monitoring is configured in HA."
- Exit cleanly.

### Partial Data (Sensor Gaps)
- Include note in digest: "Note: [device] sensor was offline [N] hours this week. Data for that
  period is estimated."
- Do not skip the digest — deliver with the available data.

### No Stored Baselines Yet
- This may be the first digest. Compose digest without trend comparison.
- Store today's data as the initial baseline for future comparison.
- Note in digest: "This is your first energy digest — we'll compare against this baseline next week."
