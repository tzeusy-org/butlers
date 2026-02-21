# Connector Operations Runbook

Status: Normative (Operations Guide)  
Last updated: 2026-02-15  
Covers: Telegram bot connector, Gmail connector

## Overview

This runbook covers deployment, monitoring, recovery, and rollback operations for connector-owned ingestion infrastructure. Connectors are transport-only adapters that submit normalized events to Switchboard's canonical ingest API.

**Key Principles:**
- Connectors own source polling/webhook handling
- Switchboard owns canonical ingest, dedupe, and routing
- Idempotent submission: replays are safe
- At-least-once delivery from connector to Switchboard
- Exactly-once effect at canonical request layer (via Switchboard dedupe)
- Connector MCP transport remains SSE (`/sse`) during spawner runtime
  streamable HTTP (`/mcp`) rollout; do not migrate connector URLs as part of
  this change.

**Related Documentation:**
- `docs/connectors/interface.md` - Connector contract
- `docs/connectors/telegram_bot.md` - Telegram connector spec
- `docs/connectors/gmail.md` - Gmail connector spec
- `docs/connectors/connector_ingestion_migration_delta_matrix.md` - Migration plan
- `docs/operations/spawner-streamable-http-rollout.md` - Runtime transport
  cutover and rollback procedure

---

## Deployment Modes

### Telegram Bot Connector

#### Polling Mode (Development)

**Use Case:** Local development, testing, or environments without public HTTPS endpoint.

**Configuration:**
```bash
# Required environment variables
export SWITCHBOARD_MCP_URL="http://localhost:40100/sse"
export CONNECTOR_PROVIDER="telegram"
export CONNECTOR_CHANNEL="telegram"
export CONNECTOR_ENDPOINT_IDENTITY="your_bot_username"
export BUTLER_TELEGRAM_TOKEN="your-telegram-bot-token"
export CONNECTOR_CURSOR_PATH="/path/to/telegram_cursor.json"
export CONNECTOR_POLL_INTERVAL_S="1.0"
export CONNECTOR_MAX_INFLIGHT="8"
```

**Startup:**
```bash
# Run connector process
uv run python -m butlers.connectors.telegram_bot
```

**Monitoring:**
- Check connector logs for poll cycles
- Verify checkpoint file is updated after successful batches
- Monitor Switchboard ingest API for `202 Accepted` responses

**Shutdown:**
- Send SIGTERM or SIGINT to connector process
- Connector will finish processing current batch and save checkpoint
- Safe to restart: polling will resume from last checkpoint

#### Webhook Mode (Production)

**Use Case:** Production deployments with public HTTPS endpoint.

**Configuration:**
```bash
# Additional webhook-specific variables
export CONNECTOR_WEBHOOK_URL="https://your-domain.com/telegram/webhook"
```

**Startup:**
1. Ensure webhook endpoint is reachable and returns 200 OK on health checks
2. Start connector process (registers webhook with Telegram)
3. Telegram will POST updates to webhook URL
4. Webhook handler should call `connector.process_webhook_update(update)`

**Monitoring:**
- Verify webhook registration succeeded (check logs for "Registered Telegram webhook")
- Monitor webhook endpoint metrics (request rate, latency, errors)
- Track ingest API submission success/failure rates

**Cutover from Polling to Webhook:**
1. Deploy webhook endpoint but don't register webhook yet
2. Keep polling mode running
3. Register webhook via connector startup
4. Verify webhook receives updates
5. Stop polling mode connector
6. Monitor for any missed updates (should be none due to overlap period)

**Rollback to Polling:**
1. Call Telegram `deleteWebhook` API or restart connector without `CONNECTOR_WEBHOOK_URL`
2. Start polling mode connector with checkpoint from before cutover
3. Connector will replay from last checkpoint (duplicates are safe)

---

### Gmail Connector

#### Watch + History Delta Mode (Primary)

