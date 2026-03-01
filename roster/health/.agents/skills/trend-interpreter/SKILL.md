---
skill: trend-interpreter
description: Interpret measurement trends and flag potential anomalies in health data
version: 1.0.0
tools_required:
  - trend_report
  - measurement_history
---

# Trend Interpreter Skill

## Purpose

This skill helps you interpret measurement trends from the Health butler's data and flag potential anomalies. Use this skill when analyzing trend reports or measurement history to provide meaningful insights to the user.

**MEDICAL DISCLAIMER**: This skill provides statistical interpretation guidance only. It does NOT constitute medical advice. Always encourage users to consult healthcare professionals for medical concerns, especially for sudden changes, out-of-range values, or concerning trends.

---

## When to Use This Skill

- After running `trend_report` to generate a weekly or monthly summary
- When analyzing `measurement_history` for a specific measurement type
- When the user asks about patterns or changes in their health data
- When flagging concerning trends that warrant professional medical attention

---

## Statistical Interpretation Guidelines

### 1. Blood Pressure (BP)

**Measurement format**: `{"systolic": 120, "diastolic": 80}` (mmHg)

**Reference Ranges** (American Heart Association):
- Normal: systolic < 120 AND diastolic < 80
- Elevated: systolic 120-129 AND diastolic < 80
- Hypertension Stage 1: systolic 130-139 OR diastolic 80-89
- Hypertension Stage 2: systolic ≥ 140 OR diastolic ≥ 90
- Hypertensive Crisis: systolic > 180 OR diastolic > 120 (seek immediate care)

**Trend Interpretation**:
- **Improving**: Consistent movement from higher stages toward normal range over 2+ weeks
- **Stable**: Readings remain within same category with < 10 mmHg systolic variation
- **Concerning**: Upward trend crossing category boundaries, or increasing by > 20 mmHg systolic over a week
- **Needs Attention**: Any hypertensive crisis reading, or sustained hypertension stage 2 values

**Anomaly Detection**:
- Single reading > 180/120: Flag as potential hypertensive crisis
- Sudden increase of > 30 mmHg systolic from recent average: Flag as anomaly
- Diastolic > 100 for 3+ consecutive readings: Flag as concerning trend

### 2. Weight

**Measurement format**: `{"value": 70.5, "unit": "kg"}` or `{"value": 155.0, "unit": "lbs"}`

**Interpretation Context**:
- Weight fluctuates naturally by 0.5-2 kg (1-4 lbs) daily due to hydration, food intake, and time of day
- Weekly trends are more meaningful than daily variations

**Trend Interpretation**:
- **Improving**: Moving toward user's stated health goal (if documented in notes)
- **Stable**: < 2 kg (4 lbs) variation over a week
- **Concerning**: > 5% body weight change in a month without intentional diet/exercise changes
- **Needs Attention**: > 2 kg (4 lbs) loss or gain in a week without explanation, especially with other symptoms

**Anomaly Detection**:
- Sudden loss > 2 kg in 3 days: Flag as potential anomaly
- Gain > 2 kg in 3 days: Flag as potential fluid retention or measurement error
- Monotonic trend (all increasing or all decreasing) over 10+ days at > 0.5 kg/day: Flag for review

### 3. Blood Glucose

**Measurement format**: `{"value": 95, "unit": "mg/dL"}` or `{"value": 5.3, "unit": "mmol/L"}`

**Reference Ranges** (ADA guidelines, fasting):
- Normal: 70-99 mg/dL (3.9-5.5 mmol/L)
- Prediabetes: 100-125 mg/dL (5.6-6.9 mmol/L)
- Diabetes: ≥ 126 mg/dL (7.0 mmol/L)
- Hypoglycemia: < 70 mg/dL (3.9 mmol/L)

**Post-meal context** (if noted):
- Normal post-meal (2 hours): < 140 mg/dL (7.8 mmol/L)
- Prediabetes post-meal: 140-199 mg/dL (7.8-11.0 mmol/L)
- Diabetes post-meal: ≥ 200 mg/dL (11.1 mmol/L)

**Trend Interpretation**:
- **Improving**: Consistent readings moving toward or maintaining normal range
- **Stable**: All readings within same category (normal, prediabetes, or diabetes) with < 20 mg/dL variation
- **Concerning**: Trend crossing from normal to prediabetes range, or increasing variability
- **Needs Attention**: Any reading < 50 mg/dL (severe hypoglycemia) or > 250 mg/dL, or sustained prediabetic/diabetic ranges

