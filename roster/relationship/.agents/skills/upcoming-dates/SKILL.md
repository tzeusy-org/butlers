---
name: upcoming-dates
description: Daily scheduled task (8am) — check for important dates (birthdays, anniversaries) in the next 7 days, draft reminder messages, and deliver via notify.
version: 1.0.0
tags: [relationship, scheduling, dates, reminders]
---

# Upcoming Dates Check

Scheduled daily at 8am. Check for important dates in the next 7 days and draft reminder messages for delivery via Telegram.

## Purpose

Proactively surface upcoming birthdays, anniversaries, and other important dates so the user can prepare and reach out to contacts at the right time.

## Tool Sequence

### Step 1: Query Upcoming Dates

```python
upcoming_dates(days_ahead=7)
```

Returns a list of upcoming dates across all contacts within the next 7 days.

### Step 2: Enrich Each Date Entry

For each date returned, gather context to make the reminder message meaningful:

```python
# Get contact details
contact_get(contact_id="<contact_id>")

# Pull recent notes for personal context
note_list(contact_id="<contact_id>", limit=3)

# Check interaction history
interaction_list(contact_id="<contact_id>", limit=1)

# Check if there's a pending gift idea for this occasion
gift_list(contact_id="<contact_id>")
```

### Step 3: Check for Existing Reminders

Avoid creating duplicate reminders for dates already acknowledged:

```python
reminder_list(contact_id="<contact_id>")
```

Skip drafting a reminder if an active (non-dismissed) reminder already exists for this date.

### Step 4: Draft Reminder Messages

For each upcoming date without an existing reminder, compose a personalized message:

**Birthday format:**
```
[Name]'s birthday is [date] ([X days away]).
[Context: last interaction summary, recent note if relevant, pending gift if any]
```

**Anniversary format:**
```
[Name and user's] [X-year] anniversary is [date] ([X days away]).
[Context if relevant]
```

**Other date format:**
```
[Date label] for [Name] is [date] ([X days away]).
```

### Step 5: Deliver via notify

Send the compiled reminder digest to the user:

```python
notify(
    channel="telegram",
    message="<compiled reminder digest>",
    intent="send"
)
```

If there are no upcoming dates in the next 7 days, skip the notify call entirely (no empty messages).

## Message Format

When multiple dates are upcoming, compile them into a single digest message rather than sending one message per date:

```
Upcoming dates this week:

- Sarah's birthday is tomorrow (March 15). You last talked 3 weeks ago — she was excited about her pottery class.
- John and Alex's 5-year friendiversary is Friday. Consider reaching out.
- Mom's birthday is in 5 days (March 19). Gift idea on record: noise-canceling headphones.
```

## Edge Cases

- **Today's date**: If a date is today, use "today" not "0 days away"
- **Tomorrow**: Use "tomorrow" for clarity
- **No upcoming dates**: Do not send a message — silence is correct behavior
- **Already sent reminder**: Check `reminder_list` to avoid duplicate notifications
- **Multiple dates for same contact**: Group them together in the digest

## Integration Notes

- This skill is triggered by the `upcoming-dates-check` schedule entry in `butler.toml` (cron: `0 8 * * *`)
- The `upcoming_dates` tool queries across all contacts in the relationship schema
- Use `date_list(contact_id)` if you need to pull dates for a specific contact directly
- After sending the digest, optionally create reminders for each date so the user can dismiss them individually
