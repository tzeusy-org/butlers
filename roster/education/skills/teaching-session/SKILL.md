# Skill: Teaching Session

## Purpose

Single-concept teaching loop. Walk the mind map's frontier — pick the next concept whose
prerequisites are all mastered, teach it with Socratic scaffolding, quiz comprehension, record
mastery, and schedule a spaced repetition review. One session, one concept.

## When to Use

Use this skill when:
- The teaching flow state is `TEACHING` or `QUIZZING`
- A scheduled teaching trigger fires for a flow in `TEACHING` status

## Token Budget

~2,000 output tokens per teaching session. Be concise and targeted. Do not over-explain.
One concept per session — do not chain into the next concept even if it seems natural.

## Core Behavioral Rules

### One Question Per Message

Never ask multiple questions in the same message. Ask one question, wait for the answer, then
continue. This is especially critical in the diagnostic probe and quiz phases.

### Socratic Before Direct

When a user asks "what is X?", do not immediately explain X. Ask what they already know about X,
what context they are coming from, or why they are curious. Use their answer to calibrate:
- If they know nothing: start from first principles with a concrete analogy.
- If they have partial knowledge: build on what they have; skip the scaffolding they do not need.
- If they know it well: skip basics; start deeper.

Socratic questioning reveals understanding in ways that passive reception does not. Understanding
demonstrated through dialogue sticks longer than explanation received.

### Positive Reinforcement Protocol

- **Correct on first attempt**: acknowledge specifically — "Exactly — [paraphrase their key insight]"
- **Correct after struggle**: make the progress visible — "That's right! You got there — [connect it to the concept]"
- **Incorrect**: never say "wrong." Say "not quite — let's think about [guiding question]" and give a
  Socratic nudge toward the answer. Never give the answer directly — let the user arrive there.

## Teaching Loop

### Step 0: Read Flow State

Call `teaching_flow_get(mind_map_id)` to read the current flow state. Note:
- `status`: Should be `TEACHING` or `QUIZZING`
- `current_node_id`: The node being taught (non-null when status is `TEACHING`)
- `current_phase`: `explaining`, `questioning`, or `evaluating`

If resuming a partially-complete session (e.g., `current_phase = "questioning"`), skip to the
appropriate step below.

### Step 1: Select Concept

If `current_node_id` is set in the flow state, use that node. Otherwise:

Call `curriculum_next_node(mind_map_id)` to get the highest-priority frontier node.

If no frontier nodes exist (all prerequisites unmastered or all concepts mastered): notify the
user of the current state and exit. Do not skip prerequisites.

### Step 2: Memory Context

Call `memory_recall(topic=<concept_label>)` and `memory_search(query=<concept_label>)` to check
for any existing knowledge or prior struggle areas related to this concept. This informs how
deep to start the explanation.

### Step 3: Socratic Opening (Explaining Phase)

Ask one opening question via `notify()`:

```
"Before I explain [concept], what do you already know about it?"
```

Wait for the answer. Use it to calibrate explanation depth:
- Strong answer (prior knowledge evident) → start deeper, skip basic scaffolding
- Partial answer (some familiarity) → build on what they have
- No answer / "nothing" → start from first principles with a concrete analogy

Deliver the opening via:
```python
notify(
    channel="telegram",
    message="Before I explain [concept], what do you already know about it?",
    intent="send",
    request_context=<session_request_context>
)
```

### Step 4: Explanation

After receiving the Socratic probe answer, explain the concept clearly:
- Lead with a concrete analogy or real-world example
- Follow with the precise definition
- For programming topics: include a working code example
- Keep it focused on one concept — do not survey related ideas
- Deliver via `notify(channel="telegram", intent="reply", ...)`

### Step 5: Comprehension Check (1–3 Questions, Questioning Phase)

Ask 1–3 quiz questions. One per message. Wait for each answer.

Question types to use:
- **Factual recall**: "Can you define X?" or "What does X do?"
- **Application**: "Given this code/scenario, what happens?"
- **Edge case** (optional, for depth): "What would happen if..."

Quality scoring rubric for each answer:

| Score | Meaning |
|-------|---------|
| 5 | Correct, confident, demonstrates understanding |
| 4 | Correct with minor gaps or slight hesitation |
| 3 | Essentially correct — core right, minor detail missing |
| 2 | Partially correct — missing a key insight |
| 1 | Largely incorrect but clearly attempted |
| 0 | No meaningful answer or complete misunderstanding |

For each answer, call:
```
mastery_record_response(
    node_id=<current_node_id>,
    mind_map_id=<mind_map_id>,
    question_text=<the question asked>,
    user_answer=<user's answer>,
    quality=<0-5 score>,
    response_type="teach"
)
```

**Feedback protocol:**
- Quality >= 3: React with emoji acknowledgment + brief positive note
  (`notify(intent="react", emoji="✅", ...)` then `notify(intent="reply", ...)`)
- Quality < 3: Never say "wrong." Use a Socratic nudge:
  "Not quite — let's think about [guiding question]" then redirect

### Step 6: Schedule Spaced Repetition Review

After all comprehension questions are answered, call:
```
spaced_repetition_record_response(
    node_id=<current_node_id>,
    mind_map_id=<mind_map_id>,
    quality=<average_quality_across_questions>
)
```

This runs the SM-2 algorithm and schedules the first review interval. The returned
`interval_days` tells you when the next review is due.

### Step 7: Persist Learning Outcome

Call `memory_store_fact()` to record what the user demonstrated:

```python
memory_store_fact(
    subject=<concept_label>,
    predicate="learning_outcome",
    content=<brief summary of what the user understood or got right>,
    permanence=<"stable" for transferable skills, "standard" for topic-specific knowledge>,
    importance=<7.0 for solid mastery, 5.0 for partial understanding>,
    tags=[<topic_tag>, <"mastered" or "learning">]
)
```

If any question had quality <= 2, also record the struggle:
```python
memory_store_fact(
    subject=<concept_label>,
    predicate="struggle_area",
    content=<what specifically confused the user>,
    permanence="volatile",
    importance=6.0,
    tags=[<topic_tag>, "struggle"]
)
```

### Step 8: Advance Flow State

Call `teaching_flow_advance(mind_map_id)` to transition to `QUIZZING` (if additional quiz
questions remain) or `REVIEWING` (based on frontier state and SM-2 schedule).

### Step 9: Exit

Notify the user of the next review timing and exit:

```python
notify(
    channel="telegram",
    message=f"[concept] covered. Well done! I'll check back with you in {interval_days} days "
            f"to make sure it sticks.",
    intent="reply",
    request_context=<session_request_context>
)
```

Do not start the next concept. The next session handles the next frontier node.

## Exit Criteria

- Exactly one concept node has been taught in this session
- 1–3 quiz responses recorded via `mastery_record_response(response_type="teach")`
- Spaced repetition review scheduled via `spaced_repetition_record_response()`
- Learning outcome stored in memory via `memory_store_fact()`
- Struggle area recorded (if any quality <= 2 response occurred)
- Flow state advanced via `teaching_flow_advance()`
- User notified of next review timing via `notify()`
- Session exits without teaching a second concept
