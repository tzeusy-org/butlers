# Skill: Energy Awareness

## Purpose

Track and analyze home energy consumption to help user understand where energy goes, identify
inefficiencies, and make optimization decisions. Generate weekly energy digests with trends,
device rankings, and actionable recommendations. Alert on anomalies (unexpected spikes, always-on devices).

## When to Use

Use this skill when:
- User asks about energy consumption ("What's my energy usage?", "Which devices use the most?")
- System needs to analyze energy trends for weekly digest
- Anomalous consumption is detected (unusual spike, device left on unexpectedly)
- User wants optimization recommendations

## Workflow

### Step 1: Retrieve Energy Data

To answer any energy question:

1. **Determine the time period** from user's request or use defaults:
   - "Today" → current day
   - "This week" → last 7 days
   - "This month" → last 30 days
   - No context → use "this week" as default

2. **Call `energy_get_consumption()`** with:
   - `period`: Time period (today, this_week, this_month, custom date range)
   - `granularity`: Optional — hourly, daily, or summary (default: summary)

3. **Call `energy_get_devices_by_usage()`** to rank top consumers:
   - `period`: Same as above
   - `limit`: 5-10 devices (show top consumers)

4. **Call `energy_get_peak_times()`** to identify demand patterns:
   - `period`: Same as above

### Step 2: Analyze Against Baselines

Compare current consumption to historical patterns:

1. **Call `memory_recall()`** to retrieve stored energy baselines:
   - Look for `energy_baseline` facts about typical consumption by device or time

2. **Calculate deviations**:
   - Is this week higher/lower than typical?
   - Are peak times consistent with known patterns?
   - Are any devices consuming more than usual?

3. **Identify anomalies**:
   - Device always-on when it should be off (e.g., AC running in winter)
   - Unexpected spike at unusual time
   - Device consumption 20%+ above baseline

### Step 3: Store Energy Patterns as Memory

After analyzing consumption:

1. **Call `memory_store_fact()`** to persist key findings:
   - `subject`: Device name or "energy"
   - `predicate`: `energy_baseline`, `energy_spike`, or `energy_pattern`
   - `content`: Concise description of pattern or anomaly
   - `permanence`: `standard` (patterns may shift seasonally)
   - `importance`: 5-7 (depends on significance)
   - `tags`: Device names, time periods, severity (e.g., `["hvac", "baseline", "winter"]`)

2. **Example facts**:
   - "HVAC typically uses 40% of daily energy in winter, peaks 7-9am and 6-8pm"
   - "Water heater ran for 6 hours yesterday instead of usual 2 hours — possible malfunction"
   - "Dishwasher left on standby uses 5W constantly — minor waste"

### Step 4: Respond to Energy Queries

When user asks about consumption (e.g., "What's my energy usage today?"):

1. **Retrieve data** (Step 1)
2. **Analyze patterns** (Step 2)
3. **Compose response** via `notify()` (answer mode):
   - **Current usage**: "Today: 18 kWh" or "This week: 120 kWh"
   - **Top consumers**: "Top 3: HVAC (40%), Water heater (25%), Kitchen appliances (15%)"
   - **Trend**: "8% below your weekly average" or "Up 12% due to unusually cold weather"
   - **Key insight**: Highlight any anomalies or seasonal patterns
   - **Optional suggestion**: "Want me to suggest ways to reduce water heater usage?"

### Step 5: Generate Weekly Energy Digest (Scheduled Task)

Run on schedule (e.g., Sunday 9am):

1. **Retrieve last 7 days of data** via `energy_get_consumption(period="this_week")`
2. **Retrieve peak times and device rankings**
3. **Analyze trends**:
   - Is consumption trending up or down?
   - Are peak times consistent?
   - Any anomalies this week?
   - Compared to previous week?

4. **Calculate efficiency metrics**:
   - Call `energy_get_efficiency_metrics()` for year-over-year or month-over-month comparison
   - Estimate cost savings achieved vs. baseline

5. **Generate recommendations**:
   - Call `energy_suggest_optimization()` for top energy consumers
   - Prioritize 2-3 actionable recommendations (not overwhelming)

