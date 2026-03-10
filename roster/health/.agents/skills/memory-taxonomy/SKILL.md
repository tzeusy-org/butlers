---
skill: memory-taxonomy
description: Health domain memory classification — taxonomy, permanence levels, and example fact patterns for storing health knowledge with entity resolution
version: 2.0.0
tools_required:
  - memory_store_fact
  - memory_recall
  - memory_search
  - memory_entity_resolve
  - memory_entity_create
---

# Health Memory Taxonomy Skill

## Purpose

Use this skill when storing health facts to memory. It defines the health domain taxonomy for
subjects, predicates, permanence levels, and tagging — ensuring consistent, retrievable health
knowledge over time.

Only load this skill when you are actively extracting and storing facts from a user message.
It is not needed for every session.

For the full entity resolution protocol — including the resolve-or-create transitory pattern,
disambiguation policy, and idempotency handling — see the `butler-memory` shared skill.

---

## Resolve Before Storing

**Every fact about a healthcare provider, pharmacy, clinic, or other external health entity
MUST be anchored to a resolved entity via `entity_id`.** Never call `memory_store_fact` with
only a raw `subject` string for external entities.

User-related health facts (conditions, medications, allergies, symptoms) are about the user
themselves — use the sender's `entity_id` from the REQUEST CONTEXT preamble.

### Health Domain Entity Type Inference

When calling `memory_entity_resolve` or creating a transitory entity, infer the correct
`entity_type` from context:

| Health entity | `entity_type` |
|---------------|---------------|
| Healthcare provider (doctor, specialist, therapist) | `person` |
| Clinic, hospital, medical practice, imaging lab | `organization` |
| Pharmacy (Walgreens, CVS, local pharmacy) | `organization` |
| Insurance provider | `organization` |
| User's own health facts (conditions, meds, allergies) | `person` — use sender `entity_id` from preamble |
| Medication or condition name as topic | *(no entity required — anchor fact to user's `entity_id`)* |

### Resolve-or-Create for Health Entities

When a healthcare provider or pharmacy is first mentioned and not in the entity graph, create
a transitory entity:

```python
# "Dr. Chen is my primary care physician"
# Step 1: resolve the provider
candidates = memory_entity_resolve(
    name="Dr. Chen",
    entity_type="person",
    context_hints={"topic": "primary care physician"}
)
# → zero candidates: create transitory entity
try:
    result = memory_entity_create(
        canonical_name="Dr. Chen",
        entity_type="person",
        metadata={
            "unidentified": True,
            "source": "fact_storage",
            "source_butler": "health",
            "source_scope": "health"
        }
    )
    provider_entity_id = result["entity_id"]
except ValueError:
    # Entity already exists (concurrent creation) — resolve to get entity_id
    candidates = memory_entity_resolve(name="Dr. Chen", entity_type="person")
    provider_entity_id = candidates[0]["entity_id"]

# Step 2: store edge-fact from user to provider
memory_store_fact(
    subject="user",
    predicate="doctor_primary_care",
    content="Dr. Chen",
    entity_id="<sender_entity_id>",        # user's entity from preamble
    object_entity_id=provider_entity_id,   # edge-fact: user → provider
    permanence="stable",
    importance=7.0,
    tags=["healthcare-provider"]
)
```

The provider entity appears in the dashboard "Unidentified Entities" section for the owner to
confirm, merge, or delete. **Never fall back to bare string subjects for external entities.**

---

## Health Domain Taxonomy

### Subject

- **User's own health facts**: use sender `entity_id` from REQUEST CONTEXT preamble; `subject` label is the user's name or `"user"`
- **Healthcare providers** (doctors, specialists): resolve to `person` entity; use provider name as `subject` label
- **Clinics/pharmacies/institutions**: resolve to `organization` entity; use institution name as `subject` label
- **Conditions/medications as topics**: these are attributes of the user — use condition or medication name as `subject` label, but anchor with the user's `entity_id`

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
| `doctor_name` | Healthcare provider name (fact on provider entity) |
| `doctor_primary_care` | User's primary care physician — edge-fact (user → provider entity) |
| `pharmacy` | Preferred pharmacy — edge-fact (user → pharmacy entity) |
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

### Medication started (user fact — anchor to sender's entity_id)

```python
# From: "Started taking Lisinopril 10mg daily for blood pressure"
# entity_id comes from sender preamble (user's entity)
memory_store_fact(
    subject="Lisinopril",
    predicate="medication",
    content="10mg daily for blood pressure management",
    entity_id="<sender_entity_id>",
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
    entity_id="<sender_entity_id>",
    permanence="stable",
    importance=9.0,
    tags=["allergy", "critical"]
)
```

### Symptom trigger pattern (user fact)

```python
# From: "Headaches usually happen when I don't drink enough water"
memory_store_fact(
    subject="headaches",
    predicate="symptom_trigger",
    content="triggered by dehydration",
    entity_id="<sender_entity_id>",
    permanence="standard",
    importance=6.0,
    tags=["symptom", "pattern"]
)
```

### Healthcare provider (resolve-or-create transitory entity)

```python
# From: "Dr. Chen is my primary care physician"
# Step 1: resolve or create provider entity (see Resolve-or-Create above)

# Step 2: store edge-fact from user to provider
memory_store_fact(
    subject="user",
    predicate="doctor_primary_care",
    content="Dr. Chen",
    entity_id="<sender_entity_id>",
    object_entity_id="<dr_chen_entity_id>",   # transitory entity for Dr. Chen
    permanence="stable",
    importance=7.0,
    tags=["healthcare-provider"]
)
```

### Pharmacy (resolve-or-create transitory organization)

```python
# From: "I use Walgreens on Main Street for prescriptions"
# Step 1: resolve or create pharmacy entity
candidates = memory_entity_resolve(name="Walgreens", entity_type="organization",
                                   context_hints={"topic": "pharmacy"})
# → use existing or create transitory (see butler-memory resolve-or-create protocol)

# Step 2: store edge-fact
memory_store_fact(
    subject="user",
    predicate="pharmacy",
    content="Walgreens on Main Street",
    entity_id="<sender_entity_id>",
    object_entity_id="<walgreens_entity_id>",
    permanence="stable",
    importance=6.0,
    tags=["healthcare-provider", "pharmacy"]
)
```

---

## Extraction Guidelines

- **Extract proactively** — capture facts from conversational messages even if they are incidental to the main request
- **Use permanence wisely** — chronic conditions and allergies are `stable`; acute symptoms are `volatile`
- **Privacy matters** — add `sensitive` or `private` tags for personal health information
- **Importance calibration** — allergies and critical conditions: 8-10; active medications: 7-8; patterns and baselines: 5-7; transient notes: 3-5
- **Anchor all external entities** — healthcare providers and pharmacies must be resolved or created as transitory entities before storing any facts about them; never use a bare string subject for an external entity