**Use Case:** Near real-time email ingestion using Gmail push notifications.

**Configuration:**
```bash
# Required environment variables
export SWITCHBOARD_MCP_URL="http://localhost:40100/sse"
export CONNECTOR_PROVIDER="gmail"
export CONNECTOR_CHANNEL="email"
export CONNECTOR_ENDPOINT_IDENTITY="gmail:user:your-email@gmail.com"
export CONNECTOR_CURSOR_PATH="/path/to/gmail_cursor.json"
export CONNECTOR_MAX_INFLIGHT="8"

# Gmail OAuth credentials (DB-managed)
# Set DB connectivity and complete the dashboard OAuth flow before starting connector
export DATABASE_URL="postgres://butlers:butlers@localhost:5432/butlers"
export CONNECTOR_BUTLER_DB_NAME="butler_general"  # optional local override DB
export BUTLER_SHARED_DB_NAME="butlers"  # shared credential DB (default)
# OAuth app credentials are managed by the dashboard/API side; connector reads
# runtime Gmail credentials from DB (`butler_secrets`) only.

# Optional: watch renewal and polling intervals
export GMAIL_WATCH_RENEW_INTERVAL_S="86400"  # 1 day
export GMAIL_POLL_INTERVAL_S="60"  # 1 minute
```

**OAuth Setup (First Time):**
1. Create OAuth 2.0 credentials in Google Cloud Console
2. Set redirect URI to `http://localhost:8080` (or your callback URL)
3. Complete the dashboard OAuth flow to persist credentials in DB-backed secrets
4. Verify connector has DB connectivity to the shared/local credential schema

**Startup:**
```bash
# Run connector process
uv run python -m butlers.connectors.gmail
```

**Monitoring:**
- Check logs for "Gmail connector starting" and historyId initialization
- Monitor OAuth token refresh cycles (should happen before expiration)
- Track history changes processed vs skipped (duplicates)
- Monitor ingest API submission metrics

**Shutdown:**
- Send SIGTERM to connector process
- Connector will finish processing current batch and save cursor
- Safe to restart: will resume from last historyId

---

## Checkpoint Recovery

### Telegram Bot Connector

**Checkpoint File Format:**
```json
{
  "last_update_id": 123456789
}
```

**Recovery Scenarios:**

1. **Clean Restart:**
   - Connector loads checkpoint and polls from `last_update_id + 1`
   - No data loss, may have duplicate submissions (safe)

2. **Checkpoint File Missing:**
   - Connector starts from scratch (update_id 0)
   - All updates since bot creation will be replayed
   - Switchboard dedupe prevents duplicate processing

3. **Checkpoint File Corrupted:**
   - Connector logs error and starts from scratch
   - Same behavior as missing checkpoint

4. **Manual Recovery:**
   ```bash
   # Edit checkpoint to replay from specific update_id
   echo '{"last_update_id": 123456000}' > /path/to/telegram_cursor.json
   # Restart connector
   ```

**Checkpoint Persistence:**
- Checkpoint is saved after each successful batch submission
- Uses atomic write (write to .tmp, then rename)
- If connector crashes mid-batch, at-most the last batch is replayed
- Duplicates are handled by Switchboard dedupe

### Gmail Connector

**Checkpoint File Format:**
```json
{
  "history_id": "987654321",
  "last_updated_at": "2026-02-15T10:00:00Z"
}
```

**Recovery Scenarios:**

1. **Clean Restart:**
   - Connector loads cursor and fetches history since `history_id`
   - Processes new messages and updates cursor

2. **Checkpoint File Missing:**
   - Connector fetches current Gmail profile to get latest historyId
   - Initializes cursor with current historyId
   - Only processes new emails going forward (no historical replay)

3. **History ID Too Old (404 from Gmail):**
   - Gmail history retention is limited (~30 days)
   - Connector catches 404, fetches current profile, and resets cursor
   - Logs warning about potential missed messages
   - Operator should verify no critical emails were missed