6. **Compose digest** with sections:
   - **Weekly Summary**: Total kWh, trend (up/down/stable)
   - **Device Rankings**: Top 5 devices by consumption with % breakdown
   - **Highlights**: Notable patterns or anomalies
   - **Recommendations**: 2-3 actionable suggestions for optimization
   - **Savings Achieved**: Compare this week vs. baseline (if applicable)

7. **Send via `notify()`** (proactive intent):
   - Use formatted text or structure for readability
   - Include a call-to-action: "Want me to adjust thermostat schedules to save more?"

### Step 6: Handle Anomalies

When anomalous consumption is detected:

1. **Identify the anomaly**:
   - Unexpected device on (AC in winter, water heater running 6+ hours)
   - Unusual peak time (consumption spike at 2am)
   - Device consumption 20%+ above baseline

2. **Alert the user**:
   - Severity low: Include in weekly digest
   - Severity medium: Send proactive alert via `notify()`
   - Severity high: Immediate alert
   - Example: "Water heater ran for 6 hours yesterday (usually 2 hours). Check if tank malfunction or someone left a tap running."

3. **Store anomaly** as volatile memory fact:
   - `subject`: Device name
   - `predicate`: `energy_spike` or `device_issue`
   - `permanence`: `volatile`
   - `importance`: 7-8 (anomalies need attention)
   - `tags`: Device, severity, date

4. **Suggest investigation**:
   - "This is unusual. Should I check device status or would you like to investigate?"

### Step 7: Respond to Optimization Requests

When user asks for ways to reduce consumption:

1. **Call `energy_suggest_optimization()`** for:
   - Top consuming devices
   - Specific devices user is interested in
   - Time periods with peak demand

2. **Rank suggestions** by impact and effort:
   - High impact, low effort: Adjust thermostat setpoint
   - High impact, medium effort: Optimize hot water usage patterns
   - Medium impact, low effort: Switch to LED bulbs

3. **Compose response** with:
   - **Problem statement**: "HVAC uses 40% of your energy"
   - **Suggested actions**: 2-3 concrete steps (e.g., "Lower nighttime setpoint by 2°F")
   - **Expected savings**: "Estimated savings: 5-10% of HVAC usage ($3-6/month)"
   - **Call-to-action**: "Should I adjust thermostat schedules?"

4. **Get user consent** before implementing changes

## Key Behaviors

### Energy Queries Are Conversational

One metric per user message:
- Good: "What's my energy usage today?"
- Avoid: "What's my usage today, peak times, and top devices?" (ask one, offer others in response)

### Provide Context, Not Just Numbers

Never just say "18 kWh". Always provide context:
- vs. typical usage ("8% below average")
- vs. time ("lower than last week because weather was milder")
- vs. goal ("on track for monthly budget")

### Anomaly Severity Levels

- **Low** (include in digest): Standby power (TV, chargers using 1-2W)
- **Medium** (proactive alert): Device consuming 10-20% more than baseline
- **High** (immediate alert): Device consuming 2x baseline, always-on when should be off, or safety concern

### Avoid Overwhelming Recommendations

Give 2-3 suggestions max, ranked by impact. Offer to dive deeper if user wants.

### Connect to Comfort

Remember comfort preferences when suggesting optimizations:
- Don't suggest "lower heating to 62°F" if user prefers 70°F
- Suggest alternatives: "Running AC at night uses 30% less energy due to cooler outside temps. Want to try sleeping cooler?"

## Exit Criteria

- `energy_get_consumption()` was called to retrieve usage data
- `energy_get_devices_by_usage()` and/or `energy_get_peak_times()` were called
- `memory_store_fact()` was called to persist baseline, pattern, or anomaly
- User has been notified via `notify()` with:
  - Current consumption OR
  - Weekly digest OR
  - Anomaly alert OR
  - Optimization recommendations
- Session exits without starting new workflow

## Common Failure Modes and Recovery

### User Asks About Future Optimization ("Will this save money?")
- Cannot predict precise savings without knowing actual changes
- Provide estimate based on baseline: "Lowering AC 2°F typically saves 3-5%. For you, that's roughly $2-4/month"
- Offer to track actual savings after change is implemented

### Energy Data Has Gaps
- Alert user: "Energy meter was offline from 2-4pm. Digest shows partial data for today."
- Skip that period or clearly mark as incomplete

### Multiple Users in Home Have Conflicting Goals
- Store separate baselines and preferences per user
- Suggest compromise schedules or prioritization
