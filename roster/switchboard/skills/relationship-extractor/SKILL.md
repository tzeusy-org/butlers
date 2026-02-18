---
name: relationship-extractor
description: Extract structured relationship data from incoming messages. Teaches the Switchboard's runtime instance how to identify contacts, interactions, life events, dates, facts, sentiments, gifts, and loans — and produce structured JSON that maps directly to Relationship butler tools.
---

# Relationship Extractor

## Purpose

When the Switchboard classifies an incoming message as relationship-relevant,
this skill tells the runtime instance **what to look for** and **how to structure
the extraction** so the Relationship butler can act on it immediately.

You are not calling tools yourself. You are producing structured JSON
extractions that the Switchboard will forward to the Relationship butler
via `route()`.

---

## Signal Taxonomy

Every incoming message may contain zero or more of these 8 signal types.
Extract **all** signals present — a single message can produce multiple
extractions.

| # | Signal Type    | What to Look For |
|---|----------------|------------------|
| 1 | **contact**    | A new person mentioned by name, with optional details (email, phone, role, company, location) |
| 2 | **interaction**| Evidence of a past or planned meeting, call, text, email, or social encounter with a named person |
| 3 | **life_event** | Milestone or change: new job, move, engagement, graduation, illness, retirement, promotion, baby |
| 4 | **date**       | Birthday, anniversary, wedding date, or any recurring calendar date tied to a person |
| 5 | **fact**       | A discrete piece of personal information: favorite food, allergy, hobby, pet name, preference |
| 6 | **sentiment**  | Emotional context about a relationship: "I'm worried about Sarah", "Alex and I had a great time" |
| 7 | **gift**       | Gift idea, purchase, or giving event tied to a person and optionally an occasion |
| 8 | **loan**       | Money lent or borrowed, with amount, direction, and optional description |

---

## Output Schema

For each signal detected, produce one JSON object following the schema for
that signal type. Return an array of all extractions.

### Envelope Format

```json
{
  "extractions": [
    {
      "signal_type": "<one of the 8 types>",
      "confidence": "HIGH" | "MEDIUM" | "LOW",
      "contact_hint": "<name of the person this relates to>",
      "data": { ... },
      "tool_mapping": {
        "tool": "<Relationship butler tool name>",
        "args": { ... }
      }
    }
  ]
}
```

Fields:

- **signal_type** — One of: `contact`, `interaction`, `life_event`, `date`,
  `fact`, `sentiment`, `gift`, `loan`.
- **confidence** — See the Confidence Scoring Rubric below.
- **contact_hint** — The person's name as mentioned in the message. Used for
  deduplication lookup before creating/updating records.
- **data** — The raw extracted data (human-readable, preserves original phrasing).
- **tool_mapping** — The exact Relationship butler tool to call and its arguments.
  The `contact_id` field should be set to `null` — the Switchboard will resolve
  it via `contact_search` before routing.

---

### Per-Type Schemas

#### 1. contact

Detected when a new person is introduced or details about a person are shared
for the first time.

```json
{
  "signal_type": "contact",
  "confidence": "HIGH",
  "contact_hint": "Sarah Chen",
  "data": {
    "name": "Sarah Chen",
    "details": {
      "email": "sarah@example.com",
      "phone": null,
      "company": "Acme Corp",
      "role": "engineering manager",
      "location": "San Francisco",
      "notes": "Met at the conference last week"
    }
  },
  "tool_mapping": {
    "tool": "contact_create",
    "args": {
      "name": "Sarah Chen",
      "details": {
        "email": "sarah@example.com",
        "company": "Acme Corp",
        "role": "engineering manager",
        "location": "San Francisco",
        "notes": "Met at the conference last week"
      }
    }
  }
}
```

**Tool signature:** `contact_create(pool, name: str, details: dict | None = None) -> dict`

If the contact already exists (resolved via `contact_search`), use
`contact_update` instead to merge new details.

**Update tool signature:** `contact_update(pool, contact_id: UUID, **fields) -> dict`
- Accepts `name` and `details` keyword arguments.

---

#### 2. interaction

Detected when someone describes a past or upcoming meeting, call, or social
encounter with a named person.

```json
{
  "signal_type": "interaction",
  "confidence": "HIGH",
  "contact_hint": "Jake",
  "data": {
    "type": "coffee",
    "summary": "Had coffee with Jake downtown, discussed his startup idea",
    "occurred_at": "2026-02-08T15:00:00Z"
  },
  "tool_mapping": {
    "tool": "interaction_log",
    "args": {
      "contact_id": null,
      "type": "coffee",
      "summary": "Had coffee with Jake downtown, discussed his startup idea",
      "occurred_at": "2026-02-08T15:00:00Z"
    }
  }
}
```

