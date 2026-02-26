# Skill: Progress Digest

## Purpose

Weekly learning progress digest. Read the last 7 days of analytics snapshots across all active
mind maps, compute trends, highlight achievements, flag struggling areas, and deliver a structured
summary to the user via their preferred notification channel.

## When to Use

Use this skill when:
- The `weekly-progress-digest` scheduled task fires (cron: `0 9 * * 0`, Sundays at 09:00)
- The user requests "show me my learning progress" or similar

## Digest Composition Protocol

### Step 1: Gather Data

1. Call `analytics_get_cross_topic()` — comparative stats across all active mind maps.
2. For each active mind map, call `analytics_get_trend(mind_map_id, days=7)` — last 7 snapshots.
3. Call `memory_search(query="learning preferences")` to check the user's preferred delivery style
   (verbose vs. concise).

### Step 2: Identify Trends

For each topic with at least 2 snapshots in the 7-day window, compute:

**Positive signals:**
- Nodes newly mastered this week (`mastered_nodes` increase)
- Retention rate improving (`retention_rate_7d` trend is positive)
- Velocity accelerating (`velocity_nodes_per_week` increase)

**Concern signals:**
- `retention_rate_7d < 0.60` — retention is low; flag for review focus
- `struggling_nodes >= 3` — multiple concepts struggling; consider curriculum re-planning
- No sessions in 7 days — topic is idle

### Step 3: Structure the Digest

Format:

```
Weekly Learning Progress — [Date]

[Topic 1]
  Mastered this week: [N] concepts ([list])
  Total mastery: [N]/[total] ([pct]%)
  Retention: [rate]%
  [Optional: flag if struggling or idle]

[Topic 2]
  ...

Highlights:
  - [Achievement 1]
  - [Achievement 2]

Watch areas:
  - [Struggle flag 1]
  - [Struggle flag 2]

Estimated completions:
  - [Topic]: ~[N] days at current pace
```

Keep it concise. The digest is read on mobile. Avoid walls of text.

### Step 4: Deliver via Owner's Preferred Channel

Do NOT hardcode Telegram. Resolve the owner contact and use their preferred channel:

```python
# Resolve owner
contact = contact_lookup(role="owner")

# Deliver digest
notify(
    channel=contact.preferred_channel,  # or fall back to "telegram"
    message=<formatted_digest>,
    intent="proactive",
    request_context=...
)
```

### Step 5: Trigger Curriculum Re-planning (if needed)

If any topic has `retention_rate_7d < 0.60` or `struggling_nodes >= 3`:
- Call `curriculum_replan(mind_map_id, reason="analytics feedback: low retention / multiple struggle nodes")`
- Note this in the digest: "I've adjusted the learning path for [topic] to focus on your struggling areas."

## Exit Criteria

- Analytics snapshots read for all active mind maps
- Trends computed (velocity, retention, achievements, struggles)
- Digest delivered via owner's preferred channel
- Curriculum re-planning triggered for any topics below retention/struggle thresholds
