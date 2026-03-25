---
name: relationship-maintenance
description: Weekly scheduled task (Mon 9am) — rank overdue contacts by Dunbar tier-weighted urgency and suggest top 3 reconnections via notify.
version: 2.0.0
tags: [relationship, scheduling, maintenance, outreach, dunbar]
---

# Relationship Maintenance

Scheduled weekly on Monday at 9am. Identify overdue contacts ranked by Dunbar
tier-weighted urgency and suggest the top 3 reconnections for the week.

## Purpose

Prevent relationships from going stale through proactive, tier-aware reconnection
suggestions delivered at the start of each week. Inner-circle contacts (tier 5, 15)
are surfaced with higher urgency — their shorter cadences reflect their importance.

## Tool Sequence

### Step 1: Get Overdue Contacts

```python
contacts_overdue()
```

This returns contacts enriched with `dunbar_tier`, `dunbar_score`, `effective_cadence`,
and `days_since_last_interaction`. Effective cadence is `stay_in_touch_days` if set,
otherwise the tier default (tier 5=14d, 15=21d, 50=45d, 150=120d, 500=270d).

Tier 1500 contacts with no `stay_in_touch_days` are excluded automatically.

### Step 2: Compute Urgency for Each Overdue Contact

For each overdue contact:

```
urgency = (days_since_last_interaction / effective_cadence) * tier_weight + context_bonus
```

**Tier weights:** 5→5.0, 15→3.0, 50→2.0, 150→1.0, 500→0.5

**Context bonus** (gather these for each contact):

```python
# Check upcoming dates
upcoming_dates(days_ahead=14)  # filter for this contact

# Check pending gifts
gift_list(contact_id="<contact_id>")  # +1.0 if any not yet 'given'

# Check recent notes for positive emotion
note_list(contact_id="<contact_id>", limit=1)  # +0.5 if positive emotion tag
```

**Bonus values:**
- +2.0 if contact has an upcoming date within 14 days
- +1.0 if contact has a pending gift (status not 'given')
- +0.5 if most recent note has positive emotional context

For contacts with `days_since_last_interaction = None` (never interacted),
use `effective_cadence * 10` as the numerator to treat them as maximally urgent.

### Step 3: Rank and Select Top 3

Sort by urgency descending. Take the top 3.

If fewer than 3 contacts are overdue, take all of them.

### Step 4: Gather Rich Context for Each

For each of the top 3 contacts:

```python
# Last interaction
interaction_list(contact_id="<contact_id>", limit=1)

# Key facts
fact_list(contact_id="<contact_id>")

# Notes
note_list(contact_id="<contact_id>", limit=3)
```

### Step 5: Compose Suggestions

**Template — upcoming date:**
```
Reach out to [Name] — [birthday/anniversary] in [X days].
Last talked [date] about [summary]. Consider: [personal hook].
```

**Template — follow-up:**
```
Check in with [Name] (tier [X] — [N] days overdue).
[Hook from memory/notes].
```

**Template — general:**
```
Reconnect with [Name] — [N] days since last contact.
[Context sentence].
```

### Step 6: Deliver via notify

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

1. Alice Chen (tier 5 — 28 days, cadence 14d, urgency 7.2)
   Birthday in 3 days. Last talked 4 weeks ago about her new job.
   → Send a birthday message early. Mention her new job.

2. Bob Martinez (tier 15 — 45 days, cadence 21d, urgency 3.1)
   Pending gift idea. He was working on a marathon.
   → Plan to give the gift and ask how the marathon went.

3. Carol Lee (tier 50 — 60 days, cadence 45d, urgency 1.8)
   You mentioned wanting to catch up over dinner.
   → Simple check-in, suggest dinner.

Want me to set reminders for any of these?
```

## Edge Cases

- **No overdue contacts**: Send "All your key relationships are up to date — no overdue check-ins this week."
- **Fewer than 3 overdue**: Suggest only those that qualify
- **Contact has active reminder**: Note it in the suggestion rather than creating a duplicate
- **Opted-out contacts**: Respect any "do not suggest" labels or facts on a contact
- **Dunbar tiers calibrating** (new user, few interactions): Note in suggestions that tier
  assignments will become more accurate as more interactions are logged

## Integration Notes

- This skill is triggered by the `relationship-maintenance` schedule entry in `butler.toml` (cron: `0 9 * * 1`)
- Works alongside the `reconnect-planner` skill, which handles on-demand staleness checks
- After the user selects contacts to reach out to, use `reminder_create` to set follow-up reminders
- Log completed outreach with `interaction_log` when the user confirms they made contact
- Use `dunbar_tier_set(contact_id, tier)` when the computed tier doesn't match reality