**Anomaly Detection**:
- Reading < 60 mg/dL: Flag as hypoglycemia risk
- Reading > 250 mg/dL: Flag as severe hyperglycemia
- Variability > 50 mg/dL between consecutive readings (same time of day): Flag as unstable glucose

### 4. Heart Rate (HR)

**Measurement format**: `{"value": 72, "unit": "bpm"}`

**Reference Ranges** (resting HR for adults):
- Athlete: 40-60 bpm
- Excellent: 60-69 bpm
- Good: 70-79 bpm
- Average: 80-89 bpm
- Below Average: 90-99 bpm
- Poor: > 100 bpm

**Context matters**: Exercise, stress, caffeine, medications, and time of day significantly affect HR.

**Trend Interpretation**:
- **Improving**: Resting HR decreasing toward 60-70 bpm range (indicates improved cardiovascular fitness)
- **Stable**: Resting HR within 5-10 bpm variation at same time of day
- **Concerning**: Resting HR increasing by > 10 bpm over a week without obvious cause
- **Needs Attention**: Sustained resting HR > 100 bpm (tachycardia) or < 40 bpm without athletic training

**Anomaly Detection**:
- Resting HR > 120 bpm: Flag as potential tachycardia
- Resting HR < 45 bpm for non-athletes: Flag as potential bradycardia
- Sudden increase > 20 bpm from recent baseline: Flag for review

---

## Anomaly Detection Heuristics

### General Principles

1. **Sudden Change Rule**: A change > 2 standard deviations from recent mean (past 7-14 days) warrants flagging
2. **Out-of-Range Rule**: Any value outside clinical reference ranges requires notation
3. **Trend Reversal Rule**: Abrupt reversal of a stable trend (e.g., stable → rapidly increasing) needs attention
4. **Consistency Rule**: 3+ consecutive readings in concerning territory escalates to "needs attention"
5. **Context Rule**: Always check measurement notes for context (time of day, post-exercise, after medication, etc.)

### Priority Levels

- **Critical**: Immediate medical attention recommended (e.g., BP > 180/120, glucose < 50 mg/dL)
- **High**: Schedule medical consultation soon (e.g., sustained stage 2 hypertension, unexplained rapid weight loss)
- **Medium**: Monitor closely and mention at next routine appointment (e.g., elevated BP trending upward)
- **Low**: Note for awareness but not immediately concerning (e.g., normal variation within ranges)

---

## Trend Narrative Templates

### Improving Trend

```
📈 **Improving Trend Detected**

Your [measurement type] shows positive movement:
- Starting point: [first value] on [date]
- Current point: [latest value] on [date]
- Change: [delta with direction]

This represents a [percentage or clinical category change] improvement. [Contextual note about what this means clinically, if applicable]. Keep up the good work!
```

### Stable Trend

```
✅ **Stable Trend**

Your [measurement type] remains consistent:
- Range: [min] to [max] over [period]
- Average: [mean value]
- Category: [clinical range if applicable]

This stability indicates [positive context, e.g., "good control" or "consistent routine"]. Continue your current approach.
```

### Concerning Trend

```
⚠️ **Trend Requires Attention**

Your [measurement type] shows a concerning pattern:
- [Description of the trend, e.g., "increasing by X over Y days"]
- Current readings: [recent values]
- Context: [any relevant notes from measurement data]

**Recommendation**: Consider scheduling a consultation with your healthcare provider to discuss this trend. Monitor closely and log any relevant symptoms or lifestyle changes.
```

### Needs Medical Attention

```
🚨 **Urgent: Medical Review Recommended**

Your [measurement type] readings are outside normal ranges:
- Recent values: [list of concerning readings with dates]
- Clinical concern: [e.g., "Stage 2 Hypertension" or "Hypoglycemia"]

**Action Required**: Contact your healthcare provider promptly. [If critical: "If experiencing symptoms like [list], seek immediate medical care."]

This is not medical advice—please consult a qualified healthcare professional.
```

---

## Workflow

### Step 1: Gather Data

Use `trend_report(period="week")` or `trend_report(period="month")` to get comprehensive data, OR use `measurement_history(type="...", start_date="...", end_date="...")` for specific measurement analysis.

### Step 2: Calculate Statistics

For each measurement type present in the data:
- Calculate mean, min, max, standard deviation
- Identify first and last values for trend direction
- Count number of readings
- Check for out-of-range values using reference ranges above

### Step 3: Apply Anomaly Detection

Check each measurement against:
- Reference range boundaries
- Sudden change thresholds
- Consistency patterns (3+ concerning readings)
- Context from notes (if available)

### Step 4: Classify Trends

For each measurement type, determine trend category:
- Improving, Stable, Concerning, or Needs Attention
- Assign priority level: Critical, High, Medium, or Low

