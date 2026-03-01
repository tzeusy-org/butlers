---
skill: memory-taxonomy
description: Health domain memory classification — taxonomy, permanence levels, and example fact patterns for storing health knowledge
version: 1.0.0
tools_required:
  - memory_store_fact
  - memory_recall
  - memory_search
---

# Health Memory Taxonomy Skill

## Purpose

Use this skill when storing health facts to memory. It defines the health domain taxonomy for subjects, predicates, permanence levels, and tagging — ensuring consistent, retrievable health knowledge over time.

Only load this skill when you are actively extracting and storing facts from a user message. It is not needed for every session.

---

## Health Domain Taxonomy

### Subject

- For user-related health data: `"user"` or the user's name
- For conditions: condition name (e.g., `"hypertension"`, `"diabetes"`)
- For medications: medication name (e.g., `"metformin"`, `"lisinopril"`)

### Predicates

| Predicate | Meaning |
|-----------|---------|
| `medication` | Current medication with dosage |
| `medication_frequency` | How often taken |
| `dosage` | Amount per dose |
| `condition_status` | `"active"`, `"managed"`, or `"resolved"` |
| `symptom_pattern` | Recurring symptoms or triggers |
| `symptom_trigger` | What causes or worsens a symptom |
| `measurement_baseline` | Typical or target measurement values |
| `dietary_restriction` | Food allergies or restrictions |
| `exercise_routine` | Regular physical activity |
| `doctor_name` | Healthcare provider |
| `pharmacy` | Preferred pharmacy location |
| `allergy` | Medication or substance allergies |

### Permanence Levels

| Level | When to use |
|-------|------------|
| `stable` | Chronic conditions, long-term medications, allergies — things unlikely to change |
| `standard` | Current medications, active symptoms, dietary patterns — current state |
| `volatile` | Acute symptoms, temporary conditions, one-time measurements |

### Tags

Use tags to enable cross-cutting queries. Common health tags:

`chronic`, `acute`, `medication`, `condition`, `tracking`, `goal`, `allergy`, `critical`, `blood-pressure`, `diabetes`, `symptom`, `pattern`, `healthcare-provider`, `dosage-change`, `sensitive`, `private`

---

## Example Facts

### Medication started

```python
# From: "Started taking Lisinopril 10mg daily for blood pressure"
memory_store_fact(
    subject="Lisinopril",
    predicate="medication",
    content="10mg daily for blood pressure management",
    permanence="standard",
    importance=8.0,
    tags=["medication", "blood-pressure"]
)
```

### Allergy (high importance, stable)

```python
# From: "I'm allergic to penicillin"
memory_store_fact(
    subject="user",
    predicate="allergy",
    content="allergic to penicillin",
    permanence="stable",
    importance=9.0,
    tags=["allergy", "critical"]
)
```

### Symptom trigger pattern

```python
# From: "Headaches usually happen when I don't drink enough water"
memory_store_fact(
    subject="headaches",
    predicate="symptom_trigger",
    content="triggered by dehydration",
    permanence="standard",
    importance=6.0,
    tags=["symptom", "pattern"]
)
```

### Healthcare provider

```python
# From: "Dr. Chen is my primary care physician"
memory_store_fact(
    subject="user",
    predicate="doctor_primary_care",
    content="Dr. Chen",
    permanence="stable",
    importance=7.0,
    tags=["healthcare-provider"]
)
```

---

## Extraction Guidelines

- **Extract proactively** — capture facts from conversational messages even if they are incidental to the main request
- **Use permanence wisely** — chronic conditions and allergies are `stable`; acute symptoms are `volatile`
- **Privacy matters** — add `sensitive` or `private` tags for personal health information
- **Importance calibration** — allergies and critical conditions: 8-10; active medications: 7-8; patterns and baselines: 5-7; transient notes: 3-5
