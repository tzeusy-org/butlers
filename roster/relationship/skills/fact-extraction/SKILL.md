---
name: fact-extraction
description: 7-step Conversational Fact Extraction Pipeline — resolve person mentions to entities, apply disambiguation policy, extract and store facts, log interactions, and update domain records. Includes question answering flow and 8 complete examples.
version: 1.0.0
tags: [relationship, memory, extraction, entity-resolution]
---

# Conversational Fact Extraction Pipeline

When processing messages with a REQUEST CONTEXT present (routed from Switchboard), always follow this extraction pipeline for every person mentioned.

## Step 1: Identify Person Mentions

Scan the message for people mentioned by name (first name, full name, nickname, or relational label like "Mom", "my boss"). Collect all mentions before proceeding.

## Step 2: Resolve Each Mention to an Entity

For each person mentioned, call:

```python
entity_resolve(
    name="<mention>",
    entity_type="person",
    context_hints={
        "topic": "<conversation topic>",
        "mentioned_with": ["<other names in message>"],
        "domain_scores": {"<entity_id>": <salience_score>, ...}  # from contact_resolve if available
    }
)
```

Salience scores can be obtained by first calling `contact_resolve(name, context)`, which returns candidates with salience scores that you can pass as `domain_scores`.

## Step 3: Apply Disambiguation Policy

Use the resolution thresholds from the spec (§10.4):

| Result | Behavior |
|--------|----------|
| **Zero candidates** (NONE) | Person is unknown. See "New People" section below. |
| **Single candidate** (HIGH) | Use `entity_id` directly. Proceed silently. |
| **Multiple candidates, top score leads by ≥30 points** (HIGH, inferred) | Use top `entity_id`. Confirm transparently: *"Assuming you're referring to [Name] ([reason]) — ..."* Include `inferred_reason` in confirmation. |
| **Multiple candidates, gap <30 points** (MEDIUM) | Ask the user: *"Did you mean [Candidate A] or [Candidate B]?"* Do not store facts until clarified. |

## Step 4: Handle New People (NONE confidence)

When `entity_resolve` returns zero candidates:

- **If sufficient identifying info (full name or enough context):**
  1. Call `entity_create(canonical_name="<full name>", entity_type="person", aliases=["<first name>", "<nickname if known>"])`
  2. Optionally call `contact_create(...)` and store the returned `entity_id` if the person seems like a recurring contact
  3. Proceed with the new `entity_id`

- **If only a first name or minimal info:**
  1. Call `entity_create(canonical_name="<first name>", entity_type="person")` to establish a minimal entity
  2. Defer contact creation until more information is available
  3. Proceed with the new `entity_id`

## Step 5: Extract and Store Facts with entity_id

Extract relationship-relevant facts from the message and store each one using the resolved `entity_id`:

```python
memory_store_fact(
    subject="<human-readable name>",  # label only, for readability
    predicate="<predicate>",
    content="<fact content>",
    entity_id="<resolved entity_id>",  # REQUIRED — anchor to entity, not raw name
    permanence="<permanence level>",
    importance=<float>,
    tags=["<tag1>", "<tag2>"]
)
```

**Never store facts with only a raw subject string.** The `entity_id` ensures facts about "Chloe", "Chloe Wong", and "Chlo" all resolve to the same identity.

## Step 6: Log Interactions

When the message implies the user interacted with a person (met, called, had lunch, etc.), log the interaction using the resolved `contact_id`:

```python
interaction_log(contact_id="<contact_id>", interaction_type="<type>", summary="<summary>")
```

## Step 7: Update Domain Records

When extracted facts map to structured fields, update both memory and domain records:

- Birthday mention → `date_add(contact_id, date_type="birthday", ...)` + `memory_store_fact(..., entity_id=...)`
- Location mention → update contact address + `memory_store_fact(..., entity_id=...)`
- Life event (new job, move, baby) → `life_event_log(contact_id, ...)` + `memory_store_fact(..., entity_id=...)`

## Memory Classification

### Relationship Domain Taxonomy

**Subject**: Person's human-readable name (used as label; entity_id is the actual anchor)

**Predicates** (examples):
- `relationship_to_user`: "friend", "colleague", "brother", "Mom"
- `birthday`: "March 15, 1985" or "March 15" (year optional)
- `anniversary`: Date-based milestones
- `preference`: Food, activities, interests, dislikes
- `current_interest`: Hobbies, projects, topics they're exploring
- `contact_phone`: Phone number
- `contact_email`: Email address
- `workplace`: Company or organization name
- `lives_in`: City or location
- `relationship_status`: "married", "single", "dating"
- `children`: Names and ages
- `nickname`: Preferred name or alias

**Permanence levels**:
- `permanent`: Identity facts unlikely to change (e.g., birthday, family relationships)
- `stable`: Facts that change slowly (e.g., workplace, location, relationship status)
- `standard` (default): Current interests, preferences, ongoing projects
- `volatile`: Temporary states or rapidly changing information