**Tool signature:** `interaction_log(pool, contact_id: UUID, type: str, summary: str | None = None, occurred_at: datetime | None = None) -> dict`

Common interaction types: `call`, `text`, `email`, `coffee`, `lunch`,
`dinner`, `meeting`, `video_call`, `party`, `visit`.

If the date/time is not mentioned, omit `occurred_at` (defaults to now).

---

#### 3. life_event

Detected when a significant life change is mentioned for a person: new job,
move, engagement, baby, graduation, retirement, illness, promotion, etc.

Life events produce **two extractions**: a note (to record the event) and
optionally a date (if a specific date is mentioned).

```json
{
  "signal_type": "life_event",
  "confidence": "HIGH",
  "contact_hint": "Maria",
  "data": {
    "event": "promotion",
    "description": "Maria got promoted to VP of Engineering at her company"
  },
  "tool_mapping": {
    "tool": "note_create",
    "args": {
      "contact_id": null,
      "content": "Life event: Maria got promoted to VP of Engineering at her company",
      "emotion": "happy"
    }
  }
}
```

**Tool signature:** `note_create(pool, contact_id: UUID, content: str, emotion: str | None = None) -> dict`

Emotion values to use for life events: `happy`, `proud`, `excited`,
`concerned`, `sad`, `neutral`.

---

#### 4. date

Detected when a birthday, anniversary, wedding date, or any recurring
calendar date is mentioned in connection with a person.

```json
{
  "signal_type": "date",
  "confidence": "HIGH",
  "contact_hint": "Dad",
  "data": {
    "label": "birthday",
    "month": 3,
    "day": 15,
    "year": 1965
  },
  "tool_mapping": {
    "tool": "date_add",
    "args": {
      "contact_id": null,
      "label": "birthday",
      "month": 3,
      "day": 15,
      "year": 1965
    }
  }
}
```

**Tool signature:** `date_add(pool, contact_id: UUID, label: str, month: int, day: int, year: int | None = None) -> dict`

Common labels: `birthday`, `anniversary`, `wedding`, `graduation`,
`memorial`, `name_day`.

If only month and day are mentioned, set `year` to `null`.

---

#### 5. fact

Detected when a discrete, specific piece of personal information is shared
about someone: favorite food, allergy, hobby, pet's name, clothing size,
preference, etc.

```json
{
  "signal_type": "fact",
  "confidence": "MEDIUM",
  "contact_hint": "Tom",
  "data": {
    "key": "favorite_cuisine",
    "value": "Thai food"
  },
  "tool_mapping": {
    "tool": "fact_set",
    "args": {
      "contact_id": null,
      "key": "favorite_cuisine",
      "value": "Thai food"
    }
  }
}
```

**Tool signature:** `fact_set(pool, contact_id: UUID, key: str, value: str) -> dict`

Use snake_case keys. Common keys: `favorite_food`, `favorite_cuisine`,
`favorite_color`, `favorite_drink`, `allergy`, `dietary_restriction`,
`hobby`, `pet_name`, `pet_type`, `clothing_size`, `shoe_size`,
`coffee_order`, `sports_team`, `music_taste`, `preferred_language`,
`nickname`, `employer`, `job_title`.

Facts are UPSERTed — setting the same key again overwrites the previous value.

---

#### 6. sentiment

Detected when the message conveys emotional context about a relationship or
person. This captures the *user's* feelings, not the contact's.

```json
{
  "signal_type": "sentiment",
  "confidence": "MEDIUM",
  "contact_hint": "Sarah",
  "data": {
    "emotion": "worried",
    "context": "Haven't heard from Sarah in weeks, hope she's doing okay"
  },
  "tool_mapping": {
    "tool": "note_create",
    "args": {
      "contact_id": null,
      "content": "Sentiment: Haven't heard from Sarah in weeks, hope she's doing okay",
      "emotion": "worried"
    }
  }
}
```

**Tool signature:** `note_create(pool, contact_id: UUID, content: str, emotion: str | None = None) -> dict`

Emotion values for sentiments: `happy`, `grateful`, `excited`, `proud`,
`nostalgic`, `worried`, `frustrated`, `sad`, `guilty`, `neutral`.

Sentiments are stored as notes with an emotion tag. Prefix the content with
`"Sentiment: "` to distinguish from regular notes.

---

#### 7. gift

Detected when a gift idea, purchase, or giving event is mentioned in
connection with a person.

```json
{
  "signal_type": "gift",
  "confidence": "HIGH",
  "contact_hint": "Mom",
  "data": {
    "description": "Silk scarf from that boutique she liked",
    "occasion": "birthday"
  },
  "tool_mapping": {
    "tool": "gift_add",
    "args": {
      "contact_id": null,
      "description": "Silk scarf from that boutique she liked",
      "occasion": "birthday"
    }
  }
}
```

