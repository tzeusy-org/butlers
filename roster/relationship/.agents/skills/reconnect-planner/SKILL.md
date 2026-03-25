---
skill: reconnect-planner
description: Identify overdue contacts ranked by Dunbar tier-weighted urgency and plan reconnection outreach
version: 2.0.0
tags: [relationship, outreach, planning, dunbar]
---

# Reconnect Planner

## Purpose

Help the Relationship butler proactively identify contacts who are overdue for
reach-out, ranked by Dunbar tier-weighted urgency. This skill uses the unified
Dunbar scoring model — contacts in inner tiers (support clique, sympathy group)
are surfaced first and with higher urgency, while outer-tier contacts appear
only when their tier-appropriate cadence has been exceeded.

## When to Use This Skill

- During scheduled check-ins (e.g., weekly review via `relationship-maintenance`)
- When the user asks "who should I reach out to?"
- As part of a periodic relationship maintenance routine
- When planning upcoming social activities

## Dunbar Tier Model

Contacts are automatically placed into concentric social layers based on
interaction frequency and recency:

| Tier | Layer Name      | Default Cadence | Tier Weight |
|------|----------------|-----------------|-------------|
| 5    | Support clique  | 14 days         | 5.0         |
| 15   | Sympathy group  | 21 days         | 3.0         |
| 50   | Good friends    | 45 days         | 2.0         |
| 150  | Meaningful      | 120 days        | 1.0         |
| 500  | Acquaintances   | 270 days        | 0.5         |
| 1500 | Recognizable    | Never (default) | —           |

- A contact's `stay_in_touch_days` value overrides their tier's default cadence.
- Tier 1500 contacts are only suggested if they have `stay_in_touch_days` set.

## How It Works

### Step 1: Get Overdue Contacts

Call `contacts_overdue()` — this returns all contacts whose time since last
interaction exceeds their effective cadence (tier default or `stay_in_touch_days`).

Each result includes `dunbar_tier`, `dunbar_score`, `effective_cadence`, and
`days_since_last_interaction` fields.

### Step 2: Compute Urgency Score

For each overdue contact, compute:

```
urgency = (days_since_last_interaction / effective_cadence) * tier_weight + context_bonus
```

**Tier weights:**
- Tier 5: 5.0
- Tier 15: 3.0
- Tier 50: 2.0
- Tier 150: 1.0
- Tier 500: 0.5

**Context bonuses:**
- +2.0 if the contact has an important date within 14 days
- +1.0 if the contact has a pending gift (active gift not yet given)
- +0.5 if the contact's most recent note has positive emotional context

### Step 3: Rank and Select

Sort all overdue contacts by urgency score descending. Select the top N
(default 3, configurable by the caller).

Contacts with no interactions but an effective cadence get:
`days_since_last_interaction = None` — treat as maximally overdue for ranking
(use a large sentinel value such as `effective_cadence * 10` for urgency calc).

### Step 4: Gather Context for Top Contacts

For each selected contact, collect context to support a meaningful suggestion:

```python
# Last interaction summary
interaction_list(contact_id="<contact_id>", limit=1)

# Recall facts from memory
fact_list(contact_id="<contact_id>")

# Check for upcoming dates (birthday, anniversary)
upcoming_dates(days_ahead=30)  # filter for this contact

# Check for pending gift ideas
gift_list(contact_id="<contact_id>")

# Recent notes
note_list(contact_id="<contact_id>", limit=3)
```

### Step 5: Generate Outreach Suggestions

For each contact, create a personalized suggestion referencing their tier context.
Use the templates below based on the available context signals.

**Template — upcoming date hook:**
```
Reach out to [Name] — their [birthday/anniversary] is in [X days].
[Tier context: inner circle — high priority / good friend, check in]
Last talked: [date, summary]. Consider: [personal hook from memory/notes].
```

**Template — follow-up on previous conversation:**
```
Check in with [Name] (tier [X]) — [days] days since last contact ([tier default] day cadence).
Hook: [relevant fact or note that gives a natural reason to reach out].
```

**Template — general reconnection:**
```
Reconnect with [Name] — [X days] since last contact (overdue by [N] days).
[One sentence of context: shared interest, recent life event, or pending item].
```

## Output Format

Present suggestions in urgency order (highest first):

```
Reconnection suggestions (by Dunbar urgency):

1. Alice Chen (tier 5 — support clique, urgency 7.2)
   Last contact: 28 days ago (14-day cadence). Birthday in 3 days.
   Suggestion: Send a birthday message early. Mention her new job she started last month.

2. Bob Martinez (tier 15 — sympathy group, urgency 3.1)
   Last contact: 45 days ago (21-day cadence). Pending gift idea.
   Suggestion: Plan to give the gift and catch up. He was working on a marathon — ask how it went.

3. Carol Lee (tier 50 — good friend, urgency 1.8)
   Last contact: 60 days ago (45-day cadence). No upcoming dates.
   Suggestion: Simple check-in. You mentioned wanting to catch up over dinner.
```

## Integration with Existing Tools

- **`contacts_overdue()`**: Primary data source — returns tier-enriched overdue list
- **`interaction_list`**: Get last interaction per contact
- **`fact_list`**: Get relationship tier overrides and shared interests
- **`note_list`**: Gather recent notes with emotion context
- **`upcoming_dates`**: Check for birthdays/anniversaries
- **`gift_list`**: Check for pending gift ideas
- **`reminder_list`**: Check for active reminders
- **`reminder_create`**: Create reminders for reconnection tasks
- **`dunbar_tier_set`**: Set or clear manual tier overrides when computed tier seems wrong

## Edge Cases

- **Fewer than 3 overdue contacts**: Suggest only those that qualify; don't pad.
- **No overdue contacts**: Return empty list with message "All relationships up to date."
- **Contact with no interactions**: Treat as maximally overdue — inner-tier no-contact is urgent.
- **Dunbar tiers still calibrating** (few interactions in system): Note this in suggestions.
  Tier assignments become more accurate as more interactions are logged.
- **Manual tier override**: Note `[manually assigned]` next to the tier label.

## Advanced Usage

- **Custom cadence**: Set `stay_in_touch_days` on a contact to override their tier cadence.
  Example: A tier-150 contact you want to keep closer — set `stay_in_touch_days=30`.
- **Manual tier override**: Use `dunbar_tier_set(contact_id, tier)` when computed tier
  doesn't reflect the actual relationship importance.
- **Tier 1500 contacts**: Never suggested by default. Set `stay_in_touch_days` if you want
  reminders for someone in the recognizable tier.