**Tags**: Use tags for cross-cutting concerns like `gift-ideas`, `sensitive`, `work-related`, `family`

### Example Facts (with entity_id)

```python
# From: "Sarah mentioned she's allergic to shellfish"
# Step 1: entity_resolve("Sarah", entity_type="person", ...) → entity_id="uuid-sarah"
memory_store_fact(
    subject="Sarah",
    predicate="food_allergy",
    content="allergic to shellfish",
    entity_id="uuid-sarah",  # resolved entity_id
    permanence="stable",
    importance=7.0,
    tags=["health", "dietary"]
)

# From: "John just started learning guitar"
# Step 1: entity_resolve("John", entity_type="person", ...) → entity_id="uuid-john"
memory_store_fact(
    subject="John",
    predicate="current_interest",
    content="learning guitar (started recently)",
    entity_id="uuid-john",  # resolved entity_id
    permanence="standard",
    importance=5.0,
    tags=["hobbies"]
)

# From: "Mom's birthday is March 15th"
# Step 1: entity_resolve("Mom", entity_type="person", ...) → entity_id="uuid-mom"
memory_store_fact(
    subject="Mom",
    predicate="birthday",
    content="March 15",
    entity_id="uuid-mom",  # resolved entity_id
    permanence="permanent",
    importance=9.0,
    tags=["important-dates", "family"]
)
```

## Question Answering

When the user asks a question about a contact or relationship:

1. **Search memory first**: Use `memory_recall(topic=<person_name>)` or `memory_search(query=<question>)` to find relevant facts
2. **Use domain tools**: Query contact data with `contact_get()`, `note_search()`, `date_list()`, etc.
3. **Combine sources**: Synthesize information from memory and domain tools
4. **Respond with notify()**: Use the "answer" intent to provide the information

Example flow:
```
User: "What does Alice like?"
1. entity_resolve("Alice", entity_type="person") → entity_id="uuid-alice"
2. memory_recall(topic="Alice", limit=10)
3. contact_get(name="Alice")
4. note_search(query="Alice preferences")
5. Synthesize: "Alice loves hiking and specialty coffee. She mentioned wanting to visit Iceland."
6. notify(channel="telegram", message=<answer>, intent="reply", request_context=<from session>)
```

## Complete Examples

### Example 1: Simple Fact Logging (React)

**User message**: "Sarah's birthday is June 10th"

**Actions**:
1. `entity_resolve("Sarah", entity_type="person", context_hints={...})` → returns `entity_id="<uuid>"`
   - Single candidate (HIGH): proceed silently
2. `date_add(contact_id="<contact_id>", date_type="birthday", month=6, day=10)`
3. `memory_store_fact(subject="Sarah", predicate="birthday", content="June 10", entity_id="<uuid>", permanence="permanent", importance=9.0, tags=["important-dates"])`
4. `notify(channel="telegram", intent="react", emoji="✅", request_context=...)`

### Example 2: Conversational Context (Affirm)

**User message**: "Had lunch with Alex today, we talked about his new startup"

**Actions**:
1. `entity_resolve("Alex", entity_type="person", context_hints={"topic": "startup, lunch"})` → `entity_id="<uuid>"`, single match
2. `interaction_log(contact_id="<contact_id>", interaction_type="meal", summary="Discussed his new startup")`
3. `memory_store_fact(subject="Alex", predicate="current_project", content="working on a new startup", entity_id="<uuid>", permanence="standard", importance=6.0)`
4. `note_create(contact_id="<contact_id>", body="Discussed his new startup over lunch", emotion="positive")`
5. `notify(channel="telegram", message="Logged your lunch with Alex. I noted his startup project.", intent="reply", request_context=...)`

### Example 3: Question Answering (Answer)

**User message**: "When is Mom's birthday?"

**Actions**:
1. `entity_resolve("Mom", entity_type="person")` → `entity_id="<uuid>"`
2. `memory_recall(topic="Mom birthday")`
3. `date_list(contact_id="<contact_id>")`
4. Find birthday: March 15
5. `notify(channel="telegram", message="Mom's birthday is March 15th. Would you like a reminder?", intent="reply", request_context=...)`

### Example 4: Multi-step with Follow-up

**User message**: "Gift idea for Lisa: that book she mentioned"

**Actions**:
1. `entity_resolve("Lisa", entity_type="person")` → `entity_id="<uuid>"`
2. `gift_add(contact_id="<contact_id>", description="Book she mentioned", status="idea")`
3. `memory_search(query="Lisa book")`
4. Check if there's a specific book reference in memory
5. If found: `notify(channel="telegram", message="Saved gift idea: [specific book title]. Shall I mark it when you purchase?", intent="reply", request_context=...)`
6. If not found: `notify(channel="telegram", message="Gift idea saved. Do you remember which book Lisa mentioned?", intent="reply", request_context=...)`

