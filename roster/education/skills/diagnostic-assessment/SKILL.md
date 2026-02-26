# Skill: Diagnostic Assessment

## Purpose

Pre-teaching calibration system. Before teaching a topic, run an adaptive probe sequence to
infer the user's existing knowledge level. Map diagnostic results onto mind map nodes as initial
mastery seeds so teaching starts at the right depth — not from scratch if the user already knows
foundations, not ahead of prerequisites if they do not.

## When to Use

Use this skill when:
- A new teaching flow starts and the flow state is `DIAGNOSING`
- The user says "teach me [topic]" for a topic with no existing mind map

## Protocol

### Step 1: Generate Concept Inventory

Generate 10-15 key concepts for the topic, spanning beginner to expert:
- 3-4 foundational concepts (beginner)
- 4-6 intermediate concepts
- 3-5 advanced concepts

Do not show this inventory to the user. Use it internally to drive the probe sequence.

### Step 2: Adaptive Probe Sequence

1. Start at the median difficulty concept from the inventory.
2. Ask one short probe question (multiple choice OR short answer — never both at once).
3. Wait for the answer before asking the next question.
4. If the answer reveals understanding → probe a harder concept.
5. If the answer reveals confusion → probe an easier concept.
6. Repeat 3-7 times. Binary search converges in 3-4 questions; add 1-3 targeted probes for
   ambiguous areas.

### Question Format Constraints

- One question per message. Never bundle.
- Use multiple choice (A/B/C/D) for factual recall.
- Use short-answer prompts for conceptual understanding.
- Keep questions concise — diagnostic, not teaching. No hints.

### Scoring Rubric

Map answers to SM-2 quality scores:
- 0-1: Complete confusion or wrong in a way that reveals a fundamental gap
- 2: Partially correct but key concept is missing or confused
- 3: Essentially correct with minor gaps
- 4: Correct, confident, and well-explained
- 5: Demonstrates depth beyond the probe — explains edge cases or caveats unprompted

### Step 3: Seed Mastery (Conservative)

After each probe:
- Call `diagnostic_record_probe(mind_map_id, node_id, quality, inferred_mastery)`
- `inferred_mastery` values: quality 0-1 → 0.1, quality 2 → 0.3, quality 3 → 0.5, quality 4 → 0.6, quality 5 → 0.7
- **Never seed mastery at 1.0 from diagnostic alone.** Teaching and quiz sessions will raise it.

### Step 4: Complete Diagnostic

After the probe sequence:
1. Call `diagnostic_complete(mind_map_id)` — finalizes mastery seeds, transitions to PLANNING.
2. Call `teaching_flow_advance(mind_map_id)` to confirm state transition.
3. Notify the user with a brief summary: "Based on our calibration, you have solid foundations
   in [X] but we'll start with [Y] to fill a key gap."
4. Exit. Do not start teaching — that is the PLANNING and TEACHING phases.

## Exit Criteria

- Flow state is transitioned from `DIAGNOSING` to `PLANNING`
- All probed nodes have `mastery_status = "diagnosed"` and a `mastery_score` between 0.1 and 0.7
- User has been notified of the calibration outcome
