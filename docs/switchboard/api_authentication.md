# Switchboard Ingest API Authentication

Status: Normative (Operations Guide)  
Last updated: 2026-02-15  
Primary owner: Platform/Core

## Overview

The Switchboard ingest API uses bearer token authentication to secure connector submissions. This document covers the complete lifecycle of API tokens used for connector authentication, including generation, distribution, rotation, revocation, and scope management.

**Related Documentation:**
- `docs/connectors/interface.md` - Connector contract and API usage
- `docs/runbooks/connector_operations.md` - Connector deployment and operations
- `docs/operations/switchboard_operator_runbook.md` - Switchboard operator procedures

---

## Authentication Architecture

### Transport Layer Security

Authentication is enforced at the MCP transport layer by the butler framework before the ingest API handler is invoked. The ingest function (`roster/switchboard/tools/ingestion/ingest.py`) trusts that the caller has been validated and has permission to submit to the specified source endpoint.

**Key Design Principles:**
- Authentication happens outside the ingest handler logic
- No per-butler or per-endpoint authorization within the ingest function
- MCP client certificates or butler-specific tokens managed by the framework
- Bearer tokens used for HTTP API access by external connectors

### Token Types

| Token Type | Use Case | Scope | Lifetime |
|------------|----------|-------|----------|
| **Connector Token** | External connector processes (Telegram, Gmail) | Ingest API only, specific endpoint | Long-lived (90 days) |
| **MCP Client Certificate** | Internal butler-to-butler communication | Full MCP tool access | Framework-managed |
| **Development Token** | Local testing and development | Ingest API only, all endpoints | Short-lived (7 days) |

This document focuses on **Connector Tokens** used by external connector processes.

---

## Token Generation

### Prerequisites

Before generating connector tokens:
1. Switchboard butler must be deployed and healthy
2. Database migrations must be current (especially `sw_014` for token storage)
3. Operator must have admin access to the butler framework

### Generation Procedure

**Step 1: Determine Token Scope**

Each connector token should be scoped to:
- **Source Channel:** `telegram`, `email`, `slack`, etc.
- **Provider:** `telegram`, `gmail`, `imap`, etc.
- **Endpoint Identity:** Specific bot ID, mailbox address, or client ID

Example scope configurations:
```json
{
  "channel": "telegram",
  "provider": "telegram",
  "endpoint_identity": "my_support_bot"
}
```

```json
{
  "channel": "email",
  "provider": "gmail",
  "endpoint_identity": "gmail:user:support@example.com"
}
```

**Step 2: Generate Token**

Using the butler framework CLI:

```bash
# Generate a new connector token
bd token create \
  --type connector \
  --channel telegram \
  --provider telegram \
  --endpoint "my_support_bot" \
  --expires-in 90d \
  --description "Production Telegram support bot"
```

Output:
```json
{
  "token_id": "tok_abc123...",
  "token": "sw_live_xyz789abcdef...",
  "scope": {
    "channel": "telegram",
    "provider": "telegram",
    "endpoint_identity": "my_support_bot"
  },
  "expires_at": "2026-05-16T00:00:00Z",
  "created_at": "2026-02-15T12:00:00Z"
}
```

**CRITICAL SECURITY NOTICE:**  
The plaintext token (`sw_live_xyz789abcdef...`) is only displayed ONCE during generation. Store it securely immediately (see Distribution section). The framework only stores a cryptographic hash of the token.

**Step 3: Verify Token**

Test the token before deploying to production:

```bash
# Test token authentication
curl -sS -X POST "$SWITCHBOARD_API_BASE_URL/api/switchboard/ingest/health" \
  -H "Authorization: Bearer $SWITCHBOARD_API_TOKEN" \
  -H "Content-Type: application/json"
```

Expected response:
```json
{
  "status": "healthy",
  "authenticated": true,
  "scope": {
    "channel": "telegram",
    "endpoint_identity": "my_support_bot"
  }
}
```

---

## Token Distribution

### Secure Storage Requirements

Connector tokens MUST be stored in a secure secret management system:

**Recommended Solutions:**
- AWS Secrets Manager
- Google Cloud Secret Manager
- HashiCorp Vault
- Kubernetes Secrets (for container deployments)

**NEVER:**
- Commit tokens to version control
- Store tokens in plain text config files
- Share tokens via email or chat
- Reuse tokens across environments (dev/staging/prod)

### Deployment Configuration

**For External Connector Processes:**

Set the token as an environment variable:

```bash
export SWITCHBOARD_API_TOKEN="sw_live_xyz789abcdef..."
export SWITCHBOARD_API_BASE_URL="https://switchboard.example.com"
```

