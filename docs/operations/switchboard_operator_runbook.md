# Switchboard Operator Runbook

This runbook covers manual intervention procedures for Switchboard operators.

## Table of Contents

1. [Dead-Letter Queue Management](#dead-letter-queue-management)
2. [Operator Controls](#operator-controls)
3. [Common Scenarios](#common-scenarios)
4. [Safety Guidelines](#safety-guidelines)

---

## Dead-Letter Queue Management

### Viewing Dead-Letter Queue

```python
from roster.switchboard.tools.dead_letter.capture import get_dead_letter_stats
from roster.switchboard.tools.dead_letter.replay import list_replay_eligible_requests

# Get overall statistics
stats = await get_dead_letter_stats(conn)
print(f"Total dead-lettered: {stats['total']}")

# List replay-eligible requests
eligible = await list_replay_eligible_requests(conn, limit=50)
for entry in eligible:
    print(f"ID: {entry['id']}, Reason: {entry['failure_reason']}")
```

### Replaying Dead-Lettered Requests

**Use case:** Infrastructure recovered, retry exhausted requests.

```python
from roster.switchboard.tools.dead_letter.replay import replay_dead_letter_request

result = await replay_dead_letter_request(
    conn,
    dead_letter_id=uuid.UUID("..."),
    operator_identity="ops@example.com",
    reason="Infrastructure recovered, safe to retry"
)

if result["success"]:
    print(f"Replayed as request: {result['replayed_request_id']}")
else:
    print(f"Replay failed: {result['error']}")
```

**Safety notes:**
- Replay preserves original `request_id` lineage
- Dedupe logic will prevent duplicate processing
- Replay is idempotent (replaying twice will fail on second attempt)
- All replays are audited in `operator_audit_log`

---

## Operator Controls

### Manual Reroute

**Use case:** Message was misclassified, needs different butler.

```python
from roster.switchboard.tools.operator.controls import manual_reroute_request

result = await manual_reroute_request(
    conn,
    request_id=uuid.UUID("..."),
    new_target_butler="health",
    operator_identity="ops@example.com",
    reason="Decomposer misclassified medication reminder as general query"
)
```

**Constraints:**
- Cannot reroute terminal requests (completed, failed, cancelled)
- Rerouted requests enter `rerouted` lifecycle state
- Original target is preserved in audit trail

### Cancel Request

**Use case:** User requested cancellation, or request is no longer valid.

```python
from roster.switchboard.tools.operator.controls import cancel_request

result = await cancel_request(
    conn,
    request_id=uuid.UUID("..."),
    operator_identity="ops@example.com",
    reason="User explicitly requested cancellation via support ticket #12345"
)
```

**Effects:**
- Request enters `cancelled` terminal state
- `final_state_at` timestamp is set
- Response summary includes cancellation reason

### Abort Request

**Use case:** Forceful termination (e.g., runaway request consuming resources).

```python
from roster.switchboard.tools.operator.controls import abort_request

result = await abort_request(
    conn,
    request_id=uuid.UUID("..."),
    operator_identity="ops@example.com",
    reason="Runaway request causing downstream circuit breakers to trip"
)
```

**Differences from cancel:**
- Abort is forceful, cancel is graceful
- Abort marks request as `aborted` instead of `cancelled`
- Use abort for operational emergencies

### Force-Complete Request

**Use case:** Manual resolution required, bypass normal flow.

```python
from roster.switchboard.tools.operator.controls import force_complete_request

result = await force_complete_request(
    conn,
    request_id=uuid.UUID("..."),
    operator_identity="ops@example.com",
    reason="Resolved via external system (Jira ticket #5678)",
    completion_summary="User query answered via direct email"
)
```

**Use sparingly:**
- Only for cases where normal flow cannot complete
- Completion summary must explain resolution path
- Audited with full attribution

---

## Common Scenarios

### Scenario 1: Circuit Breaker Open, Requests Piling Up

**Problem:** Downstream butler is down, requests timing out.

**Solution:**

1. Check dead-letter queue for `circuit_open` failures:
   ```python
   eligible = await list_replay_eligible_requests(
       conn, 
       failure_category="circuit_open"
   )
   ```

2. Wait for downstream recovery (check butler health)

3. Batch replay after confirmation:
   ```python
   for entry in eligible:
       await replay_dead_letter_request(
           conn,
           dead_letter_id=entry["id"],
           operator_identity="ops@example.com",
           reason="Health butler recovered, replaying backlog"
       )
   ```

### Scenario 2: Misclassification Spike

**Problem:** Decomposer is routing health queries to relationship butler.

**Solution:**

1. Identify affected requests:
   ```sql
   SELECT id, normalized_text, dispatch_outcomes
   FROM message_inbox
   WHERE lifecycle_state = 'dispatched'
   AND dispatch_outcomes->>'target' = 'relationship'
   AND normalized_text ILIKE '%medication%'
   LIMIT 50;
   ```

2. Manually reroute:
   ```python
   for request_id in affected_ids:
       await manual_reroute_request(
           conn,
           request_id=request_id,
           new_target_butler="health",
           operator_identity="ops@example.com",
           reason="Decomposer bug: medication queries misrouted"
       )
   ```

3. File bug report for decomposer model

### Scenario 3: User Requests Deletion of In-Flight Request

**Problem:** User wants to cancel a request that's already dispatched.

**Solution:**

1. Cancel the request:
   ```python
   await cancel_request(
       conn,
       request_id=user_request_id,
       operator_identity="support@example.com",
       reason=f"User cancellation via support ticket #{ticket_id}"
   )
   ```

2. Verify cancellation:
   ```sql
   SELECT lifecycle_state, response_summary
   FROM message_inbox
   WHERE id = $1;
   ```

---

## Safety Guidelines

### Attribution

All operator actions MUST include:
- `operator_identity`: Email or unique identifier
- `reason`: Clear, audit-friendly explanation

**Good reasons:**
- "Infrastructure recovered after 2-hour outage"
- "User cancellation via support ticket #12345"
- "Decomposer bug causing misrouting (JIRA-4567)"

**Bad reasons:**
- "Testing"
- "Fix it"
- "" (empty string)

### Idempotency

Most operations are idempotent:
- Replay: Only works once per dead-letter entry
- Cancel: Idempotent if already cancelled
- Reroute: Can be performed multiple times (creates audit trail)

### Audit Trail

All operator actions are logged in `operator_audit_log`:

```sql
SELECT
    action_type,
    operator_identity,
    reason,
    outcome,
    performed_at
FROM operator_audit_log
WHERE target_request_id = $1
ORDER BY performed_at DESC;
```

### Terminal State Protection

Requests in terminal states (`completed`, `failed`, `cancelled`, `aborted`) cannot be:
- Rerouted
- Cancelled again
- Force-completed

Only replay (via dead-letter) can resurrect a failed request.

---

## Schema Versioning and Migration

### Dead-Letter Queue Schema

**Current version:** `v1` (migration `sw_011`)

**Fields:**
- `original_request_id`: Links to source request
- `failure_category`: Enum constraint (see migration)
- `replay_eligible`: Boolean flag for replay safety
- `replayed_at`: Timestamp (null until replayed)

**Future migrations:**

If adding fields:
1. Use `ALTER TABLE` with default values
2. Update `capture_to_dead_letter` function signature
3. Maintain backwards compatibility for existing entries

If changing failure categories:
1. Update `CHECK` constraint via migration
2. Migrate existing entries if needed
3. Document category evolution

### Operator Audit Log Schema

**Current version:** `v1` (migration `sw_012`)

**Action types:**
- `manual_reroute`
- `cancel_request`
- `abort_request`
- `controlled_replay`
- `controlled_retry`
- `force_complete`

**Adding new action types:**

1. Extend `CHECK` constraint in new migration
2. Implement corresponding control function
3. Update this runbook with examples

---

## Monitoring and Alerts

### Recommended Metrics

1. **Dead-letter queue depth** (by category):
   ```sql
   SELECT failure_category, COUNT(*)
   FROM dead_letter_queue
   WHERE replayed_at IS NULL
   GROUP BY failure_category;
   ```

2. **Operator action frequency**:
   ```sql
   SELECT action_type, COUNT(*)
   FROM operator_audit_log
   WHERE performed_at > now() - INTERVAL '24 hours'
   GROUP BY action_type;
   ```

3. **Replay success rate**:
   ```sql
   SELECT
       COUNT(*) FILTER (WHERE replay_outcome = 'success') AS success,
       COUNT(*) FILTER (WHERE replay_outcome = 'failed') AS failed,
       COUNT(*) AS total
   FROM dead_letter_queue
   WHERE replayed_at IS NOT NULL;
   ```

### Alert Thresholds

- Dead-letter queue depth > 100: Investigate failure spike
- Operator action rate > 10/hour: Possible automation needed
- Replay failure rate > 20%: Check infrastructure health

---

## Support Contacts

- **Switchboard on-call:** `switchboard-oncall@example.com`
- **Butler platform team:** `#butler-platform` (Slack)
- **Escalation:** Page `butler-sre` via PagerDuty
