# Approvals Subsystem Rollout Guide

Status: Final
Last updated: 2026-02-15
Epic: butlers-0p6

## Overview

This guide provides step-by-step instructions for rolling out the approvals subsystem to butler instances, with fallback guidance for each step.

## Prerequisites

- Butler instance running with modules support
- PostgreSQL database access
- Access to butler configuration files
- Admin access to butler API/MCP tools

## Rollout Phases

### Phase 1: Database Migration

**Objective:** Create approval tables in butler database

**Steps:**

1. **Verify current migration state:**
   ```bash
   # In butler environment
   alembic current
   ```

2. **Run approval migrations:**
   ```bash
   # Apply approvals table migrations
   alembic upgrade head
   ```
   
   This creates:
   - `pending_actions` table
   - `approval_rules` table
   - `approval_events` table (immutable audit log)

3. **Verify migration success:**
   ```bash
   # Check tables exist
   psql -d butler_<name> -c "\dt" | grep -E "pending_actions|approval_rules|approval_events"
   ```

**Fallback:**

If migration fails:
```bash
# Rollback to previous state
alembic downgrade -1

# Check error logs
tail -n 100 logs/butler_<name>.log

# Common issues:
# - Insufficient permissions: Grant schema permissions
# - Existing tables: Drop manually if from abandoned migration
# - Connection timeout: Check DB connectivity
```

**Validation:**
- [ ] All three tables exist
- [ ] Indexes created (check with `\d pending_actions` in psql)
- [ ] No errors in migration log

### Phase 2: Module Configuration

**Objective:** Enable approvals module with conservative defaults

**Steps:**

1. **Add approvals config to `butler.toml`:**
   ```toml
   [modules.approvals]
   enabled = true
   default_expiry_hours = 48
   default_risk_tier = "medium"
   
   [modules.approvals.gated_tools]
   # Start with high-impact tools only
   # bot_email_send = { risk_tier = "high", expiry_hours = 24 }
   # send_telegram_message = { risk_tier = "medium" }
   ```

2. **Start with identity-aware defaults:**
   
   By default, the module gates user-facing outputs with `approval_default="always"` metadata. No explicit config needed for:
   - Email sends to external recipients
   - Telegram messages to users
   - SMS sends
   
   Bot outputs are NOT gated by default.

3. **Restart butler to load module:**
   ```bash
   # Graceful restart
   systemctl restart butler_<name>
   
   # Or via API
   curl -X POST http://localhost:<port>/api/admin/reload
   ```

4. **Verify module loaded:**
   ```bash
   # Check logs for module registration
   tail -f logs/butler_<name>.log | grep -i "approval"
   
   # Expected: "Approvals module registered 13 tools"
   ```

**Fallback:**

If butler fails to start:
```toml
[modules.approvals]
enabled = false  # Disable module
```

Restart butler and check logs for config errors:
```bash
tail -n 50 logs/butler_<name>.log | grep -i error
```

Common config issues:
- Unknown tool names in `gated_tools`: Remove or fix tool name
- Invalid risk tier: Use `low|medium|high|critical`
- Invalid expiry hours: Use positive integer

**Validation:**
- [ ] Butler starts successfully
- [ ] Module registered in logs
- [ ] 13 approval tools available via MCP
- [ ] Test pending action creation

### Phase 3: Standing Rules Setup

**Objective:** Create standing rules for safe, repeatable operations

**Steps:**

1. **Identify safe patterns:**
   
   Review recent outbound actions to identify repeatable patterns:
   ```bash
   # Query recent email sends
   psql -d butler_<name> -c "
     SELECT tool_name, tool_args->>'to', COUNT(*) 
     FROM session_tool_calls 
     WHERE tool_name LIKE '%send%' 
       AND created_at > NOW() - INTERVAL '7 days'
     GROUP BY tool_name, tool_args->>'to'
     ORDER BY count DESC
     LIMIT 20;
   "
   ```

2. **Create standing rules via MCP:**
   ```python
   # Example: Auto-approve emails to team
   await butler.call_tool("create_approval_rule", {
       "tool_name": "bot_email_send",
       "arg_constraints": {
           "to": {"type": "pattern", "value": "*@mycompany.com"}
       },
       "description": "Auto-approve emails to team members",
       "max_uses": 100,  # Bounded scope for safety
   })
   ```

3. **Start conservative:**
   
   For first rollout, create rules for:
   - Internal notifications (low risk)
   - Status updates to known recipients (low-medium risk)
   - Avoid broad rules for external sends initially

4. **Verify rules active:**
   ```bash
   # List active rules via API
   curl http://localhost:<port>/api/approvals/rules?active_only=true
   ```

**Fallback:**

If rule creation fails:
- Check actor authentication is provided
- Verify tool name matches registered tools
- For high-risk rules, ensure bounded scope (expires_at or max_uses)
- For high-risk rules, ensure narrow constraints (at least one exact/pattern)

**Validation:**
- [ ] Rules created successfully
- [ ] Rules appear in frontend rules list
- [ ] Test auto-approval with matching invocation
- [ ] Verify use_count increments

### Phase 4: Frontend Access

**Objective:** Enable operator UI for approval management

**Steps:**

1. **Verify API endpoints:**
   ```bash
   # Test approvals API
   curl http://localhost:<port>/api/approvals/actions
   curl http://localhost:<port>/api/approvals/rules
   curl http://localhost:<port>/api/approvals/metrics
   ```

2. **Access frontend:**
   
   Navigate to:
   - `/approvals` - Action queue
   - `/approvals/rules` - Rule management

3. **Configure auto-refresh:**
   
   In Settings (`/settings`):
   - Enable auto-refresh
   - Set refresh interval (default 30s)

