# Skill: Teaching Session

## Purpose

Single-concept teaching loop. Walk the mind map's frontier — pick the next concept whose
prerequisites are all mastered, teach it with Socratic scaffolding, quiz comprehension, record
mastery, and schedule a spaced repetition review. One session, one concept.

## When to Use

Use this skill when:
- The teaching flow state is `TEACHING` or `QUIZZING`
- A `review-{node_id}` scheduled task fires (spaced repetition review — prefer review-session skill)

## Token Budget

~2,000 output tokens per teaching session. Be concise and targeted. Do not over-explain.
One concept per session — do not chain into the next concept even if it seems natural.

## Teaching Loop

### Step 1: Select Concept

Call `curriculum_next_node(mind_map_id)` to get the highest-priority frontier node.

If no frontier nodes exist (all prerequisites unmastered): notify the user of the blocker and
exit. Do not skip prerequisites.

### Step 2: Memory Context

Call `memory_recall(topic=<concept_label>)` and `memory_search(query=<concept_label>)` to check
for any existing knowledge or prior struggle areas related to this concept.

### Step 3: Socratic Opening

Ask one opening question: "Before I explain [concept], what do you already know about it?"

Wait for the answer. Use it to calibrate:
- If strong answer → start deeper, skip basic scaffolding
- If partial answer → build on what they have
- If no answer → start from first principles with an analogy

### Step 4: Explanation

Explain the concept. Guidelines:
- Lead with a concrete analogy or example
- Follow with the abstract definition
- Show a working code example (for programming topics)
- Keep it focused — one concept, not a survey of related ideas

### Step 5: Comprehension Check (1-3 Questions)

Ask 1-3 quiz questions. Use a mix of:
- One factual recall question (can you define or identify it?)
- One application question (can you use it in a new context?)
- (Optional) One edge case or "what would happen if..." question for depth

Ask one question per message. Wait for each answer.

For each answer, score quality 0-5:
- 5: Correct, confident, demonstrates understanding
- 4: Correct with minor gaps
- 3: Essentially correct
- 2: Partially correct — missing a key insight
- 1: Largely incorrect but attempted
- 0: No answer or complete misunderstanding

Call `mastery_record_response(node_id, mind_map_id, question_text, user_answer, quality,
response_type="teach")` for each response.

### Step 6: Schedule Spaced Repetition

After the comprehension check, call `spaced_repetition_record_response(node_id, quality=<avg>)`
to schedule the first review interval.

### Step 7: Persist Learning Outcome

Call `memory_store_fact(subject=<concept>, predicate="learning_outcome", content=<what the user
demonstrated>, permanence=<based on mastery>, importance=<7.0 for solid, 5.0 for partial>)`.

If struggles were detected (quality <= 2 on any question), also call:
`memory_store_fact(subject=<concept>, predicate="struggle_area", content=<what was confusing>,
permanence="volatile", importance=6.0)`.

### Step 8: Advance Flow State

Call `teaching_flow_advance(mind_map_id)` to transition to QUIZZING or REVIEWING.

### Step 9: Exit

Notify the user of the next review timing. Exit. Do not start the next concept.

```python
notify(channel="telegram",
       message=f"[concept] covered. First review in {interval} days.",
       intent="reply",
       request_context=...)
```

## Exit Criteria

- Exactly one concept node has been taught
- 1-3 quiz responses recorded via `mastery_record_response()`
- Spaced repetition review scheduled via `spaced_repetition_record_response()`
- Learning outcome stored in memory
- Flow state advanced
- User notified of next review timing