**Tool signature:** `gift_add(pool, contact_id: UUID, description: str, occasion: str | None = None) -> dict`

If the gift has already been purchased or given, produce an additional
extraction using `gift_update_status` (requires the gift ID, which the
Switchboard resolves after creation):

**Status tool signature:** `gift_update_status(pool, gift_id: UUID, status: str) -> dict`

Gift pipeline statuses: `idea` -> `purchased` -> `wrapped` -> `given` -> `thanked`.

---

#### 8. loan

Detected when money lent or borrowed is mentioned between the user and a
named person.

```json
{
  "signal_type": "loan",
  "confidence": "HIGH",
  "contact_hint": "Alex",
  "data": {
    "amount": "50.00",
    "direction": "lent",
    "description": "Covered Alex's lunch at the Italian place"
  },
  "tool_mapping": {
    "tool": "loan_create",
    "args": {
      "contact_id": null,
      "amount": "50.00",
      "direction": "lent",
      "description": "Covered Alex's lunch at the Italian place"
    }
  }
}
```

**Tool signature:** `loan_create(pool, contact_id: UUID, amount: Decimal, direction: str, description: str | None = None) -> dict`

Direction is always from the user's perspective:
- `"lent"` — user gave money to the contact
- `"borrowed"` — user received money from the contact

Amount should be a decimal string (e.g., `"50.00"`, `"1200.50"`).

If the message mentions repayment/settling, use `loan_settle` instead:

**Settle tool signature:** `loan_settle(pool, loan_id: UUID) -> dict`

---

## Confidence Scoring Rubric

Assign a confidence level to each extraction based on how clearly the signal
is stated in the message.

### HIGH — Direct, explicit statement

The message explicitly states the information with no ambiguity.

| Signal | Example message | Why HIGH |
|--------|----------------|----------|
| contact | "I met Sarah Chen, she's an engineering manager at Acme Corp" | Name, role, and company all stated directly |
| interaction | "I had lunch with Jake yesterday" | Explicit interaction type, named person, time reference |
| date | "Mom's birthday is March 15th" | Named person, label, and exact date all stated |
| loan | "I lent Alex $50 for lunch" | Amount, direction, person, and context all explicit |
| gift | "I'm thinking of getting Mom a silk scarf for her birthday" | Item, person, and occasion all stated |

### MEDIUM — Implied or partially stated

The information is strongly implied but requires minor inference.

| Signal | Example message | Why MEDIUM |
|--------|----------------|------------|
| fact | "Jake always orders the pad thai" | Implies favorite dish, but "always" is an inference |
| sentiment | "I should really call Sarah back" | Implies guilt/concern, but no explicit emotion stated |
| life_event | "Did you hear about Maria's new title?" | Implies promotion but details are vague |
| interaction | "I ran into Tom at the store" | Casual encounter — unclear if meaningful interaction |

### LOW — Weak signal, speculative

The information requires significant inference or is mentioned in passing
with little context.

| Signal | Example message | Why LOW |
|--------|----------------|---------|
| contact | "Some guy named Dave was at the party" | Minimal information, may not be worth tracking |
| fact | "I think Tom mentioned he likes hiking" | Secondhand, uncertain |
| sentiment | "Whatever, it's fine" (about a person) | Extremely vague emotional signal |
| date | "I think her birthday is sometime in March" | No specific day |

### Rules

1. **When in doubt, go MEDIUM.** Only use LOW for truly speculative signals.
2. **Multiple signals reinforce each other.** If the same message mentions
   "had coffee with Jake" (interaction) and "he told me about his new job"
   (life event), both can be HIGH because the interaction context is clear.
3. **Never suppress LOW extractions.** Include them — the Switchboard can
   decide whether to act on LOW-confidence signals based on butler policy.
4. **Confidence is about signal clarity, not importance.** A LOW-confidence
   birthday is still important — it just means we are unsure about the data.

---

## Deduplication Hints

Before creating a new contact or adding data, the Switchboard should search
for existing contacts to avoid duplicates. The `contact_hint` field is the
primary key for this lookup.

### Name Matching Rules

1. **Exact match first.** Search for the name exactly as stated.
2. **Case-insensitive.** "sarah" matches "Sarah".
3. **First name only.** If the message says "Sarah" and there is exactly one
   contact named "Sarah Chen", treat it as a match.
4. **Nicknames and diminutives.** Common mappings to watch for:
   - Mom/Dad/Mama/Papa → search by relationship label or known parent names
   - Common diminutives: Tom/Thomas, Jake/Jacob, Mike/Michael, Liz/Elizabeth,
     Alex/Alexander/Alexandra, Sam/Samuel/Samantha, Dan/Daniel, Ben/Benjamin,
     Bob/Robert, Bill/William, Kate/Katherine, Jenny/Jennifer, Tony/Anthony
