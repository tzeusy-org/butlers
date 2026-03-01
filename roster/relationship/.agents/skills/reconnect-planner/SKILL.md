---
skill: reconnect-planner
description: Proactively identify contacts who haven't been reached out to in a while and plan reconnection outreach
version: 1.0.0
tags: [relationship, outreach, planning]
---

# Reconnect Planner

## Purpose

Help the Relationship butler proactively identify contacts who are becoming "stale" (haven't been contacted in a while) and plan meaningful reconnection outreach. This skill combines staleness detection, context-aware outreach suggestions, and integration with the reminder system to ensure important relationships don't fade due to neglect.

## When to Use This Skill

- During scheduled check-ins (e.g., weekly review)
- When the user asks "who should I reach out to?"
- As part of a periodic "relationship maintenance" routine
- When planning upcoming social activities

## How It Works

### Step 1: Identify Stale Contacts

Query the database to find contacts whose last interaction exceeds the staleness threshold for their relationship tier.

**Staleness Thresholds (by relationship tier):**
- **Close friends:** 2 weeks (14 days)
- **Friends:** 1 month (30 days)
- **Acquaintances:** 3 months (90 days)
- **Professional:** 2 months (60 days)
- **Family:** 2 weeks (14 days)
- **No tier specified:** Default to 1 month (30 days)

**Implementation approach:**

1. Use `contact_search` or list all contacts
2. For each contact:
   - Check their relationship tier via `fact_list` (look for a "tier" fact) or relationship labels
   - Use `interaction_list` to get their most recent interaction
   - Calculate days since last interaction
   - Compare against the threshold for their tier
3. Build a prioritized list of stale contacts

### Step 2: Gather Context for Each Stale Contact

For each stale contact, gather relevant context to inform outreach suggestions:

**Context to collect:**
- **Last interaction:** Type, date, and summary from `interaction_list`
- **Notes:** Recent notes from `note_list` (especially with emotion tags)
- **Upcoming dates:** Check `upcoming_dates` for birthdays or anniversaries
- **Shared interests:** Extract from `fact_list` or contact details
- **Pending gifts:** Check `gift_list` for any pending gift ideas
- **Active reminders:** Check `reminder_list` for context

### Step 3: Generate Outreach Suggestions

For each stale contact, create a personalized outreach suggestion based on the gathered context.

**Outreach Template Patterns:**

#### Pattern 1: Birthday/Anniversary Upcoming
```
Reach out to [name] — their [birthday/anniversary] is coming up on [date].
Suggested approach: Send a personal message a few days early, maybe mention [shared interest/recent note].
```

#### Pattern 2: Shared Interest Hook
```
Reconnect with [name] — you haven't talked since [last interaction date].
Hook: They're interested in [interest]. Consider sharing [article/event/question] about it.
```

#### Pattern 3: Follow-up on Previous Conversation
```
Check in with [name] — last time you talked about [summary from last interaction].
Suggested approach: Ask how [that topic] turned out or share an update from your side.
```

#### Pattern 4: Simple Check-in
```
Time to reconnect with [name] — it's been [X days/weeks] since you last talked.
Suggested approach: A simple "thinking of you" message or suggest [coffee/call/activity].
```

#### Pattern 5: Gift Occasion
```
Reach out to [name] for [occasion] — you had a gift idea: [description].
Suggested approach: Buy the gift and schedule delivery, or plan an in-person handoff.
```

### Step 4: Prioritization Framework

Prioritize stale contacts based on:

1. **Relationship tier:** Close friends and family come first
2. **Staleness severity:** How far over the threshold (e.g., 2x overdue > 1.5x overdue)
3. **Upcoming dates:** Contacts with birthdays/anniversaries in the next 2 weeks get priority
4. **Emotional context:** Notes with positive emotion tags or unresolved items
5. **Active reminders:** Contacts with active (non-dismissed) reminders

**Priority levels:**
- **Urgent:** Close friends/family >2x overdue OR upcoming important date within 7 days
- **High:** Tier 1-2 contacts >1.5x overdue OR upcoming date within 14 days
- **Medium:** Any contact exceeding their threshold
- **Low:** Approaching threshold but not yet overdue

### Step 5: Create Reminders from Recommendations

For each prioritized recommendation, offer to create a reminder:

**Reminder creation workflow:**
1. Present the outreach suggestion to the user
2. Ask if they want a reminder set
3. If yes, use `reminder_create` with:
   - `contact_id`: The stale contact's ID
   - `message`: The outreach suggestion text
   - `reminder_type`: "one_time" (unless the user wants recurring)
   - `due_at`: User-specified time or default to "tomorrow at 10am"

**Example reminder message:**
```
"Reach out to Alice — her birthday is Feb 15. Consider sending a message early and mentioning her new pottery hobby."
```

## Integration with Existing Tools

This skill relies on:
- **`contact_search`**: Find all contacts
- **`interaction_list`**: Get last interaction per contact
- **`fact_list`**: Get relationship tier and shared interests
- **`note_list`**: Gather recent notes with emotion context
- **`upcoming_dates`**: Check for birthdays/anniversaries
- **`gift_list`**: Check for pending gift ideas
- **`reminder_list`**: Check for active reminders
- **`reminder_create`**: Create reminders for reconnection tasks

## Progressive Disclosure Structure

### Quick Start (TL;DR)
Run a staleness check and get a prioritized list of contacts to reconnect with, along with context-aware outreach suggestions.

### Standard Workflow
1. Identify stale contacts using tier-based thresholds
2. Gather context (last interaction, notes, dates, interests)
3. Generate personalized outreach suggestions
4. Prioritize by tier, staleness, and upcoming events
5. Create reminders for selected recommendations

### Advanced Usage
- **Custom thresholds:** Override default staleness thresholds per contact using a "staleness_days" fact
- **Batch processing:** Process all stale contacts at once vs. reviewing one by one
- **Recurring check-ins:** Set up a recurring reminder to run this skill weekly
- **Integration with calendar:** Cross-reference with calendar events to suggest in-person meetups

## Example Usage

**User:** "Who should I reach out to this week?"

**Agent workflow:**
1. Run staleness detection across all contacts
2. Find 3 stale contacts:
   - Alice (close friend, 20 days since last contact, birthday Feb 15)
   - Bob (professional, 65 days, no upcoming dates)
   - Carol (friend, 35 days, shared interest in hiking)
3. Generate suggestions:
   - **Urgent:** "Alice — her birthday is in 5 days. Send a message early and mention her pottery hobby."
   - **Medium:** "Carol — it's been over a month. Share that hiking trail article you saved."
   - **Low:** "Bob — reconnect after 2+ months. Ask about his new role."
4. Present to user in priority order
5. Offer to create reminders for each

**User:** "Yes, create reminders for Alice and Carol."

**Agent actions:**
- `reminder_create(alice_id, "Reach out to Alice...", "one_time", due_at="tomorrow 10am")`
- `reminder_create(carol_id, "Reach out to Carol...", "one_time", due_at="this weekend")`

## Notes

- This is a **proactive** skill — meant to prevent relationships from becoming stale, not just react when they already are
- Staleness thresholds are guidelines, not hard rules. The user can override via facts.
- Outreach suggestions should be **specific and actionable**, not generic "check in with X"
- Always respect the user's social energy — offer to batch or spread out reconnections
- Update interaction log when the user follows through on an outreach suggestion

## Future Enhancements

- Machine learning to learn optimal staleness thresholds per contact based on historical patterns
- Integration with email/telegram modules to draft outreach messages directly
- Social network analysis to identify "connector" contacts who bridge friend groups
- Seasonal patterns (e.g., holiday check-ins, summer vacation planning)