**For Containerized Deployments:**

Use secret injection:

```yaml
# Kubernetes example
apiVersion: v1
kind: Pod
metadata:
  name: telegram-connector
spec:
  containers:
  - name: connector
    image: butlers/telegram-connector:latest
    env:
    - name: SWITCHBOARD_API_TOKEN
      valueFrom:
        secretKeyRef:
          name: switchboard-connector-tokens
          key: telegram-bot-token
    - name: SWITCHBOARD_API_BASE_URL
      value: "https://switchboard.example.com"
```

**For Docker Compose:**

```yaml
# docker-compose.yml
services:
  telegram-connector:
    image: butlers/telegram-connector:latest
    env_file:
      - .env.secret  # Contains SWITCHBOARD_API_TOKEN
    environment:
      SWITCHBOARD_API_BASE_URL: "https://switchboard.example.com"
```

**Access Control:**
- Limit token access to connector runtime processes only
- Use IAM roles/policies to restrict secret access
- Enable audit logging for secret access
- Rotate access credentials for secret management systems regularly

---

## Token Rotation

### Rotation Schedule

**Recommended Rotation Intervals:**
- **Production Connectors:** Every 90 days (automated)
- **Development Tokens:** Every 7 days (automated)
- **Incident Response:** Immediate (manual)

### Automated Rotation Procedure

**Phase 1: Generate New Token**

```bash
# Generate replacement token with same scope
bd token create \
  --type connector \
  --channel telegram \
  --provider telegram \
  --endpoint "my_support_bot" \
  --expires-in 90d \
  --description "Production Telegram bot - Rotation $(date +%Y-%m-%d)" \
  --replaces tok_abc123
```

The `--replaces` flag links the new token to the old token in the audit trail.

**Phase 2: Update Secret Manager**

```bash
# AWS Secrets Manager example
aws secretsmanager update-secret \
  --secret-id switchboard/connectors/telegram-bot \
  --secret-string "$NEW_TOKEN"

# Verify update
aws secretsmanager get-secret-value \
  --secret-id switchboard/connectors/telegram-bot \
  --query SecretString
```

**Phase 3: Restart Connector Process**

```bash
# Kubernetes example
kubectl rollout restart deployment/telegram-connector

# Docker Compose example
docker-compose restart telegram-connector
```

**Phase 4: Verify New Token**

```bash
# Check connector logs for successful authentication
kubectl logs -f deployment/telegram-connector | grep "authenticated"

# Verify ingest submissions are working
curl -sS "$SWITCHBOARD_API_BASE_URL/api/switchboard/metrics/ingest/recent" \
  -H "Authorization: Bearer $ADMIN_TOKEN" | jq '.recent_submissions[] | select(.source.endpoint_identity == "my_support_bot")'
```

**Phase 5: Revoke Old Token**

After confirming the new token works (wait at least 1 hour):

```bash
bd token revoke tok_abc123 \
  --reason "Rotated to tok_def456 on $(date +%Y-%m-%d)"
```

### Grace Period Strategy

To enable zero-downtime rotation:

1. **Generate new token** but don't revoke old token yet
2. **Deploy new token** to connector configuration
3. **Wait for connector restart** and verify health
4. **Monitor for 1-24 hours** depending on criticality
5. **Revoke old token** only after confirming zero usage

During the grace period, both tokens are valid. Monitor old token usage:

```bash
bd token usage tok_abc123 --since "1 hour ago"
```

If usage is detected, extend grace period or investigate before revoking.

### Emergency Rotation

If a token is compromised:

**Immediate Actions:**
1. Revoke compromised token immediately (no grace period)
2. Generate replacement token
3. Update secret manager
4. Force restart all connector processes
5. Audit recent API usage for suspicious activity
6. File security incident report

```bash
# Emergency revocation
bd token revoke tok_abc123 \
  --reason "SECURITY: Token compromised, immediate revocation" \
  --force

# Generate replacement
bd token create \
  --type connector \
  --channel telegram \
  --provider telegram \
  --endpoint "my_support_bot" \
  --expires-in 90d \
  --description "EMERGENCY replacement for tok_abc123" \
  --replaces tok_abc123
```

---

## Token Revocation

### Revocation Scenarios

**Planned Revocation:**
- Token rotation completion
- Connector decommissioned
- Endpoint no longer active

**Emergency Revocation:**
- Token leak or compromise
- Suspicious API usage detected
- Security audit requirement

### Revocation Procedure

**Standard Revocation:**