4. **Manual Recovery:**
   ```bash
   # Set cursor to specific historyId
   cat > /path/to/gmail_cursor.json << 'JSON'
   {
     "history_id": "987654000",
     "last_updated_at": "2026-02-15T09:00:00Z"
   }
   JSON
   # Restart connector
   ```

**Checkpoint Persistence:**
- Cursor saved after each successful batch of message ingestions
- If connector crashes, at-most the last batch is replayed
- Duplicates handled by Switchboard dedupe (Message-ID is stable)

---

## Cutover Operations

### Cutover from Module-Owned Ingestion to Connector-Owned

**Context:** Migration from in-daemon polling/webhook to external connector processes (see `docs/connectors/connector_ingestion_migration_delta_matrix.md`).

**Telegram Bot Cutover Steps:**

1. **Pre-Cutover:**
   - Ensure Switchboard ingest API is deployed and stable
   - Verify connector implementation matches `docs/connectors/telegram_bot.md`
   - Run conformance tests: `uv run pytest tests/integration/test_connector_conformance.py -k telegram`

2. **Canary Phase:**
   - Deploy connector alongside existing module polling (both running)
   - Monitor for duplicate request_ids (should be same for same update_id)
   - Verify connector checkpoint advances correctly
   - Let run for 24-48 hours

3. **Cutover:**
   - Stop module polling loop (set `mode="disabled"` in butler.toml)
   - Keep connector running
   - Monitor for any missed updates (should be none)
   - If webhook mode: register webhook during this phase

4. **Verification:**
   - Check Switchboard `message_inbox` for continuous request stream
   - Verify no gaps in update_id sequence
   - Test end-to-end: send Telegram message → verify ingestion → verify routing

**Gmail Connector Cutover Steps:**

1. **Pre-Cutover:**
   - Obtain OAuth credentials and refresh token
   - Initialize Gmail cursor with current historyId
   - Run conformance tests: `uv run pytest tests/integration/test_connector_conformance.py -k gmail`

2. **Canary Phase:**
   - Deploy connector alongside existing `bot_email_check_and_route_inbox` tool
   - Both paths will ingest same emails (duplicates are safe)
   - Monitor for duplicate request_ids (should match on Message-ID)
   - Let run for 24-48 hours

3. **Cutover:**
   - Disable inbox polling tool (remove from cron schedule)
   - Keep connector running
   - Monitor for any missed emails

4. **Verification:**
   - Send test email to monitored mailbox
   - Verify ingestion within ~60 seconds
   - Check Switchboard logs for accepted ingest
   - Verify downstream routing occurred

---

## Rollback Operations

### Rollback from Connector to Module-Owned Ingestion

**When to Rollback:**
- Connector crashes repeatedly
- Ingest API unavailable/degraded
- Duplicate detection failures
- Data loss or corruption

**Telegram Rollback:**

1. **Stop Connector:**
   ```bash
   # Send SIGTERM to connector process
   kill -TERM <connector-pid>
   ```

2. **Disable Webhook (if applicable):**
   ```bash
   # Call Telegram deleteWebhook or restart without CONNECTOR_WEBHOOK_URL
   curl -X POST "https://api.telegram.org/bot<token>/deleteWebhook"
   ```

3. **Re-enable Module Polling:**
   - Set `mode="polling"` in butler.toml
   - Restart butler daemon
   - Module will resume polling from its own checkpoint

4. **Verify:**
   - Check butler logs for "Telegram polling started"
   - Send test message and verify ingestion
   - Monitor for duplicate processing (may occur during overlap)

**Gmail Rollback:**

1. **Stop Connector:**
   ```bash
   kill -TERM <connector-pid>
   ```

2. **Re-enable Inbox Polling Tool:**
   - Re-add `bot_email_check_and_route_inbox` to cron schedule
   - Tool will resume from its own internal state

