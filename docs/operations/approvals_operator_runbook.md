# Approvals Operator Runbook

Status: Operational Guide
Last updated: 2026-02-15
Epic: butlers-0p6

## Overview

This runbook provides operational guidance for managing the approvals subsystem in production. It covers daily workflows, troubleshooting, and maintenance procedures.

## Daily Operations

> **Note on API Authentication:** All curl examples shown below assume unauthenticated access for brevity. In production, add authentication headers: `-H "Authorization: Bearer <token>"`. The current implementation lacks consistent auth enforcement - see security audit tracking in butlers-0p6 epic.

### Morning Review


**Goal:** Clear overnight pending actions and check for anomalies

**Steps:**

1. **Check pending action count:**
   ```bash
   # Via API
   curl http://localhost:<port>/api/approvals/metrics | jq '.data.total_pending'  # Note: Add -H "Authorization: Bearer <token>" for authenticated requests
   
   # Or visit /approvals in frontend
   ```

2. **Review pending actions:**
   - Navigate to `/approvals`
   - Set status filter to "Pending"
   - Sort by requested_at (oldest first)
   - Review each action for:
     - Intent clarity (is agent_summary clear?)
     - Recipient safety (are target addresses correct?)
     - Risk level (does risk tier match impact?)

3. **Batch approve safe patterns:**
   - Group similar actions (same tool + recipient pattern)
   - Consider creating standing rule instead of individual approvals
   - Use "Approve and Create Rule" workflow when available

4. **Investigate anomalies:**
   - Actions to unknown recipients
   - High-frequency actions from single session
   - Unusual tool invocations
   - Actions near expiry (< 4 hours remaining)

**Metrics to track:**
- Pending count (target: < 20)
- Approval latency P50/P95 (target: < 2h / < 12h)
- Auto-approval rate (target: > 60%)

### Expiry Management

**Goal:** Prevent action backlog from stale/abandoned actions

**Automation:**

Set up cron job (recommended):
```bash
# Run every 6 hours
0 */6 * * * curl -X POST http://localhost:<port>/api/approvals/actions/expire-stale
```

Or via butler scheduler:
```toml
[[schedules]]
cron = "0 */6 * * *"
prompt = "Expire stale approval actions older than configured TTL"
source = "cron"
enabled = true
skill = "maintenance/expire_approvals"
```

**Manual expiry:**

From frontend:
- Navigate to `/approvals`
- Click "Expire Stale Actions" button
- Confirm expiry operation

From API:
```bash
curl -X POST http://localhost:<port>/api/approvals/actions/expire-stale
```

**Expiry policy:**

Actions expire when:
- `expires_at < now()` (default: requested_at + 48h)
- Status is still `pending`

Expired actions:
- Cannot be approved/rejected
- Remain in audit log
- Visible in executed actions view (filtered by status=expired)

**When to expire manually:**
- After resolving incident that caused action backlog
- Before maintenance window (clear queue)
- When operator unavailable for extended period

### Rule Hygiene

**Goal:** Keep standing rules relevant and safe

**Weekly review:**

1. **Check rule usage:**
   ```bash
   # Via API
   curl http://localhost:<port>/api/approvals/rules | jq '.data[] | {id, description, use_count, max_uses}'
   ```

2. **Identify underused rules:**
   - use_count = 0 after 7 days → consider revoking
   - use_count approaching max_uses → extend or recreate

3. **Review broad rules:**
   - Rules with empty constraints `{}`
   - Rules with only `any` constraints
   - Consider narrowing if misuse detected

4. **Check expired rules:**
   - Navigate to `/approvals/rules`
   - Toggle "Show inactive" to see expired/revoked
   - Recreate if still needed

**Monthly audit:**

1. **Export rule inventory:**
   ```sql
-- Note: risk_tier is not stored in pending_actions; it's determined by application logic.
-- To triage by risk, filter by tool_name patterns or use the API/MCP tools.
SELECT id, tool_name, requested_at, expires_at
FROM pending_actions
WHERE status = 'pending'
ORDER BY requested_at ASC;
```

3. **Prioritize critical/high risk:**
   - Review high-risk actions first
   - Approve safe patterns, reject suspicious ones

4. **Batch approve low-risk:**
   - For homogeneous low-risk items (e.g., internal notifications)
   - Consider bulk approval after verification

