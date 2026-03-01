---
name: upcoming-travel-check
description: Scheduled task companion skill for the daily 08:00 pre-trip scan. Surfaces departures and check-ins within 48 hours, classifies actions by urgency, and sends a proactive alert via notify(intent="send").
version: 1.0.0
tools_required:
  - upcoming_travel
  - trip_summary
  - notify
---

# Upcoming Travel Check

## Purpose

This skill is the companion for the daily `upcoming-travel-check` scheduled task (cron: `0 8 * * *`). It performs a 48-hour pre-trip scan, classifies outstanding actions by urgency, and sends a proactive notification if anything requires attention. If nothing is upcoming or all actions are resolved, no notification is sent.

## Trigger Context

Triggered by the daily `upcoming-travel-check` schedule at 08:00. There is no incoming message or user context — all notifications use `intent="send"`.

## Tool Sequence

### Step 1: Retrieve Upcoming Travel

```python
result = upcoming_travel(within_days=2, include_pretrip_actions=True)
```

`result` contains a list of upcoming departures and check-ins within the next 48 hours, each with associated pre-trip actions.

If the result is empty (no upcoming travel), **stop here — do not send a notification**.

### Step 2: Classify Actions by Urgency

For each item returned by `upcoming_travel`, classify its pre-trip actions into urgency tiers:

**High (block or notify immediately)**
- Missing boarding pass for a flight departing within 24 hours
- Online check-in window open but not completed (airline supports it, departure < 24h)
- Tight layover (< 60 minutes connection time) with no acknowledged gate info
- Trip has no confirmed hotel for an upcoming check-in night

**Medium (action needed today)**
- Online check-in window opens today (24h before departure)
- Missing boarding pass for a flight departing in 24–48 hours
- Unassigned seat on a flight departing within 48 hours
- No ground transport arranged for airport pickup or departure

**Low (informational, worth surfacing)**
- Hotel check-in approaching (no action required, just awareness)
- All documents attached and check-in completed — flight is fully prepared

### Step 3: Compose Pre-Trip Alert

Only compose and send a notification if there are High or Medium urgency actions. If only Low items exist and all are resolved, do not send.

Compose the alert message in this format:

```
Your [destination] trip starts [timeframe]!

✈ [Flight: Airline + Flight#, departure time, terminal/gate if known]
🏨 [Hotel: Name, check-in date/time if known]

[High urgency actions, each on its own line with ⚠️ prefix]
[Medium urgency actions, each on its own line with ⏳ prefix]
[Low urgency items, each on its own line with ✅ prefix]
```

Examples:
- `⚠️ Boarding pass not yet attached — check-in opens now`
- `⚠️ Tight layover: 45 min at ORD — watch for gate changes`
- `⏳ Online check-in opens today at 13:40 for UA 837`
- `⏳ No ground transport arranged for airport pickup`
- `✅ Boarding pass attached, seat 22A`

### Step 4: Send Notification

```python
notify(
    channel="telegram",
    message="<composed alert from Step 3>",
    intent="send",
)
```

Use `intent="send"` — this is a scheduled outbound notification, not a reply to an incoming message. Do not pass `request_context`.

## No-Op Path

If `upcoming_travel(within_days=2, include_pretrip_actions=True)` returns no results, or all returned items have only Low urgency actions with everything resolved, do not call `notify`. Exit cleanly with no output.

## Example: Full Execution (Action Found)

**Context**: Daily 08:00 trigger, Tokyo trip departs tomorrow at 13:40.

```python
# Step 1
result = upcoming_travel(within_days=2, include_pretrip_actions=True)
# → [{
#     "trip_id": "uuid-tokyo",
#     "destination": "Tokyo",
#     "departure_at": "2026-03-15T13:40:00-08:00",
#     "flight": "UA 837, SFO → NRT",
#     "hotel_checkin": "2026-03-16T15:00:00+09:00",
#     "hotel": "Shinjuku Granbell Hotel",
#     "pretrip_actions": [
#         {"type": "boarding_pass_missing", "urgency": "high"},
#         {"type": "seat_unassigned", "urgency": "medium"}
#     ]
# }]

# Step 2: classify
# boarding_pass_missing → High (departure < 24h, check-in open now)
# seat_unassigned → Medium

# Step 3: compose
message = """Your Tokyo trip starts tomorrow!

✈ UA 837 departs SFO at 13:40 (Terminal 3, PNR K9X4TZ)
🏨 Shinjuku Granbell Hotel — check-in March 16 at 15:00

⚠️ Boarding pass not yet attached — online check-in is open now
⏳ Seat unassigned — consider selecting one at check-in"""

# Step 4: send
notify(
    channel="telegram",
    message=message,
    intent="send",
)
```

## Example: No-Op Path

**Context**: Daily 08:00 trigger, no trips departing within 48 hours.

```python
result = upcoming_travel(within_days=2, include_pretrip_actions=True)
# → []

# No upcoming travel — exit without sending notification
```
