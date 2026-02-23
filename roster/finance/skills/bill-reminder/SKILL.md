---
name: bill-reminder
description: Workflow for reviewing, triaging, and managing upcoming bills and payment reminders
version: 1.0.0
---

# Bill Reminder Skill

This skill provides a structured, interactive workflow for reviewing upcoming bills, prioritizing by urgency, and setting calendar reminders. Use this for daily bill checks, end-of-week reviews, or on-demand bill status lookups.

## Purpose

Surface bills due within a specified horizon, triage them by urgency, highlight overdue obligations, and help you take action (pay, snooze, or dispute). The workflow ensures nothing slips through the cracks and you stay ahead of due dates.

## Prerequisites

Before starting the bill reminder workflow, gather context:
1. Get upcoming bills: `upcoming_bills(days_ahead=14, include_overdue=true)` to surface all due bills
2. Understand user's reminder preference: `memory_recall(topic="bill reminder preference")` (default: 3 days before due date)
3. Verify calendar is available if setting reminders

## Review Flow

Follow this structured flow in order. Adapt based on whether this is a proactive check, a user request, or a scheduled job trigger.

### 1. Horizon Selection

Ask the user what horizon they want to review (or present the default):

- **"Today"**: Bills due today or overdue
- **"This Week"**: Bills due within 7 days
- **"Two Weeks"**: Bills due within 14 days (default)
- **"Custom"**: Allow user to specify a custom number of days ahead

**Example conversation:**
```
Bot: "Let's check your upcoming bills. I can show you bills due today, this week, two weeks, or a custom range. What would you prefer?"
User: "This week"
Bot: "Got it, showing bills due in the next 7 days."
```

### 2. Bill Summary by Urgency

Use `upcoming_bills(days_ahead=<selected_horizon>, include_overdue=true)` to retrieve all bills and their urgency classifications.

The tool should return bills with urgency categories:
- `due_today`: Due today (0 days)
- `due_soon`: Due within 3 days (1–3 days)
- `due_upcoming`: Due within horizon but not imminent (4+ days)
- `overdue`: Past due date

Present results grouped by urgency:

```
# Upcoming Bills — Next [Horizon]

## 🚨 OVERDUE ([N] bills)
- [Payee]: $[amount] — due [date] ([days] days ago)
- [Payee]: $[amount] — due [date] ([days] days ago)

## 🔴 DUE TODAY ([N] bills)
- [Payee]: $[amount] — due today
- [Payee]: $[amount] — due today

## 🟠 DUE SOON ([N] bills)
- [Payee]: $[amount] — due in [N] days ([date])
- [Payee]: $[amount] — due in [N] days ([date])

## 🟡 UPCOMING ([N] bills)
- [Payee]: $[amount] — due in [N] days ([date])
- [Payee]: $[amount] — due in [N] days ([date])

---
**Total Due in Next [Horizon]**: $[total_amount]
**Total Overdue**: $[overdue_total] ([count] bills)
```

### 3. Urgency Guidance

Provide guidance on what to prioritize:

```
**Action Priority:**

1. **First**: Address overdue bills immediately. Late payments can harm credit and incur fees.
2. **Second**: Handle bills due today or tomorrow.
3. **Third**: Set reminders for upcoming bills so you don't miss them.
4. **Consider**: Automated payment setup for recurring bills to prevent future overdue situations.
```

### 4. Interactive Bill Triage

For each bill in the "Due Soon" and "Overdue" categories, offer interactive actions:

```
Bot: "Let's handle [Payee] ($[amount], due [date])."

What would you like to do?
- ✅ Mark as paid
- 📅 Set a reminder
- ⏸️ Snooze (skip this reminder for now)
- ❓ More info
```

**For each action:**

#### Action: Mark as Paid

If user selects "Mark as paid":
1. Ask confirmation: "Mark [Payee] $[amount] as paid today?"
2. Use `track_bill(payee=..., amount=..., status="paid", paid_at=<today>)` to record payment
3. Confirm: "✅ [Payee] marked as paid."
4. If this is a recurring bill, offer to create a subscription record: "Is this a recurring bill? I can track it automatically next time."

#### Action: Set a Reminder

If user selects "Set a reminder":
1. Determine reminder timing: Use user's default (from memory, typically 3 days before due date), or ask: "When would you like to be reminded? [3 days before / 1 day before / day of]?"
2. Use `calendar_create_event(title="Bill Due: [Payee]", start_time=<reminder_date>, notes="$[amount] due [due_date]", conflict_behavior="suggest")` to create calendar event
3. Confirm: "📅 Reminder set for [reminder_date]. You'll be reminded [duration] before the due date."

