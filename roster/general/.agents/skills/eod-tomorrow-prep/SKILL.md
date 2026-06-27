---
name: eod-tomorrow-prep
description: Daily tomorrow-prep briefing, review tomorrow's calendar and fold in today's cross-butler specialist highlights into a multi-domain preparation summary sent via Telegram (sent at the end of the day, 23:00 SGT, to prepare for the day ahead)
trigger_patterns:
  - eod-tomorrow-prep
  - scheduled daily at 23:00 SGT
---

# Tomorrow Prep

This skill runs every day at 23:00 SGT (UTC+8) as a scheduled task. It reviews
tomorrow's calendar, folds in today's cross-butler specialist highlights, and sends
a structured multi-domain preparation summary to the user via Telegram. The message
is sent at the end of the day to prepare the user for the day ahead. Frame it as
"Tomorrow Prep" and title it with tomorrow's date so the future date is expected.
Do not call it an "end-of-day briefing".

The specialist highlights come from the `collect_briefing_contributions` job (cron
`58 6 * * *`, which is 14:58 SGT), which aggregates each specialist butler's daily
contribution into a combined payload under `briefing/combined/<today-SGT>` earlier
the same day. This skill reads that payload and surfaces the domains that have
updates. When no payload exists, it degrades gracefully to a calendar-only summary.

## When to Use

Triggered automatically by the `eod-tomorrow-prep` scheduled task (cron: `0 15 * * *`,
which is 23:00 SGT).

## Workflow

### Step 1: Determine Today's and Tomorrow's Dates (SGT, UTC+8)

Compute both today's and tomorrow's dates in Singapore Time (UTC+8). Today's date
keys the specialist payload lookup; tomorrow's date frames the calendar timeline.
Date string format is `YYYY-MM-DD`. All event times throughout this skill are
expressed in SGT.

```python
from datetime import datetime, timedelta, timezone

SGT = timezone(timedelta(hours=8))
now_sgt = datetime.now(SGT)
today_sgt = now_sgt.strftime("%Y-%m-%d")
tomorrow_sgt = now_sgt + timedelta(days=1)

start = tomorrow_sgt.replace(hour=0, minute=0, second=0, microsecond=0)
end   = tomorrow_sgt.replace(hour=23, minute=59, second=59, microsecond=0)
```

### Step 2: Fetch Specialist Highlights (Briefing Contributions)

Call `state_get('briefing/combined/<today-SGT>')`, where `<today-SGT>` is today's
date in `YYYY-MM-DD` format (SGT). This returns a JSON payload with a
`contributions` list. Each entry has these fields:

- `butler`: the specialist butler that produced the contribution
- `has_updates`: boolean, whether this specialist has anything to report today
- `highlights`: structured highlight data
- `summary`: a pre-rendered one-line summary string for direct inclusion

```python
combined = state_get(f"briefing/combined/{today_sgt}")
contributions = (combined or {}).get("contributions", [])
```

If `state_get` returns null or empty, proceed in **calendar-only mode**: skip the
"Today's Highlights" section entirely and build the summary from the calendar alone.

### Step 3: Fetch Tomorrow's Events

Call `calendar_list_events` with the computed start and end bounds (SGT, 00:00 to
23:59 tomorrow):

```python
events = calendar_list_events(
    start=start.isoformat(),
    end=end.isoformat(),
)
```

For each event, extract title, start time (SGT), duration, location, and any
description notes. Sort events by start time (ascending) to build a chronological
timeline.

### Step 4: Compose the Multi-Domain Summary

Open with a title line that makes clear this previews the day ahead, using
TOMORROW's weekday and date so the future date reads as intentional:

```
**🌅 Tomorrow Prep — [Tomorrow's Weekday, Date]**
```

Do not title this an "end-of-day briefing"; it is sent at the end of the day,
23:00 SGT, to prepare for tomorrow.

Then assemble the body sections:

**Calendar — [Day, Date]**
List tomorrow's events in chronological order (times in SGT). For each event:
title, time, duration, location. Include prep notes (documents to review, travel
time, items to bring), inferred from the event title, description, and location.
Note any free blocks between events. If there are no events:
"No events scheduled for tomorrow."

**Today's Highlights**
Include this section **only** if at least one specialist contribution has
`has_updates=true`. Omit the section entirely if no contributions are present or all
have `has_updates=false` (calendar-only mode).

For each specialist domain with `has_updates=true`, include one concise line using
the pre-rendered `summary` field. Group lines under these fixed domain labels (one
label per specialist butler, in the order specialists are declared in
`src/butlers/jobs/briefing.py::SPECIALIST_BUTLERS`):

```
Learning (education) · Finance · Health · Home · Lifestyle · Relationships · Travel
```

**Heads-up** (optional)
Include only when two or more high-priority highlights across different domains
suggest a cross-domain conflict or compounded risk (for example, a travel departure
that overlaps a medical appointment). Keep it to one or two sentences maximum.

### Step 5: Length Check

Ensure the full message is under 500 words for mobile readability. Trim event prep
notes or specialist summaries if needed to stay within the limit. The calendar
timeline takes priority over the Highlights section when trimming.

### Step 6: Send via notify()

Send the composed message using `intent="send"` (a proactive outbound notification,
not a reply). Scheduled prompt sessions have no interactive user, so `notify()` is
the only way to reach the user, and `request_context` is not required for `intent="send"`:

```python
notify(
    channel="telegram",
    intent="send",
    message=<composed_message>,
)
```

Do not wait for a user reply.

## Exit Criteria

- Today's and tomorrow's dates determined in SGT (UTC+8)
- `state_get('briefing/combined/<today-SGT>')` attempted; specialist contributions
  used when present, calendar-only fallback when absent or empty
- `calendar_list_events` called with correct SGT start/end bounds for tomorrow
- Calendar timeline composed with chronological events, prep notes, and free blocks
- "Today's Highlights" included only when at least one contribution has
  `has_updates=true`, grouped under the fixed domain labels
- Optional "Heads-up" included only for cross-domain conflicts
- Full message under 500 words; calendar prioritized over Highlights when trimming
- `notify(channel="telegram", intent="send", ...)` called once
- Session exits after delivery
