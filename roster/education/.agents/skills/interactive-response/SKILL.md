---
name: interactive-response
description: Education-butler response-mode selection and worked examples for teaching, quizzes, progress, reviews, and topic abandonment.
version: 1.0.0
tools_required:
  - teaching_flow_start
  - teaching_flow_abandon
  - mastery_record_response
  - mastery_get_map_summary
  - spaced_repetition_record_response
  - spaced_repetition_pending_reviews
  - mind_map_list
  - analytics_get_snapshot
  - memory_store_fact
  - notify
---

# Education Interactive Response Skill

## Purpose

Load this skill when an interactive education message needs a deliberate
response mode or a worked teaching, quiz, progress, review, or abandonment
pattern.

### Detection

Check context for a REQUEST CONTEXT JSON block. If present and `source_channel` is user-facing,
engage interactive response mode.

### Response Mode Selection

1. **React**: Emoji-only acknowledgment
   - Use when: The action is simple and self-explanatory
   - Example: User sends a quiz answer → React with ✅ or ❓

2. **Affirm**: Brief confirmation message
   - Use when: Need a short confirmation with the key fact
   - Example: "Got it, starting your Python learning path now."

3. **Follow-up**: Proactive question or observation
   - Use when: You need to continue the teaching dialogue or probe further
   - Example: "Before I explain recursion, what do you already know about it?"

4. **Answer**: Substantive response to a question
   - Use when: User asked a direct factual question about their learning progress
   - Example: "You've mastered 12 of 25 Python concepts. Your next topic is list comprehensions."

5. **React + Reply**: Combined emoji + message
   - Use when: You want immediate acknowledgment plus substantive content
   - Example: React with ✅ then "Correct! That's the key insight: [explanation of why it matters]."

### Complete Examples

#### Example 1: Starting a New Topic (Affirm + Follow-up)

**User message**: "Teach me Python"

**Actions**:
1. `teaching_flow_start(topic="Python")`
2. Transition to DIAGNOSING phase
3. `notify(channel="telegram", message="I'll start with a quick calibration to see where you are. What experience do you have with programming in general — any languages at all?", intent="reply", request_context=...)`

---

#### Example 2: Quiz Answer (React + Reply)

**User message**: "A list comprehension creates a new list by applying an expression to each item in an iterable"

**Actions**:
1. `mastery_record_response(node_id=<current_node>, quality=5, question_text=<question>, user_answer=<answer>, response_type="teach")`
2. `spaced_repetition_record_response(node_id=<current_node>, quality=5)`
3. `memory_store_fact(subject="Python list comprehensions", predicate="learning_outcome", content="user correctly defined list comprehension syntax and semantics", permanence="standard", importance=7.0, tags=["python", "comprehensions", "mastered"], entity_id=<node_entity_id>)`
4. `notify(channel="telegram", intent="react", emoji="✅", request_context=...)`
5. `notify(channel="telegram", message="Exactly right. You've nailed the definition. I'll quiz you on this again in 6 days to make sure it sticks.", intent="reply", request_context=...)`

---

#### Example 3: Struggle Detected (Follow-up)

**User message**: "I don't really get the difference between a generator and a list comprehension"

**Actions**:
1. `mastery_record_response(node_id=<generators_node>, quality=1, response_type="teach", ...)`
2. `memory_store_fact(subject="Python generators", predicate="struggle_area", content="confused about generator vs list comprehension semantics", permanence="volatile", importance=6.0, tags=["python", "generators", "struggle"], entity_id=<generators_node_entity_id>)`
3. `notify(channel="telegram", message="That's a really common sticking point. Let me try a different angle — can you tell me what happens to memory when you create a list of a million numbers? What about a generator of a million numbers?", intent="reply", request_context=...)`

---

#### Example 4: Progress Question (Answer)

**User message**: "How am I doing on Python?"

**Actions**:
1. `mind_map_list(status="active")`
2. `mastery_get_map_summary(mind_map_id=<python_map_id>)`
3. `analytics_get_snapshot(mind_map_id=<python_map_id>)`
4. `notify(channel="telegram", message="Python progress: 12/25 concepts mastered (48%). Your retention rate this week is 82% — solid. You're currently working through generators. Estimated completion at your current pace: ~14 days.", intent="reply", request_context=...)`

---

#### Example 5: Review Session Trigger

**Trigger**: Scheduled review (spaced repetition due)

**Actions**:
1. `spaced_repetition_pending_reviews(mind_map_id=<map_id>)`
2. For each due node: ask one recall question, wait for answer, score it
3. `spaced_repetition_record_response(node_id=<node_id>, quality=<score>)` for each node
4. `notify(channel="telegram", message="Review session done — 5 concepts reviewed. 4 correct, 1 needs more work (closures). Next review in 6 days.", intent="reply", request_context=...)`

---

#### Example 6: Abandoning a Topic

**User message**: "I want to stop studying machine learning for now"

**Actions**:
1. `mind_map_list(status="active")`: find machine learning map
2. `teaching_flow_abandon(mind_map_id=<ml_map_id>)`
3. `memory_store_fact(subject="machine learning", predicate="study_pattern", content="user paused machine learning study — 8/30 concepts mastered", permanence="volatile", importance=5.0, tags=["machine-learning", "paused"], entity_id=<ml_map_entity_id>)`
4. `notify(channel="telegram", intent="react", emoji="✅", request_context=...)`
5. `notify(channel="telegram", message="Machine learning study paused. You'd covered 8/30 concepts — I'll keep your progress. Say 'resume machine learning' whenever you're ready to pick it up.", intent="reply", request_context=...)`