```bash
bd token revoke <token_id> \
  --reason "Clear explanation for audit trail"
```

**Force Revocation (Skip Safety Checks):**

```bash
bd token revoke <token_id> \
  --reason "Security incident - token compromised" \
  --force
```

The `--force` flag skips grace period warnings and active usage checks. Use only for security incidents.

**Verify Revocation:**

```bash
# Check token status
bd token show <token_id>

# Verify authentication fails
curl -sS -X POST "$SWITCHBOARD_API_BASE_URL/api/switchboard/ingest/health" \
  -H "Authorization: Bearer $REVOKED_TOKEN" \
  -H "Content-Type: application/json"
```

Expected response after revocation:
```json
{
  "error": "invalid_token",
  "message": "Token has been revoked"
}
```

### Effects of Revocation

When a token is revoked:
1. **Immediate:** All subsequent API requests with that token will fail with `401 Unauthorized`
2. **In-flight requests:** May complete if already authenticated (typically < 30 seconds)
3. **Connector behavior:** Will receive authentication errors and should enter error state
4. **Audit trail:** Revocation is logged with operator identity and reason
5. **Metrics:** Failed authentication attempts will increment for the endpoint

### Revocation Audit

All revocations are logged in the `token_audit_log` table:

```sql
SELECT
    token_id,
    action_type,
    operator_identity,
    reason,
    performed_at
FROM token_audit_log
WHERE action_type = 'revoke'
ORDER BY performed_at DESC
LIMIT 50;
```

---

## Token Scope and Permissions

### Scope Enforcement

Connector tokens are scoped to specific ingestion endpoints. When a connector submits an ingest request, the token scope is validated against the envelope's source identity:

**Validation Rules:**
1. `source.channel` MUST match token scope `channel`
2. `source.provider` MUST match token scope `provider`
3. `source.endpoint_identity` MUST match token scope `endpoint_identity`

**Example:**

Token scope:
```json
{
  "channel": "telegram",
  "provider": "telegram",
  "endpoint_identity": "support_bot"
}
```

Valid ingest envelope:
```json
{
  "source": {
    "channel": "telegram",
    "provider": "telegram",
    "endpoint_identity": "support_bot"
  },
  ...
}
```

Invalid ingest envelope (different endpoint):
```json
{
  "source": {
    "channel": "telegram",
    "provider": "telegram",
    "endpoint_identity": "sales_bot"  // ❌ Mismatch
  },
  ...
}
```

Response for scope violation:
```json
{
  "error": "forbidden",
  "message": "Token scope does not permit ingestion for endpoint 'sales_bot'"
}
```

### Permission Model

Connector tokens have **minimal privileges**:

| Operation | Permitted |
|-----------|-----------|
| Submit to ingest API (`/api/switchboard/ingest`) | ✅ Yes (scoped) |
| Read ingestion health (`/api/switchboard/ingest/health`) | ✅ Yes |
| Read ingestion metrics | ❌ No |
| Replay dead-letter requests | ❌ No (operator-only) |
| Reroute requests | ❌ No (operator-only) |
| Access butler tools | ❌ No (MCP-only) |

**Principle of Least Privilege:**  
Connector tokens can ONLY submit ingest envelopes for their designated endpoint. They cannot:
- Read or modify other endpoints' data
- Access Switchboard internal tools
- Perform operator actions
- Escalate privileges

### Multi-Endpoint Tokens

For connectors managing multiple endpoints (e.g., multiple bots or mailboxes), use separate tokens per endpoint:

**✅ Recommended (one token per endpoint):**
```bash
bd token create --channel telegram --endpoint "support_bot" --expires-in 90d
bd token create --channel telegram --endpoint "sales_bot" --expires-in 90d
bd token create --channel telegram --endpoint "alerts_bot" --expires-in 90d
```

**❌ Not Supported (wildcard or multi-endpoint tokens):**
```bash
# This does NOT work
bd token create --channel telegram --endpoint "*" --expires-in 90d
```

**Rationale:**  
Per-endpoint tokens enable:
- Fine-grained revocation (compromise one bot, revoke only that token)
- Clear audit trails (which endpoint submitted which events)
- Principle of least privilege enforcement

---

## Token Management Best Practices

### Naming Conventions

Use descriptive token descriptions for easy identification:

**Good Examples:**
- `"Production Telegram support bot - support@example.com"`
- `"Staging Gmail connector - test-inbox@example.com"`
- `"Development Slack webhook - #engineering channel"`

**Bad Examples:**
- `"Token 1"`
- `"Test"`
- `"abc"`

### Token Lifecycle Tracking