3. **Verify:**
   - Send test email
   - Check butler logs for inbox polling activity
   - Verify email is ingested and routed

---

## Monitoring and Alerting

### Key Metrics

**Connector Health:**
- Process uptime
- Checkpoint update frequency
- Last successful submission timestamp
- Error rate (ingestion, source API, network)

**Ingest API:**
- Request rate (accepted, duplicate, rejected)
- Response latency (p50, p95, p99)
- Error rate (4xx, 5xx)
- Duplicate detection rate

**Source API:**
- Telegram getUpdates/webhook rate
- Gmail history fetch rate
- OAuth token refresh success rate
- Rate limit hits (429 responses)

### Alert Conditions

**Critical:**
- Connector process down for > 5 minutes
- Checkpoint not updated for > 10 minutes (indicates stall)
- Ingest API 5xx rate > 10% for > 2 minutes
- OAuth token refresh failures

**Warning:**
- Ingest API latency p95 > 2 seconds
- Duplicate detection rate > 50% (indicates replay loop)
- Source API rate limit hits
- Checkpoint file corruption detected

### Log Patterns

**Success:**
```
INFO: Submitted to Switchboard ingest request_id=<uuid> duplicate=false
INFO: Saved checkpoint historyId=<id>
```

**Duplicate (expected):**
```
INFO: Submitted to Switchboard ingest request_id=<uuid> duplicate=true
```

**Errors:**
```
ERROR: Switchboard ingest API error status_code=500
ERROR: Failed to load checkpoint, starting from scratch
WARNING: History ID 12345 is too old, resetting to current
```

---

## Troubleshooting

### Connector Not Processing Updates

**Symptoms:**
- Checkpoint not advancing
- No log activity

**Diagnosis:**
1. Check connector process is running: `ps aux | grep connector`
2. Check source API connectivity: `curl -I https://api.telegram.org` or Gmail API
3. Check Switchboard MCP server health: `curl $SWITCHBOARD_MCP_URL`
4. Review connector logs for errors

**Resolution:**
- Restart connector if crashed
- Fix network/firewall issues
- Verify credentials (Telegram token, Gmail OAuth)
- Check Switchboard API is accepting requests

### Duplicate Ingestion Loop

**Symptoms:**
- Same update processed repeatedly
- Checkpoint not advancing
- High duplicate detection rate

**Diagnosis:**
1. Check checkpoint file is writable and being updated
2. Review connector logs for checkpoint save errors
3. Check for multiple connector processes running concurrently

**Resolution:**
- Fix checkpoint file permissions
- Kill duplicate connector processes
- Verify atomic checkpoint write logic

### OAuth Token Refresh Failures (Gmail)

**Symptoms:**
```
ERROR: Failed to fetch history changes: 401 Unauthorized
```

**Diagnosis:**
1. Check refresh token is valid (not revoked)
2. Verify OAuth client credentials are correct
3. Check token hasn't been revoked in Google Cloud Console

**Resolution:**
- Re-run OAuth flow to obtain new refresh token
- Ensure credentials are persisted in `butler_secrets` via dashboard OAuth flow
- Restart connector

### Missed Messages/Updates

**Symptoms:**
- Gap in update_id sequence (Telegram)
- Missing emails in ingestion log (Gmail)

**Diagnosis:**
1. Check connector uptime during suspected gap period
2. Review Switchboard ingest logs for rejected submissions
3. Check for checkpoint corruption or reset

**Resolution:**
- If checkpoint was lost: replay from earlier checkpoint (duplicates are safe)
- If ingest API rejected: review error logs and resubmit if needed
- For Gmail: if historyId gap is recent, replay from earlier cursor
- For older gaps: may need manual message fetch and submission

---

## Appendix: Environment Variable Reference

