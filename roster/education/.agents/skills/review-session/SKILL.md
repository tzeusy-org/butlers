# Skill: Review Session

## Purpose

Spaced repetition review protocol. When scheduled review prompts fire, quiz the user on due
concepts, record SM-2 quality scores, reschedule the next review interval, and update mastery
state. One session handles up to 20 due nodes (batched).

## When to Use

Use this skill when:
- A `review-{node_id}-rep{N}` scheduled task fires (individual node review)
- A `review-{mind_map_id}-batch` scheduled task fires (batched review for the map)
- The teaching flow state is `REVIEWING`

## Token Budget

~500 output tokens per review session. Keep questions brief and focused. This is recall testing,
not re-teaching. Do not explain concepts unless the user answers incorrectly twice in a row.

## Review Loop

### Step 1: Get Due Nodes

Call `spaced_repetition_pending_reviews(mind_map_id)` to get nodes with `next_review_at <= now`.

The result is ordered by `next_review_at ASC` — most overdue first. The tool returns all due
nodes; cap your processing at 20.

**Batch handling (> 20 due nodes):**
If the result has more than 20 entries, process only the first 20 (most overdue). Notify the
user upfront how many are pending:

```python
notify(
    channel="telegram",
    message=f"{total_due} concepts are due for review. I'll cover {min(20, total_due)} now — "
            f"we'll catch the rest in the next session.",
    intent="send",
    request_context=<session_request_context>
)
```

**Priority within batch:** The tool orders by `next_review_at ASC`, so the most overdue nodes
are naturally first. Within ties, nodes with lower `ease_factor` (harder to remember) are
prioritized.

### Step 2: For Each Due Node (One at a Time)

For each of the (up to 20) due nodes:

1. **Vary question format** — check `mastery_get_node_history(node_id, limit=3)` to see the
   last 3 questions asked. Use a different format this session:
   - Definition: "In one sentence, what is [concept]?"
   - Application: "Given [scenario], how does [concept] apply?"
   - Analogy completion: "Complete this analogy: [concept] is to [X] as [Y] is to..."
   - Fill-in-the-blank: "The key property of [concept] is ___."

2. Deliver the question:
   ```python
   notify(channel="telegram", intent="send", message=<recall_question>, request_context=...)
   ```

3. Wait for the user's answer.

4. Score quality 0–5 using the standard rubric (see below).

5. Call:
   ```
   spaced_repetition_record_response(
       node_id=<node_id>,
       mind_map_id=<mind_map_id>,
       quality=<score>
   )
   ```
   This runs the SM-2 algorithm, updates `ease_factor`, `repetitions`, and `next_review_at`,
   and creates the next scheduled review automatically.

6. Give brief feedback (see Step 3 below).

### Quality Scoring Rubric

| Score | Meaning |
|-------|---------|
| 5 | Correct, immediate, confident — perfect recall |
| 4 | Correct with slight hesitation or minor gap |
| 3 | Correct but slow or needed slight prompting |
| 2 | Partially correct — missing a key element |
| 1 | Mostly wrong but showed some familiarity with the concept |
| 0 | Complete failure to recall — blackout |

### Step 3: Brief Feedback After Each Node (Not Re-teaching)

After scoring each response:

**Quality >= 3 (recalled):**
```python
notify(channel="telegram", intent="react", emoji="✅", request_context=...)
# optionally: brief positive note if the answer was particularly good
```

**Quality < 3 (failed recall):**
Provide the correct answer in 1–2 sentences. Do not re-teach in depth.

```python
notify(
    channel="telegram",
    message=f"Not quite — [brief correct answer in 1-2 sentences]. "
            f"I'll schedule a follow-up review soon.",
    intent="reply",
    request_context=...
)
```

**Repeated failure detection:** If the user has scored < 3 on the same concept in 3+ consecutive
review sessions (check `mastery_get_node_history()`), record a persistent struggle flag:

```python
memory_store_fact(
    subject=<concept_label>,
    predicate="struggle_area",
    content=f"Consistently failing reviews — scored < 3 in last 3+ review sessions",
    permanence="volatile",
    importance=7.0,
    tags=[<topic_tag>, "struggle", "review-failure"]
)
```

Then suggest revisiting the teaching session:
```python
notify(
    channel="telegram",
    message=f"You've had difficulty with [concept] in several review sessions. "
            f"Would you like me to re-teach it in depth?",
    intent="reply",
    request_context=...
)
```

### Step 4: Advance Flow State

After processing all due nodes (up to 20), check the frontier state:

```
# Check if any unmastered nodes remain with prerequisites satisfied
next_node = curriculum_next_node(mind_map_id)
```

- If `next_node` is not None (frontier has unmastered nodes):
  Call `teaching_flow_advance(mind_map_id)` → transitions to `TEACHING`
- If `next_node` is None (all nodes mastered):
  Call `teaching_flow_advance(mind_map_id)` → transitions to `COMPLETED`

### Step 5: Summary Notification

After advancing flow state, notify the user of the session outcome:

```python
# Count: correct = nodes where quality >= 3
notify(
    channel="telegram",
    message=f"Review session complete — {reviewed_count} concepts covered. "
            f"{correct_count}/{reviewed_count} recalled correctly. "
            f"{'Keep it up!' if correct_count == reviewed_count else f'{struggling_labels} needs more work.'}",
    intent="reply",
    request_context=<session_request_context>
)
```

## Exit Criteria

- `spaced_repetition_pending_reviews()` was called to get due nodes
- All due nodes (up to 20) have been quizzed, one at a time
- `spaced_repetition_record_response()` called for each node with the correct quality score
- Next review interval scheduled for each node (handled by the tool)
- Repeated-failure struggle flags recorded for any node with 3+ consecutive review failures
- Flow state advanced via `teaching_flow_advance()`
- User notified of session outcome and any struggling concepts via `notify()`
- Session exits without teaching new concepts