Maintain a token inventory spreadsheet or database:

| Token ID | Endpoint | Environment | Created | Expires | Last Rotated | Owner |
|----------|----------|-------------|---------|---------|--------------|-------|
| tok_abc123 | support_bot | Production | 2026-02-15 | 2026-05-16 | 2026-02-15 | platform-team |
| tok_def456 | test_bot | Staging | 2026-02-10 | 2026-02-17 | N/A | dev-team |

### Monitoring and Alerts

**Recommended Alerts:**

1. **Token Expiration Warning (30 days before expiry):**
   ```bash
   bd token list --expires-within 30d --format json | \
     jq -r '.[] | "WARNING: Token \(.token_id) for \(.scope.endpoint_identity) expires \(.expires_at)"'
   ```

2. **Failed Authentication Spike:**
   ```sql
   SELECT COUNT(*)
   FROM api_access_log
   WHERE status_code = 401
   AND timestamp > now() - INTERVAL '1 hour'
   GROUP BY endpoint_identity
   HAVING COUNT(*) > 100;
   ```

3. **Unused Token Detection (no usage in 7 days):**
   ```bash
   bd token list --unused-since 7d
   ```

### Separation of Environments

**NEVER share tokens between environments:**

| Environment | Token Prefix | Expiry | Rotation |
|-------------|--------------|--------|----------|
| Production | `sw_live_` | 90 days | Automated |
| Staging | `sw_staging_` | 30 days | Automated |
| Development | `sw_dev_` | 7 days | Manual |

Each environment should have completely separate tokens with different scopes and credentials.

---

## Incident Response

### Token Leak Detection

**Indicators of Compromise:**
- Token found in public GitHub repository
- Token found in application logs
- Unusual API usage patterns
- Authentication from unexpected IP addresses

**Response Procedure:**
1. **Immediate revocation** (no grace period)
2. **Generate replacement token**
3. **Audit all API requests** made with compromised token
4. **Review ingest submissions** for malicious payloads
5. **Update secret management access controls**
6. **File security incident report**

**Audit Query:**
```sql
SELECT
    request_id,
    source_endpoint_identity,
    normalized_text,
    received_at
FROM message_inbox
WHERE request_context->>'authenticated_token_id' = 'tok_COMPROMISED'
ORDER BY received_at DESC;
```

### Token Misconfiguration

**Symptoms:**
- Connector failing with `403 Forbidden`
- Scope mismatch errors in logs
- Ingest submissions rejected

**Diagnosis:**
1. Check token scope: `bd token show <token_id>`
2. Compare with connector configuration (source channel/provider/endpoint)
3. Verify environment variable is correct
4. Test token with health endpoint

**Resolution:**
1. If scope is incorrect: Generate new token with correct scope
2. If configuration is wrong: Update connector environment variables
3. If token is expired: Rotate to new token
4. Always verify fix with test submission before closing incident

---

## Migration from Legacy Authentication

### Identifying Legacy Connectors

Legacy connectors may use:
- No authentication (internal-only MCP calls)
- Shared butler tokens (not connector-specific)
- Hard-coded API keys

**Find legacy connectors:**
```bash
# Check for connectors without dedicated tokens
bd token list --type connector | jq -r '.[].scope.endpoint_identity' > /tmp/tokened_endpoints.txt
bd connector list | jq -r '.[].endpoint_identity' > /tmp/all_endpoints.txt
comm -23 <(sort /tmp/all_endpoints.txt) <(sort /tmp/tokened_endpoints.txt)
```

### Migration Procedure

**For Each Legacy Connector:**

1. **Generate dedicated token:**
   ```bash
   bd token create \
     --type connector \
     --channel <channel> \
     --provider <provider> \
     --endpoint <identity> \
     --expires-in 90d \
     --description "Migration from legacy auth"
   ```

2. **Update connector configuration:**
   ```bash
   # Add SWITCHBOARD_API_TOKEN to connector environment
   kubectl set env deployment/<connector-name> \
     SWITCHBOARD_API_TOKEN=<new_token>
   ```

3. **Verify authentication:**
   ```bash
   kubectl logs deployment/<connector-name> | grep "authenticated"
   ```

4. **Revoke legacy credentials:**
   ```bash
   bd token revoke <legacy_token_id> \
     --reason "Migrated to dedicated connector token"
   ```

5. **Update documentation and runbooks**

---

## Troubleshooting

### Common Issues

#### Issue: `401 Unauthorized` errors

**Causes:**
- Token is expired
- Token is revoked
- Token format is incorrect (missing `Bearer` prefix)
- Environment variable not set