5. **Create emergency rules:**
   - For safe patterns in backlog
   - Use bounded scope (max_uses=100)
   - Review after incident resolved

6. **Extend expiry if needed:**
   - Prevent mass expiry during incident
   - Update default_expiry_hours temporarily

### High: Auto-approval rate drop (< 30%)

**Impact:** Increased manual approval burden, slower operations

**Response:**

1. **Identify gap:**
   ```sql
   SELECT 
     tool_name,
     COUNT(*) as total_pending,
     (SELECT COUNT(*) FROM approval_rules WHERE tool_name = pa.tool_name AND active = true) as rule_count
   FROM pending_actions pa
   WHERE status = 'pending'
     AND requested_at > NOW() - INTERVAL '24 hours'
   GROUP BY tool_name
   ORDER BY total_pending DESC;
   ```

2. **Check rule status:**
   - Have rules expired?
   - Have rules hit max_uses?
   - Are constraints too narrow?

3. **Recreate missing rules:**
   - Use "Create Rule from Action" workflow
   - Review constraint suggestions
   - Set appropriate max_uses

4. **Broaden constraints if safe:**
   - Change exact → pattern for variable fields
   - Add `any` constraints for non-sensitive args
   - Maintain bounded scope for safety

### Medium: Execution failure spike (> 10% failure rate)

**Impact:** Approved actions not completing, user impact

**Response:**

1. **Identify affected tool:**
   ```sql
   SELECT tool_name, COUNT(*) as failures
   FROM pending_actions
   WHERE status = 'executed'
     AND execution_result->>'success' = 'false'
     AND executed_at > NOW() - INTERVAL '1 hour'
   GROUP BY tool_name;
   ```

2. **Check tool health:**
   - Module status in butler detail
   - External service dependencies
   - Recent config changes

3. **Temporary mitigation:**
   - Disable gating for affected tool
   - Queue new invocations for manual retry
   - Alert users of delays

4. **Root cause fix:**
   - Review tool implementation
   - Fix configuration issues
   - Update tool error handling

5. **Re-enable after verification:**
   - Test tool execution manually
   - Re-enable gating
   - Monitor for recurrence

## Maintenance Procedures

### Audit Log Retention

**Goal:** Manage audit event table growth

**Policy:**

Events are immutable but can be archived/purged per retention policy:
- **Pending actions:** Retain 90 days after decision
- **Executed actions:** Retain 180 days
- **Approval events:** Retain 365 days (compliance requirement)
- **Standing rules:** Retain indefinitely (revoked rules remain queryable)

**Archive procedure:**

```sql
-- Archive old events to cold storage (example)
COPY (
  SELECT * FROM approval_events
  WHERE occurred_at < NOW() - INTERVAL '365 days'
) TO '/backup/approval_events_archive_$(date +%Y).csv' WITH CSV HEADER;

-- Purge after archive confirmed
DELETE FROM approval_events
WHERE occurred_at < NOW() - INTERVAL '365 days';
```

**Automated retention:**

Add to butler scheduler:
```toml
[[schedules]]
cron = "0 2 * * 0"  # Weekly Sunday 2am
prompt = "Archive and purge old approval audit events per retention policy"
source = "cron"
enabled = true
skill = "maintenance/approval_retention"
```

### Rule Revocation

**When to revoke:**

- Rule no longer needed (pattern obsolete)
- Rule too broad (security concern)
- Rule causing unwanted auto-approvals
- Replacing with narrower rule

**Revocation procedure:**

From frontend:
- Navigate to `/approvals/rules`
- Find rule to revoke
- Click "Revoke" button
- Confirm revocation

From API:
```bash
# Note: This endpoint returns 501 (not implemented). Use MCP tool revoke_approval_rule instead.
curl -X POST http://localhost:<port>/api/approvals/rules/{rule_id}/revoke \
  -H "Content-Type: application/json" \
  -d '{"reason": "No longer needed"}'
```

**Effects:**

- Rule becomes inactive (no longer matches new actions)
- Existing auto-approvals remain in history
- Rule remains queryable for audit
- use_count preserved

**Recreate if needed:**

If revoking to update constraints:
1. Revoke old rule
2. Create new rule with updated constraints
3. Note both rules in audit trail

### Database Maintenance

**Index health:**

