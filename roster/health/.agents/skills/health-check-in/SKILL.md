---
name: health-check-in
description: Guided daily or weekly health check-in workflow with adaptive questioning
version: 1.0.0
---

# Health Check-In Skill

This skill provides a structured, interactive health check-in workflow that adapts to the user's active conditions and medications. Use this for daily or weekly health monitoring.

## Purpose

Guide users through a comprehensive health check-in that covers medication adherence, vital measurements, symptoms, and diet. The check-in adapts based on active conditions and produces a summary at the end.

## Prerequisites

Before starting the check-in, gather context:
1. Get active conditions: `condition_list(status="active")`
2. Get active medications: `medication_list(active_only=true)`
3. Get recent measurements to understand what vitals the user typically tracks

## Check-In Flow

Follow this structured flow in order. Use progressive disclosure — ask one question at a time and wait for response before proceeding.

### 1. Medication Adherence (if applicable)

If the user has active medications:

For each active medication:
- Ask: "Did you take [medication name] [dosage] today?" (or "this week" for weekly check-ins)
- If yes: Log the dose using `medication_log_dose(medication_id=..., notes=...)`
- If no: Ask if there was a specific reason and record in notes
- If skipped doses: Remind about importance of adherence

### 2. Vital Measurements

Based on active conditions, prompt for relevant measurements:

**Standard vitals for everyone:**
- Weight (if tracked regularly)
- Ask: "Would you like to log your weight today?"
- Use: `measurement_log(type="weight", value=..., unit="kg")`

**Condition-specific vitals:**

- **If user has diabetes or "blood sugar" condition:**
  - Ask: "What's your blood glucose reading?"
  - Use: `measurement_log(type="blood_glucose", value=..., unit="mg/dL")`

- **If user has hypertension or cardiovascular condition:**
  - Ask: "What's your blood pressure reading?"
  - Accept format like "120/80"
  - Use: `measurement_log(type="blood_pressure", value={"systolic": 120, "diastolic": 80}, unit="mmHg")`

- **If user has respiratory condition:**
  - Ask: "What's your oxygen saturation (SpO2)?"
  - Use: `measurement_log(type="oxygen_saturation", value=..., unit="%")`

- **If user tracks heart rate:**
  - Ask: "What's your resting heart rate?"
  - Use: `measurement_log(type="heart_rate", value=..., unit="bpm")`

**Progressive approach:**
- Start with most critical measurements based on conditions
- Offer to log additional measurements if user volunteers them
- Don't overwhelm with too many questions at once

### 3. Symptom Screening

Screen for symptoms based on active conditions:

**General screening:**
- Ask: "Are you experiencing any new or unusual symptoms today?"
- If yes: Ask for symptom name, severity (1-10), and any additional details
- Use: `symptom_log(name=..., severity=..., notes=...)`

**Condition-specific screening:**

- **If diabetes:** Ask about fatigue, thirst, frequent urination, blurred vision
- **If cardiovascular:** Ask about chest pain, shortness of breath, dizziness, palpitations
- **If respiratory:** Ask about coughing, wheezing, shortness of breath
- **If gastrointestinal:** Ask about nausea, pain, changes in appetite
- **If chronic pain:** Ask about pain location and intensity

For each reported symptom:
- Ask severity on 1-10 scale (1=mild, 10=severe)
- Ask if symptom is new or ongoing
- Log using `symptom_log(name=..., severity=..., notes=...)`

**Important:** Only ask about condition-relevant symptoms. If user has no active conditions, do general screening only.

### 4. Diet and Nutrition (optional)

Ask: "Would you like to log any meals from today?"

If yes:
- For each meal: Get description, approximate calories if known, and any relevant nutrients
- Use: `meal_log(description=..., calories=..., nutrients={...})`

If user is tracking specific dietary concerns (based on conditions):
- **Diabetes:** Ask about carbohydrate intake
- **Cardiovascular:** Ask about sodium intake
- **Weight management:** Suggest using `nutrition_summary` to review calorie intake

### 5. Summary Generation

After completing the check-in, generate a summary using this template:

```
# Health Check-In Summary
**Date:** [current date]
**Type:** [daily/weekly]

## Medication Adherence
[List each medication with adherence status]
- [Medication name]: ✓ Taken / ✗ Missed [reason if provided]

## Vital Signs
[List measurements logged]
- [Type]: [value] [unit] [trend indicator if available]

## Symptoms
[List symptoms reported]
- [Symptom name]: Severity [1-10] [notes]
[Or "No new symptoms reported" if none]

## Diet
[Summary of meals logged or "No meals logged"]

## Notes
[Any additional observations or concerns]

## Follow-Up Actions
[Suggest any recommended actions based on the check-in]
- [Action item 1]
- [Action item 2]
```

**Trend indicators:**
- Get latest previous measurement using `measurement_latest(type=...)`
- Compare current vs previous value
- Show: ↑ (increased), ↓ (decreased), → (stable)

**Follow-up recommendations:**
- If missed multiple medications: Suggest setting reminders
- If concerning symptom severity (≥7): Suggest contacting healthcare provider
- If vital signs outside normal range: Suggest monitoring or medical consultation
- If tracking incomplete: Offer to set up scheduled check-ins

## Summary Storage

After presenting the summary to the user:
1. Offer to save summary as a research note
2. If accepted: `research_save(topic="Health Check-In [date]", content="[summary text]")`
3. This makes check-in summaries searchable later via `research_search`

## Adaptive Tips

**For daily check-ins:**
- Focus on medications and symptoms
- Keep vital measurements light (2-3 key metrics)
- Quick flow, 5-10 minutes

**For weekly check-ins:**
- More comprehensive measurement review
- Use `trend_report` to show weekly trends
- Review medication adherence rate using `medication_history`
- More detailed nutrition review using `nutrition_summary`

**User preferences:**
- After first check-in, ask user preference for daily vs weekly
- Note preference in AGENTS.md for future sessions
- Adapt questioning style based on user engagement

## Error Handling

- If user doesn't know a measurement: Skip it, don't pressure
- If user reports concerning symptoms: Prioritize logging those first
- If check-in is interrupted: Offer to resume where left off next time
- If medication ID lookup fails: Use medication name to search and confirm with user

## Example Check-In Flow

**Bot:** "Good morning! Let's do your daily health check-in. First, let's check on your medications."

**Bot:** "Did you take your Metformin 500mg this morning?"
**User:** "Yes, just took it."
**Bot:** [Logs dose] "Great! Did you take your Lisinopril 10mg?"
**User:** "Not yet, I'll take it with breakfast."
**Bot:** [Notes for reminder] "Okay, I'll note that. Now let's check your vitals."

**Bot:** "What's your blood glucose reading this morning?"
**User:** "112 mg/dL"
**Bot:** [Logs measurement] "That's within normal range. Would you like to log your blood pressure today?"
**User:** "Yes, it's 128 over 82."
**Bot:** [Logs measurement] "Got it. Any symptoms you'd like to report today?"
**User:** "No, feeling good."
**Bot:** [Generates summary and offers to save]

**Bot:** "Here's your check-in summary: [displays formatted summary]. Would you like me to save this summary for future reference?"
