---
name: document-expiry-check
description: Scheduled task companion skill for the weekly Monday 09:00 travel document expiry scan. Checks all travel documents for expiry within 90 days, creates calendar reminders for <30 days, and sends an alert via notify(intent="send").
version: 1.0.0
tools_required:
  - list_trips
  - trip_summary
  - calendar_create_event
  - notify
---

# Document Expiry Check

## Purpose

This skill is the companion for the weekly `trip-document-expiry` scheduled task (cron: `0 9 * * 1`). It scans all planned trips for travel documents expiring within 90 days, creates calendar reminders for those expiring within 30 days, and sends a proactive alert if anything is approaching expiry. If all documents are current (> 90 days), no notification is sent.

## Trigger Context

Triggered by the weekly `trip-document-expiry` schedule on Monday at 09:00. There is no incoming message or user context — all notifications use `intent="send"`.

## Tool Sequence

### Step 1: List All Planned Trips

```python
trips = list_trips(status="planned")
```

If no planned trips are returned, check `status="active"` as well — active trips may still carry documents that need monitoring (e.g., passports, insurance).

```python
active_trips = list_trips(status="active")
all_trips = trips + active_trips
```

If both return empty lists, **stop here — no notification needed**.

### Step 2: Retrieve Document Details Per Trip

For each trip in `all_trips`:

```python
summary = trip_summary(trip_id=trip["trip_id"], include_documents=True)
```

Extract the `documents` field from each summary. Each document has:
- `type`: e.g., `"passport"`, `"visa"`, `"travel_insurance"`, `"boarding_pass"`
- `expiry_date`: ISO date string (may be null for non-expiring documents like boarding passes)
- `metadata`: dict with additional context (e.g., `{"holder": "user", "policy_number": "..."}`)

### Step 3: Filter Documents by Expiry Window

For each document with a non-null `expiry_date`:

1. Calculate `days_until_expiry = expiry_date - today`
2. Classify:
   - `days_until_expiry < 0`: **Expired** (critical)
   - `0 <= days_until_expiry < 30`: **Critical** (< 30 days)
   - `30 <= days_until_expiry < 90`: **Warning** (< 90 days)
   - `days_until_expiry >= 90`: Current — skip

Skip documents with no expiry date (boarding passes, hotel confirmations, etc.).

### Step 4: Create Calendar Reminders for <30 Day Documents

For each document classified as **Critical** (< 30 days until expiry):

Check if a calendar reminder already exists for this document expiry. If not:

```python
calendar_create_event(
    title="⚠️ [Document type] expires in [N] days",
    start_at="<30 days before expiry, 09:00 local time>",
    end_at="<30 days before expiry, 09:30 local time>",
    description="Document: [type] | Expiry: [expiry_date] | Trip: [trip destination]",
)
```

For **Expired** documents, create an immediate reminder (today at 09:00) if not already present:

```python
calendar_create_event(
    title="🚨 [Document type] HAS EXPIRED",
    start_at="<today, 09:00 local time>",
    end_at="<today, 09:30 local time>",
    description="Document: [type] | Expired: [expiry_date] | Trip: [trip destination] — immediate action required",
)
```

### Step 5: Compose Expiry Alert

Only compose a notification if at least one document is in the **Critical** or **Warning** window.

Format:
```
Travel document expiry alert:

[For each expired document:]
🚨 [Document type] for [trip destination] EXPIRED on [date] — immediate action required

[For each critical document (< 30 days):]
⚠️ [Document type] for [trip destination] expires in [N] days ([date])

[For each warning document (30–90 days):]
📋 [Document type] for [trip destination] expires in [N] days ([date])

[If reminders were created:]
Calendar reminders have been set for critical items.
```

### Step 6: Send Notification

```python
notify(
    channel="telegram",
    message="<composed alert from Step 5>",
    intent="send",
)
```

Use `intent="send"` — this is a scheduled outbound notification, not a reply to an incoming message. Do not pass `request_context`.

## No-Op Path

If no planned or active trips are found, or all documents have `expiry_date >= 90 days from today`, do not call `notify`. Exit cleanly.

## Example: Full Execution (Expiring Documents Found)

**Context**: Weekly Monday scan, user has a Tokyo trip with a visa expiring in 45 days and insurance expiring in 20 days.

```python
# Step 1
trips = list_trips(status="planned")
# → [{"trip_id": "uuid-tokyo", "destination": "Tokyo", "departure_at": "2026-03-15"}]

# Step 2
summary = trip_summary(trip_id="uuid-tokyo", include_documents=True)
# → {
#     "documents": [
#         {"type": "visa", "expiry_date": "2026-04-14", "metadata": {"country": "Japan"}},
#         {"type": "travel_insurance", "expiry_date": "2026-03-20", "metadata": {"provider": "TravelGuard", "policy_number": "TG-991234"}},
#         {"type": "boarding_pass", "expiry_date": None, "metadata": {"flight": "UA 837"}}
#     ]
# }

# Step 3: filter
# visa: 45 days → Warning
# travel_insurance: 20 days → Critical
# boarding_pass: no expiry → skip

# Step 4: calendar reminder for insurance (Critical, < 30 days)
calendar_create_event(
    title="⚠️ Travel insurance expires in 20 days",
    start_at="2026-02-28T09:00:00",
    end_at="2026-02-28T09:30:00",
    description="Document: travel_insurance | Expiry: 2026-03-20 | Trip: Tokyo — TravelGuard policy TG-991234",
)

# Step 5: compose
message = """Travel document expiry alert:

⚠️ Travel insurance for Tokyo expires in 20 days (2026-03-20) — renew or extend
📋 Japan visa for Tokyo expires in 45 days (2026-04-14)

Calendar reminder set for travel insurance expiry."""

# Step 6: send
notify(
    channel="telegram",
    message=message,
    intent="send",
)
```

## Example: No-Op Path

**Context**: Weekly Monday scan, no planned or active trips.

```python
trips = list_trips(status="planned")
# → []
active_trips = list_trips(status="active")
# → []

# No trips — exit without sending notification
```