#### Action: Snooze

If user selects "Snooze":
1. Ask: "How long should I snooze this? [1 day / 3 days / 1 week / until payment]?"
2. Store snooze state in memory: `memory_store_fact(subject=<payee>, predicate="bill_snoozed", content="snoozed until [date]", permanence="volatile", importance=5.0, tags=["bill", "snooze"])`
3. Confirm: "⏸️ I'll remind you about [Payee] on [snooze_end_date]."

#### Action: More Info

If user selects "More info":
1. Retrieve bill details: `list_transactions(payee=<payee>, limit=5)` to show recent payment history
2. Present recent transactions for this payee:
   ```
   Recent payments to [Payee]:
   - $[amount] on [date]
   - $[amount] on [date]
   - $[amount] on [date]
   ```
3. Offer to create/update subscription record if pattern is recurring

### 5. Frequency and Category Analysis

After triaging immediate bills, offer optional analysis:

```
Bot: "Would you like to see a breakdown of your bills by frequency?"
```

If yes, use `memory_recall(topic="bills")` and `list_transactions()` to categorize:

```
# Bill Categories

Recurring Bills (Monthly):
- Rent/Mortgage: $[total]
- Utilities: $[total]
- Subscriptions: $[total]
- Insurance: $[total]
- [Other]: $[total]

One-Time Bills:
- [Description]: $[amount]
- [Description]: $[amount]
```

### 6. Payment Automation Suggestions

If the user has multiple overdue bills or has asked about reducing friction, suggest automation:

```
Bot: "I notice you have [N] recurring bills. Would you like to set up automatic payments for any of them?"

Benefits:
- Never miss a due date
- Reduce manual tracking
- Protect your credit score

Bills that could be automated:
- Rent ($[amount], due 1st of month)
- Utilities ($[amount], due [date])
- Insurance ($[amount], due [date])
```

**Important scope note:** Do not initiate payments directly. Only suggest, collect, and record the user's intent:

```
Bot: "To set up automatic payment, you'll need to contact [Payee] or your bank directly. 
I can set reminders to help you remember to complete the setup."
```

### 7. Summary and Follow-Up

Generate a summary at the end:

```
# Bill Reminder Summary

**Reviewed Horizon**: Next [horizon]
**Total Bills**: [N]
**Total Due**: $[amount]
**Status**:
- Overdue: [N] bills, $[total]
- Due Today: [N] bills
- Due Soon: [N] bills
- Upcoming: [N] bills

**Actions Taken**:
- Marked as paid: [N]
- Reminders set: [N]
- Snoozed: [N]

**Next Check**: [Suggested next review date based on nearest due date]
```

**Proactive follow-up suggestions:**
- "Your nearest bill is [Payee] on [date]. I'll remind you on [reminder_date]."
- "You have [N] overdue bills. Addressing these should be your priority."
- "Consider setting up automatic payments to reduce stress and protect your credit."

### 8. Memory Storage

After the user acknowledges the summary, optionally store insights:

```
Bot: "Would you like me to remember anything from this bill review?"
```

If yes, use `memory_store_fact()` to capture:

```python
# Example: Store bill reminder preference
memory_store_fact(
    subject="user",
    predicate="bill_reminder_preference",
    content="prefers reminders 3 days before due date",
    permanence="stable",
    importance=8.0,
    tags=["bill", "preference", "reminder"]
)

# Example: Store recurring bill
memory_store_fact(
    subject="rent",
    predicate="recurring_bill",
    content="$1,800/month due on the 1st",
    permanence="stable",
    importance=9.0,
    tags=["bill", "housing", "recurring"]
)

# Example: Store payment automation intent
memory_store_fact(
    subject="user",
    predicate="wants_to_automate",
    content="considering automatic payments for utilities and insurance",
    permanence="volatile",
    importance=6.0,
    tags=["bill", "automation", "intent"]
)

# Example: Flag concerning pattern
memory_store_fact(
    subject="user",
    predicate="overdue_pattern",
    content="has [N] overdue bills; may need payment plan or income assistance",
    permanence="volatile",
    importance=7.0,
    tags=["bill", "overdue", "concern"]
)
```

## Adaptive Tips

**For daily proactive checks (scheduled task):**
- Show only bills due today or overdue
- Keep it brief: "1 bill due today ($X). Set a reminder or mark as paid?"
- 1–2 minutes

**For end-of-week user request:**
- Show bills due this week and next week
- Highlight trends (any bills spiking in cost?)
- Suggest automation for frequent bills
- 5–7 minutes

