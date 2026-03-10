# Skill: Progress Digest

## Purpose

Weekly learning progress digest. Read the last 7 days of analytics snapshots across all active
mind maps, compute trends, highlight achievements, flag struggling areas, and deliver a structured
summary to the user via their preferred notification channel (email by default for digests).

## When to Use

Use this skill when:
- The `weekly-progress-digest` scheduled task fires (cron: `0 9 * * 0`, Sundays at 09:00)
- The user requests "show me my learning progress" or similar

## Digest Composition Protocol

### Step 1: Gather Data

Call these tools to collect the analytics data:

1. `analytics_get_cross_topic()` — comparative stats across all active mind maps.
   Returns: `topics` (list with mastery_pct, retention_rate_7d, velocity per map), `portfolio_mastery`.

2. For each `mind_map_id` in the cross-topic result, call:
   `analytics_get_trend(mind_map_id, days=7)` — last 7 daily snapshots, ordered ascending.
   The metrics dict per snapshot includes: `mastered_nodes`, `total_nodes`, `mastery_pct`,
   `retention_rate_7d`, `velocity_nodes_per_week`, `struggling_nodes`, `estimated_completion_days`.

3. `analytics_get_snapshot(mind_map_id)` — latest snapshot for each map (if no 7-day trend).

4. `memory_search(query="learning preferences")` — check if the user prefers verbose vs. concise
   summaries, or has expressed delivery preferences.

### Step 2: Identify Trends

For each topic with at least 2 snapshots in the 7-day window, compute trends:

**Positive signals to highlight:**
- Newly mastered concepts: `mastered_nodes` increased since last week's snapshot
- Retention rate improving: `retention_rate_7d` trended upward over the snapshots
- Velocity accelerating: `velocity_nodes_per_week` increased vs. the prior week

**Concern signals to flag:**
- `retention_rate_7d < 0.60` — retention is low; flag for review focus
- `len(struggling_nodes) >= 3` — multiple concepts struggling; consider curriculum re-planning
- `sessions_this_period == 0` over the 7-day window — topic is idle (no study sessions)
- `estimated_completion_days` is very high or None — progress has stalled

**Portfolio-level insight:**
Compare `portfolio_mastery` to available historical data to show overall momentum.

### Step 3: Structure the Digest

Format the digest as follows (keep it concise — readable on mobile):

```
Weekly Learning Progress — [Date]

Portfolio: [portfolio_mastery]% overall mastery across [N] active topics

[Topic 1 — title]
  Mastered: [mastered_nodes]/[total_nodes] ([mastery_pct]%)  [+N this week if improved]
  Retention: [retention_rate_7d]%  [↑ or ↓ vs last week if trend available]
  Velocity: [velocity] concepts/week
  [Flag if struggling: "⚠ [N] concepts need review attention"]
  [Flag if idle: "💤 No sessions this week"]
  Estimated completion: ~[estimated_completion_days] days

[Topic 2 — title]
  ...

Highlights:
  - [Achievement 1, e.g. "Mastered 5 Python concepts this week"]
  - [Achievement 2, e.g. "Retention rate improved from 68% to 84%"]

Watch areas:
  - [Struggle flag, e.g. "Python closures: low retention (42%) — review sessions recommended"]
  - [Idle flag, e.g. "Calculus: no sessions in 7 days"]

Estimated completions:
  - [Topic]: ~[N] days at current pace
```

**Tone:** Keep it encouraging. Acknowledge effort, not just outcomes. Frame struggle areas as
opportunities, not failures.

### Step 4: Deliver via Email

For the weekly digest, use email as the primary delivery channel:

```python
notify(
    channel="email",
    intent="send",
    subject="Your weekly learning progress — [Date]",
    message=<formatted_digest>,
    request_context=<session_request_context>
)
```

If the user has a preference stored in memory (from `memory_search()`), respect it:
- If preference is Telegram or another channel, use that instead.
- If no preference stored, default to email for digests (it's the appropriate format for
  longer-form weekly summaries).

### Step 5: Trigger Curriculum Re-planning (if needed)

For any topic where `retention_rate_7d < 0.60` or `len(struggling_nodes) >= 3`:

1. Call:
   ```
   curriculum_replan(
       mind_map_id=<map_id>,
       reason="analytics feedback: low retention / multiple struggling concepts"
   )
   ```

2. Note in the digest (before delivery): "I've adjusted the learning path for [topic] to
   prioritize your struggling concepts."

### Step 6: Store Digest Summary in Memory (Optional)

If the digest contains a notable milestone or significant pattern shift, record it:

```python
memory_store_fact(
    subject=<topic_label>,
    predicate="study_pattern",
    content=<brief note about trend, e.g. "mastery accelerating — 5 concepts/week pace">,
    permanence="standard",
    importance=5.0,
    tags=[<topic_tag>, "progress", "weekly-digest"],
    entity_id=<map_entity_id>
)
```

Note: `map_entity_id` is the `entity_id` of the mind map's root concept node. Neither
`analytics_get_snapshot()` nor `mind_map_get()` return a map-level `entity_id` directly —
call `mind_map_node_list(mind_map_id=<id>)` and use the `entity_id` of the root node
(lowest `depth`, or the node with no incoming prerequisite edges). If no clear root can be
determined, omit `entity_id` — it is not required for user/topic-level study pattern facts.

## Exit Criteria

- `analytics_get_cross_topic()` called to get portfolio-level data
- `analytics_get_trend()` called for each active mind map
- Trends computed: velocity, retention, newly mastered concepts, struggling nodes
- Digest formatted and delivered via `notify(channel="email", intent="send", subject=..., ...)`
- Curriculum re-planning triggered (via `curriculum_replan()`) for any topic with low retention
  or 3+ struggling nodes
- Session exits after delivery — no teaching or review in this session