**Diagnosis:**
```bash
# Check token status
bd token show <token_id>

# Verify environment variable
echo $SWITCHBOARD_API_TOKEN

# Test authentication
curl -sS "$SWITCHBOARD_API_BASE_URL/api/switchboard/ingest/health" \
  -H "Authorization: Bearer $SWITCHBOARD_API_TOKEN"
```

**Resolution:**
- If expired: Rotate to new token
- If revoked: Generate new token (should not happen accidentally)
- If missing `Bearer`: Update connector code to include prefix
- If not set: Configure environment variable properly

#### Issue: `403 Forbidden` errors

**Causes:**
- Token scope doesn't match endpoint identity
- Token is valid but lacks permission for operation

**Diagnosis:**
```bash
# Check token scope
bd token show <token_id> | jq '.scope'

# Check ingest envelope source fields
# Compare source.channel, source.provider, source.endpoint_identity
```

**Resolution:**
- Generate new token with correct scope matching connector's endpoint
- Update connector configuration if endpoint identity changed

#### Issue: Token rotation causes brief downtime

**Cause:** Connector restarted before new token was deployed

**Prevention:**
- Always use grace period strategy (both tokens valid during transition)
- Deploy new token before revoking old token
- Monitor old token usage before revocation

**Recovery:**
- If connector is down: Deploy new token and restart immediately
- If messages are lost: Connectors should replay from checkpoint (idempotent)

---

## Reference

### Token Format

Connector tokens follow this format:
```
sw_{env}_{random}
```

Where:
- `env`: `live`, `staging`, or `dev`
- `random`: 32-character cryptographically secure random string (base62)

Example: `sw_live_4kN9xQ7pL2mY8wR3tZ5vX1sA6hB0cD4e`

### CLI Command Reference

```bash
# List all tokens
bd token list [--type connector] [--expires-within <duration>] [--unused-since <duration>]

# Show token details
bd token show <token_id>

# Create token
bd token create --type connector --channel <channel> --provider <provider> --endpoint <identity> --expires-in <duration> [--description <desc>] [--replaces <old_token_id>]

# Revoke token
bd token revoke <token_id> --reason <reason> [--force]

# Token usage statistics
bd token usage <token_id> [--since <duration>]

# Audit log
bd token audit [<token_id>] [--since <duration>]
```

### Database Schema

Token storage (migration `sw_014`):

```sql
CREATE TABLE connector_tokens (
    token_id TEXT PRIMARY KEY,
    token_hash TEXT NOT NULL UNIQUE,  -- bcrypt hash of plaintext token
    scope JSONB NOT NULL,              -- {channel, provider, endpoint_identity}
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL,
    revoked_at TIMESTAMPTZ,
    revoked_by TEXT,
    revoked_reason TEXT,
    description TEXT,
    replaces_token_id TEXT REFERENCES connector_tokens(token_id)
);

CREATE INDEX idx_connector_tokens_expires ON connector_tokens(expires_at) WHERE revoked_at IS NULL;
CREATE INDEX idx_connector_tokens_endpoint ON connector_tokens((scope->>'endpoint_identity')) WHERE revoked_at IS NULL;
```

Token audit log:

```sql
CREATE TABLE token_audit_log (
    id SERIAL PRIMARY KEY,
    token_id TEXT NOT NULL REFERENCES connector_tokens(token_id),
    action_type TEXT NOT NULL,  -- 'create', 'revoke', 'rotate', 'usage'
    operator_identity TEXT,
    reason TEXT,
    performed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    metadata JSONB
);
```

---

## Appendix: Security Checklist

Before deploying connector tokens to production:

- [ ] Token stored in secure secret manager (not version control)
- [ ] Token scope matches connector endpoint exactly
- [ ] Token expiration set appropriately (90 days for prod)
- [ ] Connector process uses environment variable (not hard-coded)
- [ ] Secret access restricted via IAM/RBAC
- [ ] Audit logging enabled for secret access
- [ ] Rotation schedule documented and automated
- [ ] Incident response runbook updated with token ID
- [ ] Monitoring alerts configured for expiration and failed auth
- [ ] Token inventory spreadsheet updated
- [ ] Team trained on rotation and revocation procedures

---

## Contact and Support

- **Security Incidents:** `security@example.com` (PagerDuty: `butler-security`)
- **Token Management Questions:** `#butler-platform` (Slack)
- **Connector Issues:** `#connectors` (Slack)
- **On-call Escalation:** Page `butler-sre` via PagerDuty

Last reviewed: 2026-02-15
Next review: 2026-05-15
