---
name: eod-tomorrow-prep
description: Daily end-of-day briefing — review tomorrow's calendar and send a structured preparation summary via Telegram
trigger_patterns:
  - eod-tomorrow-prep
  - scheduled daily at 15:00 SGT
---

# End-of-Day Tomorrow Prep

This skill runs every day at 15:00 SGT (UTC+8) as a scheduled task. It reviews tomorrow's
calendar and sends a structured preparation summary to the user via Telegram.

## When to Use

Triggered automatically by the `eod-tomorrow-prep` scheduled task (cron: `0 15 * * *`).

## Workflow

### Step 1: Determine Tomorrow's Date (SGT, UTC+8)

Compute tomorrow's date in Singapore Time (UTC+8). All event times throughout this skill
are expressed in SGT.

```python
from datetime import datetime, timedelta, timezone

SGT = timezone(timedelta(hours=8))
now_sgt = datetime.now(SGT)
tomorrow_sgt = now_sgt + timedelta(days=1)

start = tomorrow_sgt.replace(hour=0, minute=0, second=0, microsecond=0)
end   = tomorrow_sgt.replace(hour=23, minute=59, second=59, microsecond=0)
```

### Step 2: Fetch Tomorrow's Events

Call `calendar_list_events` with the computed start and end bounds (SGT):

```python
events = calendar_list_events(
    start=start.isoformat(),
    end=end.isoformat(),
)
```

### Step 3: Extract Event Details

For each event returned, extract:
- **Title**: event name
- **Time**: start time in SGT
- **Duration**: end time − start time (in minutes or hours)
- **Location**: venue or video link (if present)
- **Description/Notes**: any additional context from the event body

Sort the events by start time (ascending) to build a chronological timeline.

### Step 4: Compose the Summary

Build a structured Telegram message using the following template:

```
Tomorrow: [Weekday], [Date in SGT]

[HH:MM] [Event Title] ([Duration])
  📍 [Location if present]
  → Prep: [any docs to review, travel time estimate, items to bring — infer from description]

[HH:MM] [Free block] — [duration] free

[HH:MM] [Next Event Title] ([Duration])
  📍 [Location if present]
  → Prep: [preparation notes]

Heads-up:
• [Flag early starts (before 08:00)]
• [Flag back-to-back events (gap < 15 min)]
• [Flag travel-heavy days or unusual locations]
• [Flag unusually long events (> 3 hours)]
```

**Composition guidelines:**
- List events in chronological order (all times in SGT)
- Identify free blocks between events; highlight blocks ≥ 30 minutes as breathing room
- Infer preparation notes from the event description, title, and location — e.g. if a location
  is a physical address, note travel time; if a title mentions "review" or "demo", suggest
  preparing materials
- Keep the heads-up section concise — maximum 3–4 bullets
- Mobile-friendly: keep the total message under ~400 words

### Step 5: Handle the Empty-Calendar Case

If `calendar_list_events` returns zero events for tomorrow:

Send a brief note instead of a structured briefing:

```
Tomorrow: [Weekday], [Date in SGT]

No events scheduled — clear day ahead.
```

### Step 6: Send via notify()

Send the composed message using `intent="send"` (outbound, not a reply):

```python
notify(
    channel="telegram",
    intent="send",
    message=<composed_message>,
    request_context=<session_request_context>,
)
```

Do not wait for a user reply. This is a proactive outbound notification.

## Exit Criteria

- Tomorrow's date determined in SGT (UTC+8)
- `calendar_list_events` called with correct SGT start/end bounds
- All events extracted with title, time, duration, location, description
- Summary composed with chronological timeline, prep notes, free blocks, and heads-up
- `notify(channel="telegram", intent="send", ...)` called once
- Session exits after delivery
