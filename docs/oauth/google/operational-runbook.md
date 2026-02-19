# Google OAuth Operational Runbook

> **Deprecation Notice (see butlers-973.7):**
> `BUTLER_GOOGLE_CALENDAR_CREDENTIALS_JSON`, `GMAIL_CLIENT_ID`, `GMAIL_CLIENT_SECRET`,
> `GMAIL_REFRESH_TOKEN`, and `GOOGLE_REFRESH_TOKEN` are deprecated env vars. Credentials
> should now be stored in the butler database via the dashboard OAuth flow and resolved
> at runtime using DB-first resolution. See `docs/oauth/google/setup-guide.md` for the
> updated credential setup workflow. The env-var fallback remains functional for backward
> compatibility during migration.

## Overview

This runbook covers day-to-day operations, troubleshooting, and maintenance for Google OAuth tokens in production Butlers deployments. It complements `setup-guide.md` with operational procedures.

## Section 0: Verification Checklist

Use this checklist after initial OAuth setup or after any credential rotation.
Two paths are covered: **Local Development (Tailscale HTTPS)** and **Production**.

---

### 0.1: Local Development Verification (Tailscale HTTPS)

Prerequisites:
- Tailscale installed and authenticated (`tailscale up`)
- Butlers dev environment running (`./dev.sh`)
- Google Cloud Console redirect URI includes your Tailscale HTTPS URL

#### Checklist

- [ ] **Tailscale is serving the backend**
  ```bash
  tailscale serve status
  # Expected: https://<your-name>.ts.net/ → http://localhost:8200
  ```

- [ ] **Backend API is reachable via HTTPS**
  ```bash
  curl -s https://<your-tailscale-name>.ts.net/api/health | jq .
  # Expected: {"status": "ok", ...}
  ```

- [ ] **OAuth status shows not_configured or connected**
  ```bash
  curl -s https://<your-tailscale-name>.ts.net/api/oauth/status | jq .google.state
  # Expected: "not_configured" (before bootstrap) or "connected" (after)
  ```

- [ ] **OAuth start redirects to Google**
  ```bash
  curl -s -o /dev/null -w "%{http_code}"     https://<your-tailscale-name>.ts.net/api/oauth/google/start
  # Expected: 302
  curl -s "https://<your-tailscale-name>.ts.net/api/oauth/google/start?redirect=false" | jq .authorization_url
  # Expected: a URL starting with "https://accounts.google.com/..."
  ```

- [ ] **Complete the OAuth flow manually**
  1. Open `https://<your-tailscale-name>.ts.net/api/oauth/google/start` in a browser
  2. Authenticate with Google and grant permissions
  3. Verify you are redirected back and see a success message

- [ ] **OAuth status shows connected after bootstrap**
  ```bash
  curl -s https://<your-tailscale-name>.ts.net/api/oauth/status | jq .google
  # Expected: {"state": "connected", "connected": true, "scopes_granted": [...]}
  ```

- [ ] **Credentials persist across restarts**
  ```bash
  # Restart the backend
  # Then check status again — should still show "connected"
  curl -s https://<your-tailscale-name>.ts.net/api/oauth/status | jq .google.state
  # Expected: "connected"
  ```

- [ ] **Startup guard passes for Gmail connector**
  ```bash
  # In a new terminal, source credentials and run the guard directly:
  GOOGLE_REFRESH_TOKEN=<your-token> uv run python -c "
  from butlers.startup_guard import require_google_credentials_or_exit
  require_google_credentials_or_exit(caller='test')
  print('Guard passed — credentials are present')
  "
  # Expected: "Guard passed — credentials are present"
  ```

- [ ] **Gmail connector starts without credential error**
  ```bash
  # Check the connector pane in tmux for absence of STARTUP BLOCKED error
  # Expected: no "STARTUP BLOCKED" banner; connector begins polling
  ```

- [ ] **dev.sh pre-flight passes**
  ```bash
  ./dev.sh --skip-oauth-check  # Only if needed for debugging
  # Without --skip-oauth-check, the script should show no WARNING banner
  ```

---

### 0.2: Production Verification Checklist

