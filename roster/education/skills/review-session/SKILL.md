# Skill: Review Session

## Purpose

Spaced repetition review protocol. When scheduled review prompts fire, quiz the user on due
concepts, record SM-2 quality scores, reschedule the next review interval, and update mastery
state. One session handles up to 20 due nodes (batched).

## When to Use

Use this skill when:
- A `review-{node_id}-rep{N}` scheduled task fires
- A `review-{mind_map_id}-batch` scheduled task fires (batched review)
- The teaching flow state is `REVIEWING`

## Token Budget

~500 output tokens per review session. Keep questions brief and focused. This is recall, not
re-teaching. Do not explain concepts unless the user answers incorrectly twice in a row.

## Review Loop

### Step 1: Get Due Nodes

Call `spaced_repetition_pending_reviews(mind_map_id)` to get nodes with `next_review_at <= now`.

Cap at 20 nodes per session. If more than 20 are due, batch them — notify the user how many
total are pending and process 20 now.

### Step 2: For Each Due Node (One at a Time)

1. Generate a focused recall question for the concept:
   - Prefer varied formats: definition, application, analogy completion, fill-in-the-blank
   - Never reuse the exact same question format from prior sessions (check `mastery_get_node_history()`)
2. Ask the question. One question per message.
3. Wait for the answer.
4. Score quality 0-5 using the standard rubric.
5. Call `spaced_repetition_record_response(node_id, quality=<score>)` — this runs SM-2,
   updates `ease_factor`, `repetitions`, and `next_review_at`, and creates the next scheduled review.

### Quality Scoring Rubric

- 5: Correct, immediate, confident
- 4: Correct with slight hesitation or minor gap
- 3: Correct but slow or with prompting
- 2: Partially correct — missing key element
- 1: Mostly wrong but showed some familiarity
- 0: Complete failure to recall

### Step 3: Brief Feedback (Not Re-teaching)

After scoring:
- Quality >= 3: Acknowledge briefly ("Correct") or with a positive note ("Exactly right")
- Quality < 3: Provide the correct answer in 1-2 sentences. Do not re-teach in depth.
  If the user has scored < 3 on the same concept 3+ sessions in a row, flag it:
  `memory_store_fact(subject=<concept>, predicate="struggle_area", content=<pattern>, permanence="volatile")`
  and suggest revisiting the teaching session for this concept.

### Step 4: Advance Flow State

After processing all due nodes:
- If frontier has unmastered nodes: call `teaching_flow_advance(mind_map_id)` → TEACHING
- If all nodes mastered: call `teaching_flow_advance(mind_map_id)` → COMPLETED
- Notify the user of the review outcome:

```python
notify(channel="telegram",
       message=f"Review done — {N} concepts reviewed. {correct}/{N} correct. Next review in {days} days.",
       intent="reply",
       request_context=...)
```

## Exit Criteria

- All due nodes (up to 20) have been quizzed
- SM-2 quality recorded for each via `spaced_repetition_record_response()`
- Next review interval scheduled for each node
- Flow state advanced
- User notified of session outcome and next review timing