### Telegram Bot Connector

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `SWITCHBOARD_MCP_URL` | Yes | - | SSE endpoint for Switchboard MCP server |
| `CONNECTOR_PROVIDER` | Yes | `telegram` | Provider identifier |
| `CONNECTOR_CHANNEL` | Yes | `telegram` | Channel identifier |
| `CONNECTOR_ENDPOINT_IDENTITY` | Yes | - | Bot username or ID |
| `BUTLER_TELEGRAM_TOKEN` | Yes | - | Telegram bot API token |
| `CONNECTOR_CURSOR_PATH` | Yes** | - | Checkpoint file path (**for polling mode) |
| `CONNECTOR_POLL_INTERVAL_S` | Yes** | - | Poll interval in seconds (**for polling mode) |
| `CONNECTOR_WEBHOOK_URL` | Yes*** | - | Webhook URL (***for webhook mode) |
| `CONNECTOR_MAX_INFLIGHT` | No | `8` | Max concurrent ingest submissions |

### Gmail Connector

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `SWITCHBOARD_MCP_URL` | Yes | - | SSE endpoint for Switchboard MCP server |
| `CONNECTOR_PROVIDER` | Yes | `gmail` | Provider identifier |
| `CONNECTOR_CHANNEL` | Yes | `email` | Channel identifier |
| `CONNECTOR_ENDPOINT_IDENTITY` | Yes | - | Mailbox identity (e.g., `gmail:user:email@example.com`) |
| `CONNECTOR_CURSOR_PATH` | Yes | - | Checkpoint file path |
| `CONNECTOR_MAX_INFLIGHT` | No | `8` | Max concurrent ingest submissions |
| `DATABASE_URL` | No | - | DB URL for DB-first credential resolution |
| `CONNECTOR_BUTLER_DB_NAME` | No | - | Local butler DB name for per-butler override secrets |
| `BUTLER_SHARED_DB_NAME` | No | `butlers` | Shared credential DB name |
| `GMAIL_WATCH_RENEW_INTERVAL_S` | No | `86400` | Watch renewal interval (seconds) |
| `GMAIL_POLL_INTERVAL_S` | No | `60` | History polling interval (seconds) |

---

## Token Management

Connector authentication uses bearer tokens managed by the Switchboard butler framework.

**Complete Token Management Documentation:**  
See `docs/switchboard/api_authentication.md` for full coverage of:
- Token generation and issuance
- Secure storage and distribution
- Rotation procedures (automated and emergency)
- Revocation and incident response
- Token scope and permissions
- Security best practices

**Token-Related Operations Quick Reference:**

### Token Rotation (Planned)

```bash
# Generate new token
NEW_TOKEN=$(bd token create \
  --type connector \
  --channel telegram \
  --provider telegram \
  --endpoint "support_bot" \
  --expires-in 90d \
  --replaces <old_token_id> \
  --json | jq -r '.token')

# Update secret manager
aws secretsmanager update-secret \
  --secret-id switchboard/connectors/telegram-bot \
  --secret-string "$NEW_TOKEN"

# Restart connector
kubectl rollout restart deployment/telegram-connector

# Verify health
kubectl logs -f deployment/telegram-connector | grep "authenticated"

# Revoke old token after grace period
bd token revoke <old_token_id> --reason "Rotated to new token"
```

### Token Revocation (Emergency)

```bash
# Immediate revocation if compromised
bd token revoke <token_id> \
  --reason "SECURITY: Token compromised, immediate revocation" \
  --force

# Generate replacement
NEW_TOKEN=$(bd token create \
  --type connector \
  --channel telegram \
  --provider telegram \
  --endpoint "support_bot" \
  --expires-in 90d \
  --description "EMERGENCY replacement" \
  --json | jq -r '.token')

# Update and restart immediately
aws secretsmanager update-secret \
  --secret-id switchboard/connectors/telegram-bot \
  --secret-string "$NEW_TOKEN"
kubectl rollout restart deployment/telegram-connector --force
```

For detailed procedures and troubleshooting, consult `docs/switchboard/api_authentication.md`.