5. **Ambiguous match.** If `contact_search` returns multiple results for a
   first-name-only query, set confidence to MEDIUM and include all candidate
   names in a `"candidates"` field:
   ```json
   {
     "contact_hint": "Sarah",
     "candidates": ["Sarah Chen", "Sarah Miller"],
     "confidence": "MEDIUM"
   }
   ```
6. **No match.** If no contact is found, the extraction proceeds as a new
   contact creation (the `contact` signal type) combined with whatever other
   signal was detected.

### Merge Strategy

When an existing contact is found:

- **contact** signal → Use `contact_update` to merge new details into
  the existing contact's `details` JSONB. Do not overwrite existing values
  unless the new information is clearly a correction.
- **All other signals** → Use the resolved `contact_id` and proceed with
  the mapped tool.

---

## Multi-Signal Extraction Example

**Input message:**
> "Had dinner with Jake last night. His birthday is April 22nd. He mentioned
> he's allergic to shellfish. I still owe him $30 from last week. Maybe I
> should get him a nice bottle of whiskey for his birthday."

**Expected output:**

```json
{
  "extractions": [
    {
      "signal_type": "interaction",
      "confidence": "HIGH",
      "contact_hint": "Jake",
      "data": {
        "type": "dinner",
        "summary": "Had dinner with Jake last night",
        "occurred_at": "2026-02-08T19:00:00Z"
      },
      "tool_mapping": {
        "tool": "interaction_log",
        "args": {
          "contact_id": null,
          "type": "dinner",
          "summary": "Had dinner with Jake last night",
          "occurred_at": "2026-02-08T19:00:00Z"
        }
      }
    },
    {
      "signal_type": "date",
      "confidence": "HIGH",
      "contact_hint": "Jake",
      "data": {
        "label": "birthday",
        "month": 4,
        "day": 22,
        "year": null
      },
      "tool_mapping": {
        "tool": "date_add",
        "args": {
          "contact_id": null,
          "label": "birthday",
          "month": 4,
          "day": 22,
          "year": null
        }
      }
    },
    {
      "signal_type": "fact",
      "confidence": "HIGH",
      "contact_hint": "Jake",
      "data": {
        "key": "allergy",
        "value": "shellfish"
      },
      "tool_mapping": {
        "tool": "fact_set",
        "args": {
          "contact_id": null,
          "key": "allergy",
          "value": "shellfish"
        }
      }
    },
    {
      "signal_type": "loan",
      "confidence": "HIGH",
      "contact_hint": "Jake",
      "data": {
        "amount": "30.00",
        "direction": "borrowed",
        "description": "Owed from last week"
      },
      "tool_mapping": {
        "tool": "loan_create",
        "args": {
          "contact_id": null,
          "amount": "30.00",
          "direction": "borrowed",
          "description": "Owed from last week"
        }
      }
    },
    {
      "signal_type": "gift",
      "confidence": "MEDIUM",
      "contact_hint": "Jake",
      "data": {
        "description": "A nice bottle of whiskey",
        "occasion": "birthday"
      },
      "tool_mapping": {
        "tool": "gift_add",
        "args": {
          "contact_id": null,
          "description": "A nice bottle of whiskey",
          "occasion": "birthday"
        }
      }
    }
  ]
}
```

Note: The gift extraction is MEDIUM confidence because "maybe I should get"
is tentative, not a definite plan.

---

## Tool Signature Reference

Complete mapping of Relationship butler tools used by this skill:

| Tool | Signature | Used For |
|------|-----------|----------|
| `contact_create` | `(pool, name: str, details: dict \| None)` | New contact |
| `contact_update` | `(pool, contact_id: UUID, **fields)` | Merge details into existing contact |
| `contact_search` | `(pool, query: str)` | Deduplication lookup |
| `interaction_log` | `(pool, contact_id: UUID, type: str, summary: str \| None, occurred_at: datetime \| None)` | Log interactions |
| `note_create` | `(pool, contact_id: UUID, content: str, emotion: str \| None)` | Life events and sentiments |
| `date_add` | `(pool, contact_id: UUID, label: str, month: int, day: int, year: int \| None)` | Important dates |
| `fact_set` | `(pool, contact_id: UUID, key: str, value: str)` | Quick facts |
| `gift_add` | `(pool, contact_id: UUID, description: str, occasion: str \| None)` | Gift ideas |
| `gift_update_status` | `(pool, gift_id: UUID, status: str)` | Gift pipeline progression |
| `loan_create` | `(pool, contact_id: UUID, amount: Decimal, direction: str, description: str \| None)` | New loans |
| `loan_settle` | `(pool, loan_id: UUID)` | Settle existing loans |