Monitor query performance:
```sql
-- Check index usage
SELECT schemaname, tablename, indexname, idx_scan
FROM pg_stat_user_indexes
WHERE schemaname = 'public'
  AND tablename IN ('pending_actions', 'approval_rules', 'approval_events')
ORDER BY idx_scan;
```

**Vacuum and analyze:**

```sql
-- Regular maintenance
VACUUM ANALYZE pending_actions;
VACUUM ANALYZE approval_rules;
VACUUM ANALYZE approval_events;
```

**Table size monitoring:**

```sql
SELECT 
  tablename,
  pg_size_pretty(pg_total_relation_size(tablename::regclass)) as total_size,
  pg_size_pretty(pg_relation_size(tablename::regclass)) as table_size,
  pg_size_pretty(pg_total_relation_size(tablename::regclass) - pg_relation_size(tablename::regclass)) as index_size
FROM pg_tables
WHERE schemaname = 'public'
  AND tablename IN ('pending_actions', 'approval_rules', 'approval_events');
```

## Troubleshooting Guide

### Problem: Pending actions not appearing in frontend

**Symptoms:**
- API returns actions but frontend shows empty state
- Count mismatch between API and UI

**Diagnosis:**
1. Check API endpoint directly:
   ```bash
   curl http://localhost:<port>/api/approvals/actions?limit=10
   ```
2. Check browser console for errors
3. Verify frontend API client configuration
4. Check CORS settings

**Resolution:**
- If API works: Clear browser cache, reload
- If API fails: Check approvals module enabled
- If CORS error: Update API CORS configuration

### Problem: Rule not matching expected actions

**Symptoms:**
- Action remains pending despite matching rule
- Auto-approval expected but not happening

**Diagnosis:**
1. Check rule is active:
   ```sql
   SELECT * FROM approval_rules WHERE id = '<rule_id>';
   ```
2. Verify rule not expired or exhausted:
   - `expires_at IS NULL OR expires_at > NOW()`
   - `max_uses IS NULL OR use_count < max_uses`
3. Test constraint matching:
   ```python
   # In butler Python environment
   from butlers.modules.approvals.rules import _args_match_constraints  # Note: This is a private function
   
   constraints = {...}  # From rule
   args = {...}  # From action
   
   result = _args_match_constraints(args, constraints)
   print(result)  # Should be True
   ```
4. Check precedence (higher-specificity rule may match first)

**Resolution:**
- If expired: Recreate rule with new expiry
- If exhausted: Increase max_uses or create new rule
- If constraints mismatch: Update constraints or action args
- If precedence conflict: Revoke conflicting rule or adjust specificity

### Problem: High approval latency

**Symptoms:**
- Actions pending for > 12 hours
- User complaints about delays

**Diagnosis:**
1. Check pending action count:
   ```sql
   SELECT COUNT(*) FROM pending_actions WHERE status = 'pending';
   ```
2. Review approval metrics:
   ```bash
   curl http://localhost:<port>/api/approvals/metrics
   ```
3. Check operator activity:
   ```sql
   SELECT 
     DATE(decided_at) as day,
     COUNT(*) as decisions
   FROM pending_actions
   WHERE status IN ('approved', 'rejected')
     AND decided_at > NOW() - INTERVAL '7 days'
   GROUP BY day;
   ```

**Resolution:**
- If backlog: Prioritize critical actions, batch approve safe ones
- If low activity: Increase operator coverage or expand auto-approval
- If rule gaps: Create standing rules for common patterns
- Consider extending expiry to reduce pressure

### Problem: Execution errors after approval

**Symptoms:**
- Actions approved but execution_result shows failure
- Tools work outside approval flow but fail when executed

**Diagnosis:**
1. Check execution result:
   ```sql
   SELECT id, tool_name, execution_result
   FROM pending_actions
   WHERE status = 'executed'
     AND execution_result->>'success' = 'false'
   ORDER BY executed_at DESC
   LIMIT 10;
   ```
2. Verify tool executor wired correctly in module
3. Test tool directly with same arguments
4. Check for redaction issues (sensitive fields removed?)

**Resolution:**
- If tool executor missing: Wire executor in module startup
- If argument issues: Fix constraint suggestions or validation
- If tool bug: File bug, disable gating temporarily
- If redaction too aggressive: Adjust redaction rules

## Metrics and KPIs

### Operational Metrics

**Daily:**
- Pending action count (current)
- Actions approved today
- Actions rejected today
- Actions expired today
- Auto-approval rate today