**Fallback:**

If frontend errors:
- Check API connectivity
- Verify approvals module enabled
- Check browser console for errors
- Fall back to MCP tool access for approvals

**Validation:**
- [ ] Frontend loads without errors
- [ ] Pending actions display correctly
- [ ] Metrics show accurate counts
- [ ] Filters work correctly

### Phase 5: Monitoring and Alerts

**Objective:** Set up operational visibility

**Steps:**

1. **Create approval metrics dashboard:**
   
   Monitor:
   - Pending action count (alert if > 50)
   - Auto-approval rate (alert if < 50% - indicates rule gaps)
   - Approval latency (median time pending → decided)
   - Rejection rate (alert if > 20% - indicates workflow issues)
   - Execution failure rate (alert if > 5%)

2. **Set up expiry automation:**
   
   Add cron job to expire stale actions:
   ```bash
   # Run hourly
   0 * * * * curl -X POST http://localhost:<port>/api/approvals/actions/expire-stale
   ```
   
   Or via butler scheduler:
   ```toml
   [[schedules]]
   cron = "0 * * * *"
   prompt = "Expire stale approval actions"
   source = "cron"
   enabled = true
   skill = "maintenance/expire_approvals"
   ```

3. **Monitor audit event volume:**
   ```sql
   SELECT 
     event_type, 
     COUNT(*), 
     DATE(occurred_at) as day
   FROM approval_events
   WHERE occurred_at > NOW() - INTERVAL '7 days'
   GROUP BY event_type, day
   ORDER BY day DESC, count DESC;
   ```

**Fallback:**

If monitoring fails:
- Fall back to manual checks via frontend
- Set calendar reminders for daily approval review
- Use email alerts for critical pending actions

**Validation:**
- [ ] Metrics dashboard updating
- [ ] Alerts configured
- [ ] Expiry automation running
- [ ] Audit logs populated

## Rollout Checklist

### Pre-Rollout
- [ ] Database backup completed
- [ ] Butler config reviewed
- [ ] Team trained on approval workflows
- [ ] Rollback plan documented

### Rollout
- [ ] Phase 1: Migrations applied
- [ ] Phase 2: Module configured and loaded
- [ ] Phase 3: Standing rules created
- [ ] Phase 4: Frontend access verified
- [ ] Phase 5: Monitoring enabled

### Post-Rollout
- [ ] Test approval flow end-to-end
- [ ] Verify auto-approval working
- [ ] Check audit events populating
- [ ] Monitor for errors in first 24h
- [ ] Review operator feedback

## Progressive Rollout Strategy

For multi-butler deployments, roll out in phases:

### Week 1: Low-Risk Butler
- Choose butler with low external output volume
- Full rollout with conservative rules
- Gather operator feedback

### Week 2: Medium-Risk Butler
- Apply learnings from Week 1
- Expand rule library
- Tune expiry windows

### Week 3: High-Risk Butlers
- Roll out to user-facing butlers
- Stricter rules and monitoring
- Daily approval reviews

### Week 4+: Full Fleet
- Complete rollout
- Optimize rules based on usage patterns
- Reduce manual approval rate to < 20%

## Common Rollout Issues

### Issue: High pending action backlog

**Symptoms:**
- Many pending actions accumulating
- Low auto-approval rate
- Operator overload

**Resolution:**
1. Review pending actions for patterns
2. Create standing rules for safe patterns
3. Bulk approve homogeneous low-risk items
4. Adjust expiry windows to reduce backlog
5. Consider raising risk thresholds for some tools

### Issue: Execution failures

**Symptoms:**
- Actions approved but execution fails
- Error payloads in execution_result

**Resolution:**
1. Check tool executor is wired correctly
2. Verify tool arguments are valid
3. Check for transient failures (retry)
4. Review error logs for root cause
5. Fix underlying tool issues

### Issue: Rule matching failures

**Symptoms:**
- Expected auto-approvals still pending
- Rules not matching despite correct constraints

**Resolution:**
1. Verify rule is active and not expired
2. Check use_count not exhausted
3. Review constraint specificity
4. Test constraint matching with action args
5. Check for precedence conflicts with other rules

### Issue: Frontend errors

**Symptoms:**
- 404/500 errors on approvals pages
- Empty state despite pending actions
- API timeouts

**Resolution:**
1. Verify approvals module enabled
2. Check API endpoint connectivity
3. Verify database migrations applied
4. Check frontend build version matches backend
5. Review API error logs

## Rollback Procedures

### Emergency Rollback (disable approvals immediately)

```toml
[modules.approvals]
enabled = false
```

Restart butler. All gated tools revert to ungated behavior.

**Impact:**
- All pending actions remain in queue (no data loss)
- Standing rules remain in database
- Can re-enable later without loss

### Full Rollback (remove approvals completely)

```bash
# Rollback migrations
alembic downgrade -2  # Remove both approval migrations

# Remove config
# Delete [modules.approvals] section from butler.toml

# Restart butler
systemctl restart butler_<name>
```

**Impact:**
- All approval data deleted (pending actions, rules, events)
- No recovery possible - backup first!

### Partial Rollback (keep data, disable gating)

```toml
[modules.approvals]
enabled = true
# Remove all entries from gated_tools
[modules.approvals.gated_tools]
# (empty)
```

**Impact:**
- No new actions gated
- Existing pending actions can still be decided
- Standing rules inactive but preserved

## Support Contacts

- **Approvals module issues:** Platform team
- **Butler configuration:** Butler owner
- **Database issues:** Database team
- **Frontend issues:** Frontend team

## Version History

- 2026-02-15: Initial rollout guide (butlers-0p6.8)