### Example 5: Complex Fact Extraction (React + Reply)

**User message**: "Met with John and Sarah for dinner. John mentioned he's moving to Seattle next month for a new job at Amazon. Sarah said she might visit."

**Actions**:
1. `entity_resolve("John", entity_type="person", context_hints={"topic": "dinner, Seattle, Amazon", "mentioned_with": ["Sarah"]})` → `entity_id="<uuid-john>"`, single match
2. `entity_resolve("Sarah", entity_type="person", context_hints={"topic": "dinner, visit", "mentioned_with": ["John"]})` → `entity_id="<uuid-sarah>"`, single match
3. `interaction_log(contact_id="<john_contact_id>", interaction_type="meal", summary="Dinner with Sarah. John moving to Seattle for Amazon job.")`
4. `interaction_log(contact_id="<sarah_contact_id>", interaction_type="meal", summary="Dinner with John. Mentioned might visit.")`
5. `memory_store_fact(subject="John", predicate="workplace", content="Amazon (starting next month)", entity_id="<uuid-john>", permanence="stable", importance=8.0, tags=["work", "major-change"])`
6. `memory_store_fact(subject="John", predicate="lives_in", content="Seattle (moving next month)", entity_id="<uuid-john>", permanence="stable", importance=8.0, tags=["location", "major-change"])`
7. `memory_store_fact(subject="Sarah", predicate="travel_intent", content="might visit (context: John's move)", entity_id="<uuid-sarah>", permanence="volatile", importance=4.0)`
8. `notify(channel="telegram", intent="react", emoji="✅", request_context=...)`
9. `notify(channel="telegram", message="Logged dinner with John and Sarah. Noted John's move to Amazon in Seattle next month. Should I set a reminder to check in with him after the move?", intent="reply", request_context=...)`

### Example 6: Ambiguous Name — Inferred Resolution (HIGH confidence)

**User message**: "Chloe came by today, we had coffee"

**Actions**:
1. `entity_resolve("Chloe", entity_type="person", context_hints={"domain_scores": {"<uuid-chloe-wong>": 95, "<uuid-chloe-tan>": 12}})` → two candidates, top leads by 83 points → HIGH confidence, `inferred=true`, `inferred_reason="partner, most frequent contact"`
2. Use `entity_id="<uuid-chloe-wong>"`
3. `interaction_log(contact_id="<chloe_wong_contact_id>", interaction_type="coffee", summary="Came by, had coffee")`
4. `notify(channel="telegram", message="Assuming you're referring to Chloe Wong (your partner) — logged your coffee catch-up.", intent="reply", request_context=...)`

### Example 7: Ambiguous Name — Ask User (MEDIUM confidence)

**User message**: "Alex got promoted!"

**Actions**:
1. `entity_resolve("Alex", entity_type="person", context_hints={"topic": "promotion"})` → two candidates, gap <30 points → MEDIUM confidence
2. Do NOT store facts yet.
3. `notify(channel="telegram", message="Did you mean Alex Chen or Alex Rivera?", intent="reply", request_context=...)`
4. Wait for user clarification before proceeding.

### Example 8: Unknown Person (NONE — New Entity)

**User message**: "I met someone new today — Marcus Webb, he's a product designer at Figma"

**Actions**:
1. `entity_resolve("Marcus Webb", entity_type="person")` → zero candidates
2. Enough info (full name) → `entity_create(canonical_name="Marcus Webb", entity_type="person", aliases=["Marcus"])` → `entity_id="<uuid-marcus>"`
3. `contact_create(first_name="Marcus", last_name="Webb", job_title="Product Designer", company="Figma")` → store returned `entity_id` on contact
4. `memory_store_fact(subject="Marcus Webb", predicate="workplace", content="Product designer at Figma", entity_id="<uuid-marcus>", permanence="stable", importance=6.0, tags=["work"])`
5. `notify(channel="telegram", message="Added Marcus Webb to your contacts — product designer at Figma.", intent="reply", request_context=...)`

## Guidelines

- **Always respond** when `request_context` is present — silence feels like failure
- **Be concise** — users are on mobile devices
- **Resolve before storing** — always call entity_resolve before memory_store_fact; never store facts with only a raw subject string
- **Extract liberally** — capture facts even if tangential to the main request
- **Use tags** — they enable rich cross-cutting queries later
- **Permanence matters** — stable facts (workplace, location) need different TTL than volatile facts (mood, temporary interests)
- **Questions deserve answers** — always use memory + domain tools to provide substantive responses
- **Proactive follow-ups** — offer to set reminders, create events, or track related information
- **Confirm inferred resolutions** — when `inferred=true`, always mention the resolved name and reason to the user
- **Ask on ambiguity** — when MEDIUM confidence (gap <30 points), ask before acting; don't guess
