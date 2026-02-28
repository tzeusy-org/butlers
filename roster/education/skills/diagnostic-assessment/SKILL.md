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

### Step 0: Initialize the Diagnostic Session

Call `diagnostic_start(mind_map_id)` to initialize the DIAGNOSING state and retrieve the
concept inventory ranked by `difficulty_rank`. This returns a list of:
- `node_id`: UUID to reference when recording probes
- `label`: Concept name
- `description`: Brief description
- `difficulty_rank`: Depth-based difficulty (0 = easiest)

If the mind map has no nodes yet (freshly created flow with no prior planning), generate an
internal inventory of 10–15 concepts spanning beginner to expert:
- 3–4 foundational concepts (depth 0–1, beginner)
- 4–6 intermediate concepts (depth 2–3)
- 3–5 advanced concepts (depth 4–5)

Do not show this inventory to the user. It is internal scaffolding for the probe sequence.

### Step 1: Adaptive Probe Sequence (Binary Search on Difficulty)

1. Sort the concept inventory by `difficulty_rank`. Identify the median index as the starting
   probe target (e.g., index `len(inventory) // 2`).
2. Set `lo = 0`, `hi = len(inventory) - 1`, `probe_count = 0`.
3. For each probe (repeat until `probe_count >= 7` or difficulty range converges):
   a. Select the concept at the midpoint between `lo` and `hi`.
   b. Generate a focused question for that concept.
   c. Deliver via `notify(channel="telegram", intent="send", message=<question>, request_context=...)`.
   d. Wait for the user's answer.
   e. Score the answer 0–5 (see rubric below).
   f. Call `diagnostic_record_probe(mind_map_id, node_id, quality, inferred_mastery)`.
   g. If `quality >= 3`: set `lo = midpoint + 1` (probe harder concepts next).
      If `quality < 3`: set `hi = midpoint - 1` (probe easier concepts next).
   h. Increment `probe_count`.
4. Stop when:
   - `probe_count >= 7` (hard cap), OR
   - `lo > hi` (range converged — knowledge boundary found), OR
   - After 3 probes, if you have high confidence in the boundary from the pattern.

### Question Format Constraints

- **One question per message. Never bundle multiple questions.** Wait for the user's response
  before delivering the next probe. The probe sequence only works if you observe each answer
  before picking the next concept.
- Use multiple choice (A/B/C/D) for factual recall — definitions, syntax identification,
  discrimination between similar but distinct concepts.
- Use short-answer for conceptual understanding — "Explain...", "What happens when...",
  "Why does...".
- Keep questions concise and purely diagnostic — no teaching, no hints embedded in the question.
- Adapt phrasing to the topic domain (code snippet for programming, formula for math, etc.).

### Scoring Rubric

Map answers to SM-2 quality scores:

| Score | Meaning |
|-------|---------|
| 0 | Complete blackout — no recall or reveals fundamental misconception |
| 1 | Wrong but attempted — effort shown, key concept missed |
| 2 | Partially correct — missing a crucial element or confused about mechanism |
| 3 | Essentially correct — core right, minor gaps acceptable |
| 4 | Correct, confident, and clearly explained |
| 5 | Demonstrates depth beyond the probe — mentions edge cases or caveats unprompted |

### Step 2: Conservative Mastery Seeding

For each probe call `diagnostic_record_probe(mind_map_id, node_id, quality, inferred_mastery)`
using the following `inferred_mastery` mapping:

| Quality | inferred_mastery |
|---------|-----------------|
| 0 | 0.1 |
| 1 | 0.1 |
| 2 | 0.3 |
| 3 | 0.5 |
| 4 | 0.6 |
| 5 | 0.7 |

**Never seed mastery at 1.0 from diagnostic alone.** The tool enforces a hard cap at 0.7, but
the LLM must not attempt values above 0.7. Full mastery is earned through teaching and quiz
sessions, not diagnostic probes.

The tool only seeds mastery for quality >= 3. For quality < 3, the node remains `unseen` — do
not manually set lower mastery scores.

### Step 3: Complete the Diagnostic

After the probe sequence (3–7 questions):

1. Call `diagnostic_complete(mind_map_id)` — finalizes mastery seeds and transitions the flow
   state from `DIAGNOSING` to `PLANNING`. Returns a summary with `inferred_frontier_rank`.
2. Notify the user with a brief, encouraging calibration summary:

```python
notify(
    channel="telegram",
    message="Based on our calibration, you have solid foundations in [X, Y]. "
            "We'll start with [first frontier concept] and build from there.",
    intent="reply",
    request_context=<session_request_context>
)
```

Adapt the message tone:
- User knew a lot (high `inferred_frontier_rank`): emphasize building on strong foundations
- Moderate knowledge: acknowledge what they know, frame gaps as natural next steps
- Knew little: start from first principles, frame as an exciting journey, not a deficit

3. Exit. Do not start teaching. The PLANNING phase runs in the next triggered session.

## Exit Criteria

- `diagnostic_start()` was called to initialize the session
- `diagnostic_record_probe()` was called for each probe question (3–7 total)
- `diagnostic_complete()` was called and succeeded
- Flow state has transitioned from `DIAGNOSING` to `PLANNING`
- All probed concepts with quality >= 3 have `mastery_status = "diagnosed"` and
  `mastery_score` in [0.3, 0.7]
- User has been notified of the calibration outcome via `notify()`
- Session exits without entering the PLANNING or TEACHING phases
