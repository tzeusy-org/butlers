---
skill: weekly-health-summary
description: Scheduled weekly health digest — weight trend, medication adherence, symptom patterns, and notable changes
version: 1.0.0
trigger: scheduled
schedule: "0 9 * * 0"
tools_required:
  - health_summary
  - trend_report
  - measurement_history
  - medication_history
  - medication_list
  - symptom_history
  - notify
---

# Weekly Health Summary Skill

## Purpose

Generates a comprehensive weekly health digest every Sunday at 9am. Covers weight trend, medication adherence rates, and symptom patterns over the past 7 days. Sends the digest to the user via notify with `intent=send`.

---

## Tool Sequence

### Step 1: Overall Health Snapshot

```
health_summary()
```

Get the current health overview to establish baseline context.

### Step 2: Weight Trend

```
trend_report(metrics=["weight"], days=7)
```

Retrieve the weight trend for the past week. Note direction (improving/stable/concerning) and the delta from the first to last reading.

If no weight measurements exist for the week, skip and note "No weight data this week."

### Step 3: Blood Pressure Trend (if tracked)

```
trend_report(metrics=["blood_pressure"], days=7)
```

Include if blood pressure measurements exist. Apply the same improving/stable/concerning classification.

### Step 4: Medication Adherence

```
medication_list(active_only=true)
```

Get all active medications, then for each:

```
medication_history(medication_name=<name>, days=7)
```

Calculate adherence rate per medication:
- **Adherence rate** = doses_taken / doses_expected × 100%
- Expected doses depend on frequency: daily = 7, twice daily = 14, etc.
- Classify: Excellent (≥90%), Good (75-89%), Needs Improvement (<75%)

### Step 5: Symptom Patterns

```
symptom_history(days=7)
```

Summarize:
- Total symptom events logged
- Most frequent symptoms and their average severity
- Any high-severity events (severity ≥ 7)
- Patterns or recurrences worth noting

### Step 6: Compose Digest

Assemble the weekly summary using this template:

```
Weekly Health Summary — [Date Range]

**Weight**: [trend description, e.g., "Down 1.2 lbs — improving trend"] or "No data this week"

**Medication Adherence**:
- [Medication A]: [rate]% ([classification])
- [Medication B]: [rate]% ([classification])
[Or "No active medications tracked"]

**Symptoms**: [count] events logged
- [Top symptom]: [frequency]x, avg severity [N]/10
[Or "No symptoms logged this week"]

**Notable**: [Any patterns, anomalies, or encouraging changes worth highlighting]
```

Keep the digest concise and readable. Celebrate improvements. Flag anything concerning without alarm.

### Step 7: Send Digest

```
notify(channel="telegram", message=<digest>, intent="send")
```

Use `intent=send` to push the summary proactively (not as a reply to a user message).

---

## Guidelines

- **Skip missing data gracefully** — if a metric has no data for the week, note it briefly and move on
- **Celebrate progress** — highlight improvements in adherence, weight, or symptom reduction
- **Flag concerns without alarm** — if adherence is low or a symptom is recurring, name it plainly and suggest a next step
- **No medical advice** — report trends and patterns; do not diagnose or recommend treatments
- **Medical disclaimer** — include a brief disclaimer if flagging any out-of-range readings

---

## Medical Disclaimer

This summary provides statistical summaries of logged health data for personal tracking purposes only. It does not constitute medical advice. Consult your healthcare provider for diagnosis or treatment decisions.
