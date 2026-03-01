# Skill: Device Health Check

## Purpose

Perform a nightly device health survey at 4am. Query all connected Home Assistant entities for
online status, battery levels, and last communication time. Flag offline devices, critically low
batteries, and devices due for firmware updates. Store findings in memory and send an alert via
`notify(channel="telegram", intent="send")` — alert if issues found, all-clear summary otherwise.

## When to Use

Use this skill when:
- The `device-health-check` scheduled task fires (cron: `0 4 * * *`, nightly at 04:00)
- User requests "run the device health check" or "check device status"

## Workflow

### Step 1: Survey All Entities

Call `ha_list_entities()` (no domain filter) to get the complete list of entities.

Identify entities with problematic states:
- **Offline/unavailable**: state is `"unavailable"` or `"unknown"`
- **Battery sensors**: entity IDs or friendly names containing `battery` or `battery_level`
- **Low battery**: battery sensor value <= 20%

Build three categorized lists from the results:
1. `offline_entities`: entities with state `unavailable` or `unknown`
2. `low_battery_entities`: battery sensors with value <= 20%
3. `critical_battery_entities`: battery sensors with value <= 10%

### Step 2: Get Battery Levels for Battery-Powered Devices

For each entity identified as a battery sensor (step 1), call:

```python
ha_get_entity_state(entity_id=<battery_sensor_entity_id>)
```

Classify by urgency:
- **Critical** (<= 10%): "will fail within hours/days — alert required"
- **High** (11-20%): "replace within days"
- **Medium** (21-30%): "plan to replace within a week"

### Step 3: Classify Findings by Severity

Classify each issue:

| Condition | Severity | Action |
|---|---|---|
| Device offline > 24h (check `last_changed`) | Critical | Alert |
| Device offline < 24h | Warning | Store, include in next digest |
| Battery <= 10% | Critical | Alert |
| Battery 11-20% | Warning | Store, include in alert summary |
| Battery 21-30% | Info | Store for trend tracking |
| Entity state: unknown (transient) | Info | Store if recurring |

### Step 4: Store Findings in Memory

For each issue found, store a memory fact:

**Critical/high battery:**
```python
memory_store_fact(
    subject=<device_friendly_name>,
    predicate="device_issue",
    content="battery at <X>% — needs replacement <urgency>",
    permanence="volatile",
    importance=8.0 if critical else 6.5,
    tags=["maintenance", "battery", "urgent" if critical else "warning"]
)
```

**Offline device:**
```python
memory_store_fact(
    subject=<device_friendly_name>,
    predicate="device_issue",
    content="device offline — state is <unavailable/unknown> since <last_changed>",
    permanence="volatile",
    importance=7.5,
    tags=["offline", "maintenance", "urgent"]
)
```

**Healthy check (no issues):**
```python
memory_store_fact(
    subject="device-fleet",
    predicate="device_issue",
    content="health check passed — all <N> devices online, no low battery",
    permanence="volatile",
    importance=3.0,
    tags=["health-check", "healthy"]
)
```

### Step 5: Compose and Send Alert (if Critical Issues Found)

If any critical or high-severity issues were found, compose and send an alert:

```
Device Health Check — [Date]

Issues Requiring Attention:
  [device name] battery at 8% — replace soon (critical)
  [device name] offline since [time] — check power or connectivity

Warnings (non-urgent):
  [device name] battery at 15% — replace within a few days
  [device name] battery at 18% — plan to replace this week

[N] other devices checked — all healthy.
```

Send via:

```python
notify(
    channel="telegram",
    intent="send",
    subject="Device Health Alert — [N] issues found",
    message=<formatted_alert>,
)
```

Use `intent="send"` — this is a scheduled proactive delivery, not a reply.

**If no critical issues found**, send a brief status update:

```python
notify(
    channel="telegram",
    intent="send",
    subject="Device Health Check — All Clear",
    message="Nightly check complete. All [N] devices online. No low battery alerts.",
)
```

## Exit Criteria

- `ha_list_entities()` called to survey all entities
- `ha_get_entity_state()` called for each battery sensor identified
- Findings classified by severity (critical, warning, info)
- `memory_store_fact()` called for each issue (and for a clean-bill-of-health if no issues)
- `notify(channel="telegram", intent="send")` called — either with alert (if issues) or all-clear summary
- Session exits — no interactive troubleshooting in this session (that is handled by the
  `troubleshooting` skill when user follows up)

## Common Failure Modes

### `ha_list_entities()` Returns Empty or Fails
- Store an error fact: `memory_store_fact(subject="device-fleet", predicate="device_issue", content="health check failed — ha_list_entities returned no results", permanence="volatile", ...)`
- Send alert: "Device health check could not complete — Home Assistant may be unavailable."

### Too Many Devices to Check Individually
- Prioritize entities in `unavailable` or `unknown` state from `ha_list_entities()` (state is
  included in the listing response).
- Call `ha_get_entity_state()` only for battery sensors and offline devices — not for all entities.
- The listing result is sufficient to identify problems without individual state calls for healthy
  devices.

### Transient Unavailability (Device Comes Back Quickly)
- If `last_changed` shows the entity returned to normal very recently (within 30 minutes), classify
  as Info rather than Warning.
- Still store the fact, but lower importance: `importance=4.0`.