**For on-demand bill lookup:**
- Allow any horizon selection
- Support specific payee queries: "When is my electric bill due?"
- 2–5 minutes

**For bill management specialist mode (monthly deep-dive):**
- Show full 30-day outlook
- Break down by category (utilities, subscriptions, housing, etc.)
- Compare to previous month's bills (if available)
- Surface any new bills or anomalies
- 15–20 minutes

## Error Handling

- If no bills exist for the period: "No bills due in the next [horizon]. You're all set!"
- If a bill's due date is unclear: Ask user to clarify or store the bill with a note
- If calendar conflict occurs: Present alternatives per calendar tool's conflict_behavior setting
- If payee lookup fails: Use the bill name directly or ask user to clarify
- If overdue list is very long: Prioritize by amount (highest first) and ask if user wants to batch actions

## Example Bill Review Flows

### Example 1: Quick Daily Check

```
Bot: "Daily bill check:"

🚨 OVERDUE:
- Electric bill: $89.50 — due 5 days ago

🔴 DUE TODAY:
- Credit card min: $340 — due today

🟠 DUE SOON:
- Comcast: $89.99 — due in 2 days

You have 1 overdue bill. Would you like to address it?
```

### Example 2: Weekly Review with Reminders

```
Bot: "Here's your bill outlook for the next 7 days:"

🔴 DUE TODAY:
- Water: $52.30

🟠 DUE SOON:
- Rent: $1,800 — due in 2 days
- Internet: $79.99 — due in 4 days
- Insurance: $120 — due in 6 days

Total due this week: $2,052.29

Let's handle each one. First, your water bill ($52.30, due today). What would you like to do?
[✅ Mark as paid / 📅 Set reminder / ⏸️ Snooze / ❓ More info]
```

### Example 3: Two-Week Overview with Automation Suggestion

```
Bot: "Two-week bill outlook:"

🟡 UPCOMING (14 days):
- Rent: $1,800 — due in 2 days
- Electric: $89.50 — due in 7 days
- Internet: $79.99 — due in 9 days
- Spotify: $12.99 — due in 12 days
- Netflix: $15.99 — due in 13 days

Total: $1,998.47

💡 I notice rent, electric, and internet are recurring bills due around the same time each month.
Would you like to set up automatic payments to avoid missing these deadlines?
```

### Example 4: Comprehensive Monthly Bill Management

```
Bot: "February bill management overview:"

**Recurring Bills This Month:**
- Rent: $1,800 (due 1st)
- Utilities (Electric, Water, Gas): $240 (due ~10th)
- Internet: $79.99 (due 15th)
- Subscriptions: $45/month (Netflix, Spotify, etc.)
- Insurance: $150 (due 25th)

**One-Time Bills:**
- Car repair: $340 (due 18th)
- Medical bill: $120 (due 28th)

**Total This Month**: $2,774.99

**Trend**: Your February bills are $150 higher than January (new subscription added).

**Recommendations:**
1. Set reminders for one-time bills (repair, medical)
2. Consider automating recurring bills
3. Review new subscriptions — are they all still needed?

Let's start with today's bills. Anything due today?
```

### Example 5: Overdue Crisis Management

```
Bot: "Bill status review:"

🚨 OVERDUE ([N] days behind):
- Rent: $1,800 — 20 days overdue
- Utilities: $240 — 10 days overdue
- Credit card: $300 — 5 days overdue

**Total Overdue**: $2,340

This is a priority issue. Overdue payments can:
- Damage your credit score
- Trigger late fees and interest
- Risk eviction (for housing) or service disconnection

**Immediate Actions:**
1. Contact landlord/utility company — explain situation and ask about payment plans
2. Prioritize rent and utilities first
3. I can help you remember these when payments are made

Would you like help drafting messages to your creditors?
```

---

## Important Reminders

1. **Always surface overdue bills first**: They are the highest priority
2. **Provide clear due dates**: Include the date, days remaining, and amount
3. **Support actions**: Offer to mark paid, set reminders, snooze, or provide more info
4. **Respect urgency**: Tailor interaction speed based on due date proximity
5. **Never initiate payments**: Only track, remind, and suggest
6. **Use memory strategically**: Store recurring patterns and preferences, not individual bills
7. **Escalate concerns**: If a user has multiple overdue bills, flag this as a potential financial hardship situation

---

## Version History

- v1.0.0 (2026-02-23): Initial skill creation with urgency triage, interactive actions, automation suggestions, and memory integration
