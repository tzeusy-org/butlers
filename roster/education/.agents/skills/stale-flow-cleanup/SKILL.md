---
name: stale-flow-cleanup
description: Abandon inactive teaching flows and clean up stale spaced repetition schedules.
version: 1.1.0
---

# Skill: Stale Flow Cleanup

## Purpose

Weekly maintenance pass to abandon zero-node drafts older than 24 hours and populated unfinished
learning inactive for more than 30 days, then clean up associated pending review schedules. Prevents orphaned flows from cluttering
the active state and schedules from firing for topics the user has effectively stopped studying.

## When to Use

Use this skill when:
- The `weekly-stale-flow-check` scheduled task fires (cron: `0 4 * * 1`, Mondays at 04:00)

## Staleness Criteria

Evaluate elapsed times against one current UTC time using strict boundaries:
- A draft with zero nodes is stale when `created_at` is more than 24 hours old,
  whether or not a flow exists. Exactly 24 hours remains unchanged.
- A populated unfinished map is stale when its flow's `last_session_at`, or
  its newest node activity when no flow exists, is more than 30 days old.
- Preserve recent drafts, completed/abandoned maps and all-mastered graphs.

## Cleanup Protocol

### Step 1: Enumerate Mind Maps

Call `mind_map_list()` and retain maps with `status="draft"` or `status="active"`.
For each map call `teaching_flow_get(mind_map_id=...)`. A missing flow is an
orphan to evaluate, not a reason to skip the map. Fetch the map graph with
`mind_map_get()` to count nodes and inspect their activity timestamps.

The map supplies `id`, `title`, `status` and `created_at`; the flow, when present,
supplies `last_session_at`.

If no draft or active maps are returned, exit silently — no notification needed for a maintenance no-op.

### Step 2: Filter for Stale Flows

Build `stale_maps` records containing the map id/title, optional flow, the applicable
activity timestamp, and the reason (`stalled setup` or `inactive learning`).

Use the criteria above for each enumerated map. Never use only `flow:*` keys:
that would make flow-less drafts unreachable. A draft younger than 24 hours
remains unchanged even when it has no nodes or flow state.

If no stale flows are found, exit without taking further action.

### Step 3: Abandon Each Stale Flow

For each stale map, in sequence:

1. When a flow exists, call `teaching_flow_abandon(mind_map_id=<mind_map_id>)`.
   Otherwise call `mind_map_update_status(mind_map_id=<mind_map_id>, status="abandoned")`.
   The status tool refuses unsafe transitions and never activates an empty map.

2. Call `spaced_repetition_schedule_cleanup(mind_map_id=<mind_map_id>)` to remove all pending
   review schedules associated with this mind map.

3. Call `memory_store_fact()` to record the abandonment:
   ```python
   memory_store_fact(
       subject=<mind_map_title>,
       predicate="study_pattern",
       content=f"Learning map abandoned: {stale_map['reason']}. "
               f"Activity: {stale_map['activity_at'] or 'no recorded activity'}.",
       permanence="volatile",
       importance=4.0,
       tags=[<topic_tag_derived_from_title>, "paused", "stale-flow-cleanup"]
   )
   ```

### Step 4: Notify the User

After processing all stale flows, send a summary notification:

```python
notify(
    channel="telegram",
    intent="send",
    message=f"Weekly cleanup: {len(stale_maps)} stale learning map(s) paused: "
            f"{', '.join(m['title'] for m in stale_maps)}. "
            f"Your progress is preserved. Say 'resume [topic]' anytime to pick up where you left off.",
)
```

If no stale flows were found, skip this notification (no news is good news for a maintenance task).

## Exit Criteria

- `mind_map_list()` enumerated draft/active maps, including orphans
- Zero-node drafts older than 24 hours were evaluated alongside stale populated flows
- Populated unfinished learning inactive for more than 30 days was evaluated
- The appropriate flow or map abandonment tool called for each stale map
- `spaced_repetition_schedule_cleanup()` called for each stale flow to remove pending reviews
- A `memory_store_fact()` with `predicate="study_pattern"` recorded for each abandoned flow
- User notified of cleanup summary (only if at least one flow was abandoned)
- Retry only idempotent schedule cleanup for maps already abandoned; do not repeat state writes or notifications
- Session exits without teaching, reviewing, or modifying non-stale flows