Prerequisites:
- Production domain with valid HTTPS certificate
- Google Cloud Console redirect URI includes the production URL
- `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`, `GOOGLE_OAUTH_REDIRECT_URI` set

#### Checklist

- [ ] **Backend health check passes**
  ```bash
  curl -s https://<prod-domain>/api/health | jq .status
  # Expected: "ok"
  ```

- [ ] **OAuth status endpoint returns 200**
  ```bash
  curl -s -o /dev/null -w "%{http_code}" https://<prod-domain>/api/oauth/status
  # Expected: 200
  ```

- [ ] **Status reflects current state accurately**
  ```bash
  curl -s https://<prod-domain>/api/oauth/status | jq .google
  # Expected: either {"state": "connected", "connected": true, ...}
  # or {"state": "not_configured", "connected": false, ...}
  ```

- [ ] **OAuth start endpoint is reachable**
  ```bash
  curl -s "https://<prod-domain>/api/oauth/google/start?redirect=false" | jq .authorization_url
  # Expected: a Google authorization URL (not a 503 error)
  ```

- [ ] **Callback URI is registered in Google Cloud Console**
  - Navigate to Google Cloud Console → APIs & Services → Credentials
  - Confirm `https://<prod-domain>/api/oauth/google/callback` is listed as an authorized redirect URI

- [ ] **Run the OAuth flow end-to-end in a browser**
  1. Open `https://<prod-domain>/api/oauth/google/start`
  2. Complete authentication with Google
  3. Verify redirect back to dashboard succeeds
  4. Confirm status shows `connected`

- [ ] **Credentials are stored in DB (not only in env vars)**
  ```bash
  # Connect to the production DB and check the oauth credentials table:
  psql $PG_DSN -c "SELECT credential_key, credentials->>'client_id', updated_at FROM google_oauth_credentials;"
  # Expected: one row with credential_key='google' and a recent updated_at
  ```

- [ ] **Scopes include both Gmail and Calendar**
  ```bash
  curl -s https://<prod-domain>/api/oauth/status | jq '.google.scopes_granted[]'
  # Expected: includes both gmail and calendar scopes
  ```

- [ ] **Re-bootstrap does not break existing credentials**
  - Run the OAuth flow again (re-authenticate)
  - Verify that the status still shows `connected` and scopes are unchanged

- [ ] **Startup guard logs for connectors show no errors**
  ```bash
  # Check production logs for absence of "STARTUP BLOCKED"
  # and presence of "Google credentials resolved" or similar OK indicator
  journalctl -u butlers-gmail-connector --no-pager | grep -E "STARTUP|credential|oauth" | tail -20
  ```

---

### 0.3: Verification Failure Triage

| Symptom | Likely cause | Action |
|---------|-------------|--------|
| `GET /api/oauth/google/start` returns 503 | `GOOGLE_OAUTH_CLIENT_ID` not set | Set env var and restart |
| `GET /api/oauth/status` shows `not_configured` | No credentials bootstrapped | Run OAuth flow via dashboard |
| OAuth callback returns `invalid_state` | State token expired (>10 min) | Re-initiate from `/start` |
| OAuth callback returns `no_refresh_token` | Access type not `offline` | Check `prompt=consent` in start URL |
| Gmail connector shows `STARTUP BLOCKED` | Startup guard failed | Check that env vars or DB credentials are set |
| Status shows `expired` | Refresh token revoked or inactive >6 months | Re-run OAuth flow |
| Status shows `missing_scope` | Token lacks required scopes | Re-run OAuth with all scopes |
| Status shows `redirect_uri_mismatch` | Redirect URI not in Google Console | Add URI to Google Cloud Console |
| `psql` shows no row in `google_oauth_credentials` | OAuth flow never completed (DB store path) | Run callback flow with DB manager wired |

---

---

## Section 1: Daily Operations

### 1.1: Verifying OAuth Status

Check that Butlers has valid Google OAuth credentials:

```bash
# Check if environment variable is set
env | grep BUTLER_GOOGLE_CALENDAR_CREDENTIALS_JSON

# If using .env file, verify it's sourced
cat .env | grep BUTLER_GOOGLE_CALENDAR_CREDENTIALS_JSON
```