### Step 5: Generate Narrative

Use the appropriate template above to craft a clear, actionable narrative. Always include:
- Specific values and dates
- Context from clinical reference ranges
- Clear recommendations
- Medical disclaimer when appropriate

### Step 6: Summarize Findings

Present findings in order of priority:
1. Critical and High priority items first
2. Medium priority items
3. Low priority items and positive trends last

Include an overall summary statement about the user's health data trends.

---

## Examples

### Example 1: Blood Pressure Analysis

**Input**: `trend_report(period="week")` returns 7 BP readings ranging from 128/82 to 142/88

**Analysis**:
- Mean: 135/85 (Stage 1 Hypertension)
- Trend: Increasing (128/82 → 142/88)
- Anomalies: Last reading 142/88 crosses into stage 1, upward trend of 14 mmHg systolic

**Output**:
```
⚠️ **Blood Pressure Trend Requires Attention**

Your blood pressure shows an upward trend this week:
- Week start: 128/82 mmHg (Elevated) on [date]
- Week end: 142/88 mmHg (Stage 1 Hypertension) on [date]
- Change: +14/+6 mmHg
- Average: 135/85 mmHg

You've moved from the Elevated category into Stage 1 Hypertension range. While a single week of readings isn't diagnostic, this trend warrants attention.

**Recommendation**: Monitor your BP daily at the same time. Consider lifestyle factors like sodium intake, stress, and sleep. Schedule a check-in with your healthcare provider if this trend continues or readings exceed 140/90 consistently.
```

### Example 2: Stable Weight

**Input**: `measurement_history(type="weight")` returns 10 readings over 2 weeks, ranging from 69.8 to 70.4 kg

**Analysis**:
- Mean: 70.1 kg
- Range: 0.6 kg variation (< 1% of body weight)
- Trend: Stable (no monotonic direction)

**Output**:
```
✅ **Stable Weight Trend**

Your weight remains consistent over the past 2 weeks:
- Range: 69.8 to 70.4 kg
- Average: 70.1 kg
- Variation: 0.6 kg (0.9%)

This level of variation is completely normal and reflects typical daily fluctuations from hydration and food intake. Your weight is stable—keep maintaining your current routine!
```

### Example 3: Concerning Glucose Readings

**Input**: `trend_report(period="month")` shows fasting glucose readings: 92, 95, 98, 103, 108, 105, 110 mg/dL

**Analysis**:
- Trend: Increasing from normal (92) to prediabetes range (110)
- Last 3 readings: all > 100 mg/dL (prediabetes threshold)
- Change: +18 mg/dL over one month

**Output**:
```
⚠️ **Blood Glucose Trend Requires Medical Review**

Your fasting blood glucose shows an upward trend over the past month:
- Month start: 92 mg/dL (Normal) on [date]
- Month end: 110 mg/dL (Prediabetes range) on [date]
- Change: +18 mg/dL
- Last 3 readings: All above 100 mg/dL (prediabetes threshold)

You've transitioned from the normal range into the prediabetes range (100-125 mg/dL). This is an important finding that should be discussed with your healthcare provider.

**Recommendation**: Schedule an appointment with your doctor to discuss these results. They may want to order additional tests (HbA1c) and discuss lifestyle modifications or monitoring strategies. In the meantime, continue tracking your glucose regularly.

**Disclaimer**: This is not a diagnosis. Only a healthcare provider can properly evaluate your glucose levels in context of your overall health.
```

---

## Important Reminders

1. **Always prioritize safety**: Flag critical values immediately and recommend medical consultation
2. **Provide context**: Raw numbers are less useful than interpreted trends with clinical context
3. **Be clear about limitations**: You're interpreting data, not diagnosing conditions
4. **Encourage user agency**: Provide actionable recommendations they can act on
5. **Document assumptions**: Note when you're making assumptions about measurement context (e.g., "assuming fasting measurement")
6. **Check measurement notes**: Context matters—a high HR after exercise is different from resting tachycardia

---

## Medical Disclaimer

**This skill provides statistical interpretation and general health information only. It does NOT constitute medical advice, diagnosis, or treatment. The Health butler and this skill are tools for personal health tracking and awareness, not substitutes for professional medical care.**

**Always consult with qualified healthcare providers for:**
- Medical diagnosis and treatment decisions
- Interpretation of abnormal readings
- Changes to medications or treatment plans
- Urgent or emergency medical concerns

**If you experience severe symptoms or medical emergencies, contact emergency services immediately.**

---

## Version History

- v1.0.0 (2026-02-10): Initial skill creation with BP, weight, glucose, and heart rate interpretation guidelines
