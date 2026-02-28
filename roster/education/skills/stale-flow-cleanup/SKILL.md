# Skill: Stale Flow Cleanup

## Purpose

Weekly maintenance pass to abandon teaching flows that have been inactive for 30+ days and clean
up their associated pending spaced repetition schedules. Prevents orphaned flows from cluttering
the active state and schedules from firing for topics the user has effectively stopped studying.

## When to Use

Use this skill when:
- The `weekly-stale-flow-check` scheduled task fires (cron: `0 4 * * 1`, Mondays at 04:00)

## Staleness Criteria

A teaching flow is stale when **all** of the following are true:
- `status` is `active` (i.e., not `completed` or `abandoned`)
- `last_session_at` is more than 30 days ago (or `null` and `created_at` is more than 30 days ago)

## Cleanup Protocol

### Step 1: List Active Flows

Call `teaching_flow_list(status="active")` to retrieve all active flows.

The response includes, per flow: `mind_map_id`, `mind_map_title`, `status`, `created_at`,
`last_session_at`.

If no active flows are returned, notify the user that there is nothing to clean up and exit.

### Step 2: Filter for Stale Flows

From the active flows, identify stale flows:

```python
from datetime import datetime, timezone, timedelta

STALE_THRESHOLD_DAYS = 30
now = datetime.now(timezone.utc)

stale_flows = [
    flow for flow in active_flows
    if (flow["last_session_at"] is not None
        and (now - datetime.fromisoformat(flow["last_session_at"])).days > STALE_THRESHOLD_DAYS)
    or (flow["last_session_at"] is None
        and (now - datetime.fromisoformat(flow["created_at"])).days > STALE_THRESHOLD_DAYS)
]
```

If no stale flows are found, exit without taking further action.

### Step 3: Abandon Each Stale Flow

For each stale flow, in sequence:

1. Call `teaching_flow_abandon(mind_map_id=<mind_map_id>)` to transition the flow status from
   `active` to `abandoned`.

2. Call `spaced_repetition_schedule_cleanup(mind_map_id=<mind_map_id>)` to remove all pending
   review schedules associated with this mind map.

3. Call `memory_store_fact()` to record the abandonment:
   ```python
   memory_store_fact(
       subject=<mind_map_title>,
       predicate="study_pattern",
       content=f"Teaching flow abandoned after 30+ days of inactivity. "
               f"Last active: {flow['last_session_at'] or 'never'}.",
       permanence="volatile",
       importance=4.0,
       tags=[<topic_tag_derived_from_title>, "paused", "stale-flow-cleanup"]
   )
   ```

### Step 4: Notify the User

After processing all stale flows, send a summary notification:

```python
notify(
    intent="proactive",
    message=f"Weekly cleanup: {len(stale_flows)} stale learning flow(s) archived after 30+ days "
            f"of inactivity — {', '.join(f['mind_map_title'] for f in stale_flows)}. "
            f"Your progress is preserved. Say 'resume [topic]' anytime to pick up where you left off.",
    request_context=<session_request_context>
)
```

If no stale flows were found, skip this notification (no news is good news for a maintenance task).

## Exit Criteria

- `teaching_flow_list(status="active")` was called to retrieve all active flows
- All flows inactive for 30+ days have been identified
- `teaching_flow_abandon()` called for each stale flow
- `spaced_repetition_schedule_cleanup()` called for each stale flow to remove pending reviews
- A `memory_store_fact()` with `predicate="study_pattern"` recorded for each abandoned flow
- User notified of cleanup summary (only if at least one flow was abandoned)
- Session exits without teaching, reviewing, or modifying non-stale flows