**Weekly:**
- Approval latency P50/P95/P99
- Execution failure rate
- Rule creation rate
- Rule revocation rate
- Manual approval burden (actions/operator/day)

**Monthly:**
- Auto-approval trend
- Most-used rules
- Most-gated tools
- Operator productivity (approvals/hour)

### Health Indicators

**Green:**
- Pending count < 20
- Auto-approval rate > 60%
- Approval latency P95 < 12h
- Execution failure rate < 2%

**Yellow:**
- Pending count 20-50
- Auto-approval rate 40-60%
- Approval latency P95 12-24h
- Execution failure rate 2-5%

**Red:**
- Pending count > 50
- Auto-approval rate < 40%
- Approval latency P95 > 24h
- Execution failure rate > 5%

## Best Practices

### Rule Creation

1. **Start narrow, broaden cautiously:**
   - Begin with exact constraints
   - Add pattern/any constraints only after verification
   - Use bounded scope (expiry or max_uses) initially

2. **Test before deploying:**
   - Create rule from specific action
   - Verify it matches expected future invocations
   - Monitor first few auto-approvals

3. **Document rule intent:**
   - Use clear descriptions
   - Include context in created_from field
   - Add notes about expected use cases

4. **Set appropriate risk tiers:**
   - Low: Internal notifications, status updates
   - Medium: User messages, non-critical outputs
   - High: External communications, data exports
   - Critical: Financial transactions, irreversible actions

### Approval Decisions

1. **Review context thoroughly:**
   - Read agent_summary for intent
   - Check tool_args for recipient/content
   - Verify risk tier appropriate
   - Check for suspicious patterns

2. **Create rules from patterns:**
   - If approving multiple similar actions, create rule
   - Use "Approve and Create Rule" workflow
   - Set max_uses for new rules (e.g., 50)

3. **Reject with clear reasons:**
   - Document why action rejected
   - Help improve future agent prompts
   - Consider filing bug if rejection due to agent error

4. **Don't approve if uncertain:**
   - Better to reject and investigate
   - Execution can't be undone
   - Ask for clarification if intent unclear

### Operational Hygiene

1. **Review approvals daily:**
   - Don't let queue grow beyond 20
   - Address anomalies immediately
   - Keep approval latency low

2. **Audit rules monthly:**
   - Revoke unused rules
   - Update outdated constraints
   - Verify high-risk rules still appropriate

3. **Monitor trends:**
   - Track auto-approval rate over time
   - Investigate sudden changes
   - Optimize for operator efficiency

4. **Document incidents:**
   - Record what went wrong
   - Document mitigation steps
   - Update runbook with learnings

## Escalation Contacts

- **Approval queue backlog:** On-call operator
- **Execution failures:** Module owner
- **Database issues:** Database team
- **Security concerns:** Security team
- **Frontend errors:** Frontend team

## Quick Reference

### Common Commands

```bash
# Check pending count
curl localhost:<port>/api/approvals/metrics | jq '.data.total_pending'

# List pending actions
curl localhost:<port>/api/approvals/actions?status=pending

# Expire stale actions
curl -X POST localhost:<port>/api/approvals/actions/expire-stale

# List active rules
curl localhost:<port>/api/approvals/rules?active_only=true

# Get approval metrics
curl localhost:<port>/api/approvals/metrics | jq
```

### Common SQL Queries

```sql
-- Pending actions by tool
SELECT tool_name, COUNT(*) 
FROM pending_actions 
WHERE status = 'pending' 
GROUP BY tool_name;

-- Actions near expiry (< 2 hours)
SELECT id, tool_name, agent_summary, expires_at
FROM pending_actions
WHERE status = 'pending'
  AND expires_at < NOW() + INTERVAL '2 hours'
ORDER BY expires_at;

-- Most-used rules
SELECT id, description, use_count, max_uses
FROM approval_rules
WHERE active = true
ORDER BY use_count DESC
LIMIT 10;

-- Recent execution failures
SELECT tool_name, execution_result->>'error', COUNT(*)
FROM pending_actions
WHERE status = 'executed'
  AND execution_result->>'success' = 'false'
  AND executed_at > NOW() - INTERVAL '24 hours'
GROUP BY tool_name, execution_result->>'error';
```

## Version History

- 2026-02-15: Initial operator runbook (butlers-0p6.8)