### 1.2: Testing Token Validity

Start Butlers and check for authentication errors in logs:

```bash
uv run src/butlers/main.py --config-path roster/sample-butler
```

Look for these success indicators:
- Calendar module initializes without errors
- No `CalendarTokenRefreshError` or `CalendarCredentialError` in logs

### 1.3: Monitoring Token Health

The calendar module logs all OAuth operations:

```
[INFO] calendar: Initialized calendar module with Google provider
[INFO] calendar: Refreshed access token (valid for 3599 seconds)
[INFO] calendar: Calendar API request succeeded (200 OK)
```

If you see:
```
[ERROR] calendar: Failed to refresh access token: <error>
```

See **Section 3: Troubleshooting**.

---

## Section 2: Credential Rotation and Updates

### 2.1: Planned Credential Rotation (No Downtime)

**Scenario**: You want to rotate OAuth credentials proactively (best practice every 6 months).

**Steps**:

1. **Obtain new refresh token** (see setup-guide.md Part 2)

2. **Create new credential JSON**:
   ```bash
   # On a development machine
   export BUTLER_GOOGLE_CALENDAR_CREDENTIALS_JSON='{"client_id":"<NEW_CLIENT_ID>","client_secret":"<NEW_CLIENT_SECRET>","refresh_token":"<NEW_REFRESH_TOKEN>"}'
   ```

3. **Update environment** (method depends on deployment):
   
   **For local .env**:
   ```bash
   # Update .env file
   sed -i 's/"refresh_token":"[^"]*"/"refresh_token":"<NEW_REFRESH_TOKEN>"/g' .env
   source .env
   ```

   **For Kubernetes**:
   ```bash
   kubectl create secret generic google-oauth-new \
     --from-literal=credentials.json='{"client_id":"...","client_secret":"...","refresh_token":"..."}'
   kubectl patch deployment butlers -p '{"spec":{"template":{"spec":{"volumes":[{"name":"google-oauth","secret":{"secretName":"google-oauth-new"}}]}}}}'
   ```

   **For Docker Compose**:
   ```bash
   # Update the secret in docker-compose.yml or via environment variable
   docker-compose up -d butlers
   ```

4. **Verify** no errors in logs after deployment:
   ```bash
   kubectl logs -f deployment/butlers | grep -i "calendar\|oauth\|credential"
   ```

5. **Delete old secret** (once verified new one works):
   ```bash
   kubectl delete secret google-oauth
   ```

### 2.2: Emergency Credential Replacement (Token Compromised)

**Scenario**: OAuth credentials are compromised and must be replaced immediately.

**Steps**:

1. **Revoke the compromised token immediately**:
   ```bash
   # Go to Google Account Security
   # https://myaccount.google.com/security -> "Your apps and sites"
   # Find "Butlers Dev" and click "Remove access"
   ```

2. **Obtain a new refresh token** (setup-guide.md Part 2)

3. **Update all environments** (prod, staging, dev):
   ```bash
   # Update in secret manager, Kubernetes, Docker, or env var
   export BUTLER_GOOGLE_CALENDAR_CREDENTIALS_JSON='{"client_id":"<CLIENT_ID>","client_secret":"<NEW_SECRET>","refresh_token":"<NEW_REFRESH_TOKEN>"}'
   ```

4. **Restart all Butlers instances**:
   ```bash
   # Kubernetes
   kubectl rollout restart deployment/butlers
   
   # Docker Compose
   docker-compose restart butlers
   
   # Local
   # Kill the process and restart
   ```

5. **Verify** credentials work:
   ```bash
   # Check logs for successful token refresh
   kubectl logs -f deployment/butlers -c butlers | grep "Refreshed access token"
   ```

6. **Audit**: Review which services/systems had access with the old token

---

## Section 3: Troubleshooting

### 3.1: Error: `CalendarCredentialError: ... must be set to a non-empty JSON object`

**Cause**: `BUTLER_GOOGLE_CALENDAR_CREDENTIALS_JSON` is not set or is empty.

**Steps to fix**:

1. **Check if env var is set**:
   ```bash
   echo $BUTLER_GOOGLE_CALENDAR_CREDENTIALS_JSON
   ```

   If empty:

2. **Set it** (if using .env):
   ```bash
   export BUTLER_GOOGLE_CALENDAR_CREDENTIALS_JSON='{"client_id":"...","client_secret":"...","refresh_token":"..."}'
   ```

3. **Verify it's valid JSON**:
   ```bash
   echo $BUTLER_GOOGLE_CALENDAR_CREDENTIALS_JSON | jq .
   ```

4. **If using Kubernetes**:
   ```bash
   kubectl get secret google-oauth -o yaml
   # Check that the data.credentials.json field exists and contains valid JSON
   ```

### 3.2: Error: `CalendarCredentialError: ... is missing required field(s): [...]`

**Cause**: The JSON is valid but missing one or more of: `client_id`, `client_secret`, `refresh_token`.

**Steps to fix**:

1. **Print the current credential** (redacted):
   ```bash
   echo $BUTLER_GOOGLE_CALENDAR_CREDENTIALS_JSON | jq 'keys'
   # Should show: ["client_id", "client_secret", "refresh_token"]
   ```

2. **If any field is missing**, obtain a new credential:
   - Get new client ID/secret from Google Cloud Console (setup-guide.md Step 1.4)
   - Get new refresh token (setup-guide.md Part 2)
   - Reconstruct the full JSON object

3. **Update the env var** and restart Butlers

### 3.3: Error: `CalendarTokenRefreshError: Failed to refresh access token`

**Cause**: The refresh token is invalid, expired, or doesn't have the required scopes.

**Symptoms**:
- Butlers starts but fails on first calendar API call
- Log shows: `[ERROR] calendar: Failed to refresh access token: ...`

**Steps to fix**:

1. **Check if the refresh token was revoked**:
   - Go to [Google Account Security](https://myaccount.google.com/security)
   - Click **Your apps and sites**
   - Check if "Butlers Dev" is still listed

   If missing, the token was revoked. Get a new one.

2. **If it hasn't been revoked**, the token may be expired or have wrong scopes.

3. **Obtain a fresh refresh token** (setup-guide.md Part 2) with the correct scopes:
   ```
   https://www.googleapis.com/auth/gmail.readonly
   https://www.googleapis.com/auth/gmail.modify
   https://www.googleapis.com/auth/calendar
   ```

4. **Update the env var**:
   ```bash
   export BUTLER_GOOGLE_CALENDAR_CREDENTIALS_JSON='{"client_id":"<CLIENT_ID>","client_secret":"<SECRET>","refresh_token":"<NEW_REFRESH_TOKEN>"}'
   ```

5. **Restart Butlers** and verify logs show successful token refresh

### 3.4: Error: `CalendarRequestError: ... API request failed (401): Invalid Credentials`

**Cause**: The access token is invalid or the refresh token couldn't be used to get a new one.

**Steps to fix**:

1. **Check that the current access token didn't expire unexpectedly**:
   - Restart Butlers (forces a fresh token refresh)
   - Try the same operation again

2. **If the error persists**, the refresh token is likely compromised:
   - Revoke it (Google Account Security)
   - Obtain a new refresh token (setup-guide.md Part 2)
   - Update env var and restart

### 3.5: Error: `CalendarRequestError: ... API request failed (403): Insufficient permissions`

**Cause**: The OAuth scopes don't include the permission needed for the operation.

**Common scenarios**:
- Using `gmail.readonly` scope but trying to modify a label (requires `gmail.modify`)
- Using `calendar.readonly` scope but trying to create an event (requires `calendar`)

**Steps to fix**:

1. **Identify which operation failed** (check logs for the API endpoint):
   ```
   POST https://www.googleapis.com/calendar/v3/calendars/primary/events
   403 Forbidden: Insufficient permissions
   ```

2. **Check the required scope** (setup-guide.md Part 4)

3. **Obtain a refresh token with the correct scope**:
   - Use Google OAuth Playground or the Python script (setup-guide.md Part 2)
   - Include all required scopes

4. **Update env var** and restart

### 3.6: Access Token Cache Exhaustion

**Scenario**: You see many token refresh calls in logs (every API request instead of once per hour).

**Cause**: Access token is not being cached properly (likely a memory issue or cache was cleared).

**Steps to diagnose**:

```bash
# Check logs for token refresh frequency
kubectl logs deployment/butlers | grep "Refreshed access token" | wc -l

# If count is very high (more than once per hour sustained), investigate memory
kubectl top node
kubectl top pod <pod-name>
```

**Steps to fix**:

1. **Verify memory is sufficient** (calendar module needs ~100MB for token cache)

2. **Check if there are multiple Butlers instances** (each maintains its own cache):
   ```bash
   kubectl get pods -l app=butlers
   # If more than 1, each will refresh independently (this is normal)
   ```

3. **If a single pod is refreshing tokens excessively**, restart it:
   ```bash
   kubectl delete pod <pod-name>
   ```

---

## Section 4: Token Expiration and Lifecycle

### 4.1: Access Token Expiration (Normal)

**Facts**:
- Access tokens are valid for ~1 hour
- Butlers automatically refreshes before expiration
- No manual action needed

**What you might see**:
```
[INFO] calendar: Refreshed access token (valid for 3599 seconds)
```

This is normal and expected. The module handles the entire refresh automatically.

### 4.2: Refresh Token Expiration (Rare, After 6 Months)

**Facts**:
- Refresh tokens expire if unused for 6 months
- Google may invalidate them after 6 months of inactivity

**How to prevent**:
- Run Butlers regularly (at least once per 6 months)
- Set up a heartbeat task to periodically access Google Calendar

**If it expires anyway**:
1. Obtain a new refresh token (setup-guide.md Part 2)
2. Update env var
3. Restart Butlers

### 4.3: Monitoring Token Expiration

To track token health, you can query Butlers logs:

```bash
# Check when token was last refreshed
kubectl logs deployment/butlers | grep "Refreshed access token" | tail -1

# Expected output:
# [2025-02-19T10:30:45Z] [INFO] calendar: Refreshed access token (valid for 3599 seconds)
```

If the timestamp is more than 1 hour old, the calendar module may not have made any API calls recently (which is fine, the token will be refreshed on next use).

---

## Section 5: Scope Management

### 5.1: What Scopes Are Needed?

| Feature | Scope | Why |
|---------|-------|-----|
| Read emails | `gmail.readonly` | Required for inbox integration |
| Organize emails (labels, mark spam) | `gmail.modify` | Required for email management |
| Read calendar | `calendar.readonly` | Required for calendar sync |
| Create/edit/delete events | `calendar` | Required for event management |

**Recommended minimum for Butlers**:
```
https://www.googleapis.com/auth/gmail.modify
https://www.googleapis.com/auth/calendar
```

### 5.2: Adding a New Scope

**Scenario**: You want to add email-sending capability to Butlers.

**Steps**:

1. **Obtain a new refresh token with the additional scope** (setup-guide.md Part 2):
   ```
   https://www.googleapis.com/auth/gmail.readonly
   https://www.googleapis.com/auth/gmail.modify
   https://www.googleapis.com/auth/gmail.send       <-- NEW
   https://www.googleapis.com/auth/calendar
   ```

2. **Update the OAuth consent screen** (setup-guide.md Step 1.3) to list the new scope

3. **Re-authenticate** to grant the new permission

4. **Update env var** with the new refresh token

5. **Restart Butlers**

### 5.3: Removing a Scope

**Scenario**: You want to reduce permissions (e.g., use read-only calendar).

**Steps**:

1. **Obtain a new refresh token with fewer scopes** (setup-guide.md Part 2):
   ```
   https://www.googleapis.com/auth/gmail.modify
   https://www.googleapis.com/auth/calendar.readonly    <-- Changed from 'calendar'
   ```

2. **Update env var** with the new refresh token

3. **Restart Butlers**

4. **Verify** that event creation now fails with a 403 (if you try it):
   ```
   CalendarRequestError: ... API request failed (403): Insufficient permissions
   ```

---

## Section 6: Multi-Account Setup

### 6.1: Single Butlers Instance, Multiple Gmail Accounts

**Scenario**: You want one Butlers instance to manage email from multiple accounts.

**Current Limitation**: Each Butlers instance supports only one `BUTLER_GOOGLE_CALENDAR_CREDENTIALS_JSON`.

**Workaround**: Deploy multiple Butlers instances, one per account:

```bash
# Instance 1: Alice's account
BUTLER_NAME=butler-alice \
BUTLER_GOOGLE_CALENDAR_CREDENTIALS_JSON='{"client_id":"...","client_secret":"...","refresh_token":"<ALICE_REFRESH_TOKEN>"}' \
uv run src/butlers/main.py --config-path roster/sample-butler

# Instance 2: Bob's account
BUTLER_NAME=butler-bob \
BUTLER_GOOGLE_CALENDAR_CREDENTIALS_JSON='{"client_id":"...","client_secret":"...","refresh_token":"<BOB_REFRESH_TOKEN>"}' \
uv run src/butlers/main.py --config-path roster/sample-butler
```

Each instance maintains its own database and token state.

### 6.2: Shared OAuth Client Across Deployments

**Scenario**: You have prod, staging, and dev environments, all using the same OAuth client.

**Setup**:

1. **Create a single OAuth client** in Google Cloud Console (setup-guide.md Step 1.4)

2. **Register all redirect URIs** in that client:
   ```
   https://prod.example.com/oauth/google/callback
   https://staging.example.com/oauth/google/callback
   https://dev.example.com/oauth/google/callback
   ```

3. **Obtain separate refresh tokens** for each environment (setup-guide.md Part 2)

4. **Deploy each environment** with its own refresh token:
   ```bash
   # Prod
   BUTLER_GOOGLE_CALENDAR_CREDENTIALS_JSON='{"client_id":"<SHARED_CLIENT_ID>","client_secret":"<SHARED_SECRET>","refresh_token":"<PROD_REFRESH_TOKEN>"}' \
   kubectl set env deployment/butlers-prod ...
   
   # Staging
   BUTLER_GOOGLE_CALENDAR_CREDENTIALS_JSON='{"client_id":"<SHARED_CLIENT_ID>","client_secret":"<SHARED_SECRET>","refresh_token":"<STAGING_REFRESH_TOKEN>"}' \
   kubectl set env deployment/butlers-staging ...
   ```

---

## Section 7: Monitoring and Alerting

### 7.1: Key Metrics to Monitor

1. **Token Refresh Success Rate**:
   ```bash
   # Count successful refreshes
   kubectl logs deployment/butlers | grep "Refreshed access token" | wc -l
   
   # Count refresh failures
   kubectl logs deployment/butlers | grep "Failed to refresh" | wc -l
   ```

   **Alert**: If failures > 0 in a 24-hour window, investigate (Section 3).

2. **API Request Success Rate**:
   ```bash
   # Count successful API requests
   kubectl logs deployment/butlers | grep "Calendar API request succeeded" | wc -l
   
   # Count failed API requests
   kubectl logs deployment/butlers | grep "Calendar API request failed" | wc -l
   ```

   **Alert**: If failure rate > 1%, check token or scopes.

3. **Token Cache Hit Rate**:
   Look for logs that show token reuse without refresh:
   ```bash
   # A "Refreshed" log means the cache was expired
   # No "Refreshed" log for an hour means good cache hit rate
   ```

### 7.2: Setting Up Log Alerts

**Example: Datadog Alert**

```
service:butlers AND error:"CalendarTokenRefreshError"
```

Alert if this query returns > 0 results in a 15-minute window.

**Example: CloudWatch Alert (AWS)**

```
Logs Insights Query:
fields @timestamp, @message
| filter @message like /CalendarTokenRefreshError/
| stats count() as error_count
```

Trigger alarm if `error_count > 0`.

### 7.3: Audit Logging

To track OAuth token usage, enable audit logging at the Butlers application level:

```python
# In src/butlers/modules/calendar.py (future enhancement)
logger.info(
    "OAuth token refreshed",
    extra={
        "event_type": "oauth_token_refresh",
        "provider": "google",
        "access_token_expires_in": 3599,
        "timestamp": datetime.now(UTC).isoformat(),
    }
)
```

---

## Section 8: Disaster Recovery

### 8.1: Lost Refresh Token

**Scenario**: The only copy of your refresh token is deleted or lost.

**Recovery**:

1. **Obtain a new refresh token** (setup-guide.md Part 2) by re-authenticating
2. **Update env var** and restart Butlers
3. **Revoke the old token** (if you know what it was):
   ```bash
   # Google Account Security -> "Your apps and sites" -> "Butlers Dev" -> Remove access
   ```

### 8.2: Compromised Client Secret

**Scenario**: Your OAuth client secret is exposed in logs or committed to git.

**Recovery**:

1. **Immediately revoke the old client secret** in Google Cloud Console:
   ```
   APIs & Services > Credentials > [Your OAuth 2.0 Client] > Delete
   ```

2. **Create a new OAuth 2.0 client** (setup-guide.md Step 1.4):
   - New client ID
   - New client secret

3. **Obtain a new refresh token** with the new client credentials (setup-guide.md Part 2)

4. **Update all environments**:
   ```bash
   export BUTLER_GOOGLE_CALENDAR_CREDENTIALS_JSON='{"client_id":"<NEW_ID>","client_secret":"<NEW_SECRET>","refresh_token":"<NEW_TOKEN>"}'
   ```

5. **Restart Butlers**

6. **Audit**: Check logs to see if the old secret was used maliciously

### 8.3: Verification After Recovery

After any token/secret replacement, verify these checks:

```bash
# 1. Butlers starts without credential errors
uv run src/butlers/main.py --config-path roster/sample-butler

# Expected output (no errors):
# [INFO] calendar: Initialized calendar module with Google provider
# [INFO] calendar: Refreshed access token (valid for 3599 seconds)

# 2. Test a calendar operation (if API is available)
curl -H "Authorization: Bearer $(jq -r .access_token <<< $BUTLER_AUTH_TOKEN)" \
  https://www.googleapis.com/calendar/v3/calendars/primary/list

# 3. Check logs for any credential-related errors
kubectl logs deployment/butlers | grep -i "credential\|oauth\|error"
```

---

## Section 9: Migration Guide

### 9.1: Migrating OAuth Credentials Between Environments

**Scenario**: Moving Butlers from dev to production.

**Steps**:

1. **In dev**: Export the current credentials (if working)
   ```bash
   echo $BUTLER_GOOGLE_CALENDAR_CREDENTIALS_JSON
   ```

2. **In prod**: Set the same credentials
   ```bash
   export BUTLER_GOOGLE_CALENDAR_CREDENTIALS_JSON='<DEV_CREDENTIALS>'
   ```

3. **Update the OAuth redirect URI** in Google Cloud Console to prod domain:
   ```
   https://prod.example.com/oauth/google/callback
   ```

4. **Restart Butlers in prod** and verify logs

5. **Optional**: Obtain a separate refresh token for prod (best practice):
   - Logout from dev's OAuth app in Google Account
   - Re-authenticate from prod
   - Use the new refresh token in prod

---

## Quick Reference

| Issue | Solution | Docs |
|-------|----------|------|
| `CalendarCredentialError` | Set `BUTLER_GOOGLE_CALENDAR_CREDENTIALS_JSON` | 3.1 |
| `CalendarTokenRefreshError` | Get new refresh token | 3.3 |
| Token expired after 6 months | Get new refresh token, restart | 4.2 |
| Need new scope | Get new refresh token with scope | 5.2 |
| Multiple accounts | Deploy separate Butlers instances | 6.1 |
| Token compromised | Revoke in Google, get new token | 8.2 |

---

## Support and Escalation

For issues not covered in this runbook:

1. **Check setup-guide.md Part 4** for scope details
2. **Enable debug logging** in Butlers:
   ```bash
   export BUTLERS_LOG_LEVEL=DEBUG
   uv run src/butlers/main.py
   ```
3. **Review Google API documentation**: https://developers.google.com/identity/protocols/oauth2
4. **Contact your Butlers admin** if the issue persists
