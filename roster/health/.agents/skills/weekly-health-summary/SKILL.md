---
name: weekly-health-summary
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
trend_report(period="week")
```

Call this once for the weekly report. Read weight from the returned `measurement_trends`, using its actual first and last readings and timestamps. Describe the observed change without assuming that weight loss or gain is medically better. The same report supplies the other measurement trends, medication adherence and symptom aggregates below.

If no weight measurements exist for the week, skip and note "No weight data this week."

### Step 3: Blood Pressure Trend (if tracked)

Use the same report's `measurement_trends` entry for blood pressure if present; do not issue a second report with unsupported `metrics` or `days` arguments. Blood pressure values are compound systolic/diastolic data, not one scalar.

If individual measurement details are needed, use the registered call:

```
measurement_history(type="blood_pressure", start_date=<window_start_iso8601>, end_date=<window_end_iso8601>)
```

Use timezone-aware ISO-8601 bounds for one seven-day detail window. Describe only the returned readings; do not invent a clinical classification.

### Step 4: Medication Adherence

```
medication_list(active_only=true)
```

Use the weekly report's `medication_adherence` entries: `medication_id`, `name`, `taken_doses`, `expected_doses` and `adherence_rate`. The service accounts for the medication's age and schedule. Do not replace its denominator with a fixed seven or fourteen doses.

If dose details are needed for an active medication, use its actual returned `id`:

```
medication_history(medication_id=<returned_medication_id>, start_date=<window_start_iso8601>, end_date=<window_end_iso8601>)
```

Use the same aware detail-window bounds. This history call's `adherence_rate` is the fraction of logged doses that were not skipped; it is not the weekly expected-dose adherence and must not replace that report field.

For a returned weekly percentage, retain these descriptive labels: Excellent (≥90%), Good (75-89%), Needs Improvement (<75%). A null rate is unavailable/not applicable, not zero.

### Step 5: Symptom Patterns

```
symptom_history(start_date=<window_start_iso8601>, end_date=<window_end_iso8601>)
```

Use the weekly report's `symptom_frequency` and `symptom_severity_avg` for aggregates. Request the history above only for detail, using the same aware bounds. Summarize only actual returned data:
- Total symptom events logged
- Most frequent symptoms and their average severity
- Any high-severity events (severity ≥ 7)
- Patterns or recurrences worth noting

### Step 6: Compose Digest

Assemble the weekly summary using this template:

```
Weekly Health Summary — [Date Range]

**Weight**: [trend description, e.g., "Down 1.2 lbs across the recorded readings"] or "No data this week"

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

- **Distinguish outcomes** — a successful empty response means no recorded data in that window; a failed read means unavailable. Missing or null fields are not zero. Report genuine returned zeros as zero and do not turn an API error into a healthy empty summary.
- **Celebrate progress** — highlight improvements in adherence, weight, or symptom reduction
- **Flag concerns without alarm** — if adherence is low or a symptom is recurring, name it plainly and suggest a next step
- **No medical advice** — report trends and patterns; do not diagnose or recommend treatments
- **Medical disclaimer** — include a brief disclaimer if flagging any out-of-range readings

---

## Medical Disclaimer

This summary provides statistical summaries of logged health data for personal tracking purposes only. It does not constitute medical advice. Consult your healthcare provider for diagnosis or treatment decisions.
