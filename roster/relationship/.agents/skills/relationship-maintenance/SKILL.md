---
name: relationship-maintenance
description: Weekly scheduled task (Mon 9am) — identify contacts not interacted with in 30+ days, recall context, and suggest 3 people to reach out to this week via notify.
version: 1.0.0
tags: [relationship, scheduling, maintenance, outreach]
---

# Relationship Maintenance

Scheduled weekly on Monday at 9am. Review contacts not interacted with in 30+ days and suggest 3 people to reach out to this week, with context on last interaction and upcoming dates.

## Purpose

Prevent relationships from going stale through proactive, contextual reconnection suggestions delivered at the start of each week.

## Tool Sequence

### Step 1: Find Stale Contacts

Query for contacts with no recent interaction:

```python
# Get contacts with last interaction older than 30 days
interaction_list(stale_days=30)
```

If the tool doesn't support `stale_days` directly, retrieve all contacts and filter by last interaction date:

```python
contact_search(limit=100)
# For each contact:
interaction_list(contact_id="<contact_id>", limit=1)
# Filter: keep contacts where last interaction is >30 days ago or never
```

### Step 2: Gather Context for Each Stale Contact

For each stale contact, collect context to support a meaningful outreach suggestion:

```python
# Last interaction summary
interaction_list(contact_id="<contact_id>", limit=1)

# Recall relevant facts from memory
memory_recall(topic="<contact_name>", limit=5)

# Check for upcoming dates (birthday, anniversary)
upcoming_dates(contact_id="<contact_id>", days_ahead=30)

# Check for pending gift ideas
gift_list(contact_id="<contact_id>")

# Recent notes
note_list(contact_id="<contact_id>", limit=3)
```

### Step 3: Score and Prioritize

Rank stale contacts by reconnection priority:

**Priority signals** (highest to lowest):
1. Upcoming birthday or anniversary within 14 days
2. Pending gift idea or open reminder
3. Relationship tier: close friends and family > friends > acquaintances
4. Staleness severity: contacts longest overdue rank higher
5. Positive emotional context in recent notes (suggests good relationship to maintain)

Select the **top 3** contacts to suggest. If fewer than 3 contacts are stale, suggest only the stale ones.

### Step 4: Compose Outreach Suggestions

For each of the top 3 contacts, draft a personalized suggestion:

**Template — upcoming date hook:**
```
Reach out to [Name] — their [birthday/anniversary] is in [X days].
Last talked: [date, summary]. Consider: [personal hook from memory/notes].
```

**Template — follow-up on previous conversation:**
```
Check in with [Name] — it's been [X days] since you [last interaction summary].
Hook: [relevant fact or note that gives a natural reason to reach out].
```

**Template — general reconnection:**
```
Reconnect with [Name] — [X days] since last contact.
[One sentence of context: shared interest, recent life event, or pending item].
```

### Step 5: Deliver via notify

Send the weekly suggestions as a single message:

```python
notify(
    channel="telegram",
    message="<weekly outreach suggestions>",
    intent="send"
)
```

## Message Format

```
Weekly relationship check-in:

1. Alice Chen — birthday in 5 days (Feb 15). You last talked 6 weeks ago about her new job. Consider sending a message early.

2. Bob Martinez — 45 days since your last coffee meeting. He was working on a marathon training plan — good conversation starter.

3. Carol Lee — 8 weeks since last contact. You mentioned wanting to catch up over dinner. She's in the same neighborhood now.

Want me to set reminders for any of these?
```

## Edge Cases

- **Fewer than 3 stale contacts**: Suggest only those that qualify; don't pad with non-stale contacts
- **No stale contacts**: Send a brief positive message: "All your key relationships are up to date — no overdue check-ins this week."
- **Contact has active reminder**: Note it in the suggestion rather than creating a duplicate
- **Opted-out contacts**: Respect any "do not suggest" labels or facts on a contact

## Integration Notes

- This skill is triggered by the `relationship-maintenance` schedule entry in `butler.toml` (cron: `0 9 * * 1`)
- Works alongside the `reconnect-planner` skill, which handles on-demand staleness checks
- After the user selects contacts to reach out to, use `reminder_create` to set follow-up reminders
- Log completed outreach with `interaction_log` when the user confirms they made contact
