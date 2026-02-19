# Google OAuth Setup Guide

## Overview

This guide covers end-to-end setup for Google OAuth 2.0 authentication with Butlers, enabling secure access to Gmail and Google Calendar APIs. It includes Google Cloud Console configuration, OAuth scope selection, and local development setup with Tailscale HTTPS.

## Prerequisites

- Google Cloud account (free tier is sufficient for development)
- Access to Google Cloud Console
- Local development environment with `butlers` repo
- (Optional) Tailscale account for local HTTPS testing

## Part 1: Google Cloud Console Setup

### Step 1.1: Create a Google Cloud Project

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Click the project dropdown at the top-left
3. Select **New Project**
4. Enter a project name (e.g., `butlers-oauth-dev`)
5. Click **Create**
6. Wait for the project to be created and automatically selected

### Step 1.2: Enable Required Google APIs

You must enable both Gmail API and Google Calendar API for Butlers to function with email and calendar integration.

#### Enable Gmail API

1. In Google Cloud Console, navigate to **APIs & Services > Library**
2. Search for **Gmail API**
3. Click on **Gmail API**
4. Click **Enable**
5. A notification confirms the API is now enabled

#### Enable Google Calendar API

1. In **APIs & Services > Library**, search for **Google Calendar API**
2. Click on **Google Calendar API**
3. Click **Enable**
4. A notification confirms the API is now enabled

### Step 1.3: Configure OAuth Consent Screen

1. Navigate to **APIs & Services > OAuth consent screen**
2. Under **User Type**, select **External** (for development)
3. Click **Create**
4. Fill in the OAuth consent screen form:
   - **App name**: `Butlers Dev`
   - **User support email**: Use your Gmail address
   - **Developer contact information**: Use your Gmail address
5. Click **Save and Continue**
6. On the **Scopes** step, click **Add or Remove Scopes**
7. Add the following scopes (search for each and add):
   - `https://www.googleapis.com/auth/gmail.readonly` — Read Gmail messages
   - `https://www.googleapis.com/auth/gmail.modify` — Modify Gmail labels and mark messages
   - `https://www.googleapis.com/auth/calendar` — Full Google Calendar access
   - `https://www.googleapis.com/auth/calendar.readonly` — Read-only Google Calendar access
8. Click **Update** and then **Save and Continue**
9. On the **Test users** step, click **Add Users**
10. Add your Gmail address as a test user
11. Click **Save and Continue**
12. Review the summary and click **Back to Dashboard**

### Step 1.4: Create OAuth 2.0 Credentials

1. Navigate to **APIs & Services > Credentials**
2. Click **Create Credentials > OAuth client ID**
3. If prompted, first complete the OAuth consent screen setup (see above)
4. Select **Web application** as the application type
5. Under **Authorized redirect URIs**, add the following:
   - **For local development (Tailscale)**: `https://<your-tailscale-domain>:8000/oauth/google/callback`
   - **For localhost testing (if no Tailscale)**: `http://localhost:8000/oauth/google/callback`
   - **For production (if deployed)**: `https://<your-production-domain>:8000/oauth/google/callback`

   > **IMPORTANT**: Google requires HTTPS for redirect URIs, except for localhost (127.0.0.1). Tailscale Funnel provides a way to serve HTTPS over localhost via `tailscale serve`.

6. Click **Create**
7. A modal displays your **Client ID** and **Client Secret**
8. Click **Download JSON** to save credentials, or copy the values manually

### Step 1.5: Store Credentials Securely

**NEVER commit OAuth credentials to git.** Credentials must be managed via environment variables or a secret manager.

For **local development**, use the dashboard OAuth flow (recommended) or set the
client ID and secret env vars for the OAuth bootstrap:

```bash
# .env (add to .gitignore)
# App config for OAuth bootstrap (always required):
GOOGLE_OAUTH_CLIENT_ID=<YOUR_CLIENT_ID>
GOOGLE_OAUTH_CLIENT_SECRET=<YOUR_CLIENT_SECRET>

# After completing the OAuth flow via the dashboard, the refresh token is stored
# in the butler's database automatically. No env var needed for the refresh token.
```

> **Legacy (deprecated):** `BUTLER_GOOGLE_CALENDAR_CREDENTIALS_JSON` is deprecated and
> will be removed in a future release. Use the dashboard OAuth flow to store credentials
> in the database, or set `GOOGLE_OAUTH_CLIENT_ID` / `GOOGLE_OAUTH_CLIENT_SECRET` and
> complete the dashboard OAuth flow to obtain the refresh token.

For **production**, use:
- Kubernetes secrets (if deployed)
- AWS Secrets Manager
- GCP Secret Manager
- Passwordless environment variable injection via deployment platform

---

## Part 2: Obtaining Refresh Tokens

A **refresh token** is a long-lived token that allows Butlers to obtain new access tokens without user re-authentication. You must obtain it once during the initial OAuth flow.

### Step 2.1: Get Initial Refresh Token (Interactive Flow)

You can use Google's OAuth Playground or a custom script to obtain the refresh token.

#### Option A: Using Google OAuth Playground (Simplest)

1. Go to [Google OAuth 2.0 Playground](https://developers.google.com/oauthplayground/)
2. Click the settings icon (top-right)
3. Check **Use your own OAuth credentials**
4. Enter your Client ID and Client Secret from Step 1.4
5. Click **Close**
6. On the left, under **Select the OAuth 2.0 scopes**, enter:
   ```
   https://www.googleapis.com/auth/gmail.readonly
   https://www.googleapis.com/auth/gmail.modify
   https://www.googleapis.com/auth/calendar
   ```
7. Click **Authorize the APIs**
8. Select your test Gmail account
9. Grant permission when prompted
10. Click **Exchange authorization code for tokens**
11. Copy the **Refresh Token** from the response
12. Save this refresh token securely

#### Option B: Using a Python Script

Create a temporary script to get the refresh token:

```python
#!/usr/bin/env python3
"""Get Google OAuth refresh token for Butlers."""

import json
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/calendar",
]

# Load credentials from the JSON file downloaded in Step 1.4
flow = InstalledAppFlow.from_client_secrets_file(
    "credentials.json",  # The JSON file from Step 1.4
    SCOPES,
)
creds = flow.run_local_server(port=8000)

# Print the credentials
print(json.dumps({
    "client_id": creds.client_id,
    "client_secret": creds.client_secret,
    "refresh_token": creds.refresh_token,
}, indent=2))
```

Run:
```bash
pip install google-auth-oauthlib google-auth-httplib2 google-api-python-client
python get_refresh_token.py
```

This opens a browser for authentication and prints the full credential JSON.

### Step 2.2: Verify Refresh Token Works

Once you have the refresh token, use the dashboard to store it in the database.
The dashboard OAuth flow (`/api/oauth/google/start`) handles this automatically.

Alternatively, for manual testing, store credentials via the API:

```bash
# Store credentials via the OAuth callback (handled automatically by the dashboard)
# Or test directly by verifying the calendar module starts without errors:

GOOGLE_OAUTH_CLIENT_ID=<YOUR_CLIENT_ID> \
GOOGLE_OAUTH_CLIENT_SECRET=<YOUR_CLIENT_SECRET> \
DATABASE_URL=postgres://butlers:butlers@localhost:5432/butlers \
uv run src/butlers/main.py --config-path roster/sample-butler
```

If the credential is valid, the calendar module initializes without errors.

> **Deprecated:** The `BUTLER_GOOGLE_CALENDAR_CREDENTIALS_JSON` env var is deprecated.
> Use the dashboard OAuth flow and DB-stored credentials instead.

---

## Part 3: Local HTTPS Setup with Tailscale Serve

For local development, Google requires HTTPS redirect URIs (except localhost). **Tailscale Serve** provides an easy way to expose your local Butlers API over HTTPS.

### Step 3.1: Install and Authenticate Tailscale

1. Download and install [Tailscale](https://tailscale.com/download/)
2. Run `tailscale up` to authenticate with your account
3. Verify connection: `tailscale status`

### Step 3.2: Start Butlers on Local Port

In one terminal, start Butlers with the API enabled on port 8000:

```bash
cd /home/tze/GitHub/butlers
uv run src/butlers/main.py --config-path roster/sample-butler
```

The API listens on `http://localhost:8000`.

### Step 3.3: Set Up Tailscale Serve for HTTPS

In another terminal, expose the local API via Tailscale:

```bash
# Serve local port 8000 via Tailscale
tailscale serve https:443 http://localhost:8000
```

This provides a stable HTTPS URL like:

```
https://<your-tailscale-name>.ts.net/
```

You can check the exact URL via:

```bash
tailscale serve status
```

### Step 3.4: Update OAuth Redirect URI

Update your Google Cloud Console credentials (Step 1.4) to include the Tailscale HTTPS URL:

```
https://<your-tailscale-name>.ts.net/oauth/google/callback
```

### Step 3.5: Test OAuth Flow

1. Access your Butlers API via the Tailscale HTTPS URL:
   ```
   https://<your-tailscale-name>.ts.net/auth/login
   ```

2. You should see an OAuth login button

3. Click to authenticate with Google

4. Grant requested permissions

5. You should be redirected back to the app with an active session

---

## Part 4: Supported Google APIs and Scopes

### Gmail API

**Required scopes for email integration:**

| Scope | Purpose | Read-Only |
|-------|---------|-----------|
| `https://www.googleapis.com/auth/gmail.readonly` | Read messages and attachments | Yes |
| `https://www.googleapis.com/auth/gmail.modify` | Read, modify labels, mark spam/unread | No |
| `https://www.googleapis.com/auth/gmail.send` | Send emails (not yet used by Butlers) | No |

**Recommended for Butlers**: `gmail.modify` (enables inbox management beyond read-only)

### Google Calendar API

**Required scopes for calendar integration:**

| Scope | Purpose | Read-Only |
|-------|---------|-----------|
| `https://www.googleapis.com/auth/calendar.readonly` | Read-only calendar access | Yes |
| `https://www.googleapis.com/auth/calendar` | Full calendar management (create, edit, delete events) | No |

**Recommended for Butlers**: `calendar` (enables full event management)

### Why These Scopes?

- **Gmail**: The modify scope allows Butlers to label and organize emails, not just read them
- **Calendar**: Full scope enables Butlers to create, reschedule, and manage events autonomously

These are the **minimum scopes** for meaningful Butlers functionality. Use the read-only versions only if your use case is audit/monitoring (no event creation).

---

## Part 5: OAuth Token Lifecycle and Refresh

### Access Tokens vs. Refresh Tokens

- **Access Token**: Short-lived (typically 1 hour), grants API access. Expires and must be refreshed.
- **Refresh Token**: Long-lived (typically 6 months or indefinite), used to obtain new access tokens. **Never expires** unless revoked or 6 months of inactivity passes.

### How Butlers Handles Token Refresh

The calendar module automatically:

1. **Stores** the refresh token securely in `BUTLER_GOOGLE_CALENDAR_CREDENTIALS_JSON`
2. **Exchanges** the refresh token for a new access token when needed (before expiration)
3. **Caches** the access token in memory to avoid redundant refresh calls
4. **Detects** when the refresh token is invalid and raises `CalendarTokenRefreshError`

**No manual refresh is required.** The module handles the entire lifecycle.

### Token Refresh Flow

```
┌─────────────────────────────────────────┐
│ Butlers Calendar Module Starts          │
├─────────────────────────────────────────┤
│ 1. Load refresh token from env var      │
│ 2. Exchange for access token            │
│ 3. Cache access token (1 hour)          │
└─────────────────────────────────────────┘
        ↓
┌─────────────────────────────────────────┐
│ Make Calendar API Request               │
├─────────────────────────────────────────┤
│ 1. Check if access token cached & valid │
│ 2. If expired, exchange refresh token   │
│ 3. Send request with access token       │
└─────────────────────────────────────────┘
        ↓
┌─────────────────────────────────────────┐
│ If Refresh Fails                        │
├─────────────────────────────────────────┤
│ 1. Log CalendarTokenRefreshError        │
│ 2. Operator must get new refresh token  │
│ 3. Update env var                       │
│ 4. Restart Butlers                      │
└─────────────────────────────────────────┘
```

### Revoking a Refresh Token

If a refresh token is compromised, revoke it immediately:

1. Go to [Google Account Security](https://myaccount.google.com/security)
2. Click **Your apps and sites**
3. Find **Butlers Dev** and select it
4. Click **Remove access**
5. Generate a new refresh token (Part 2)
6. Update `BUTLER_GOOGLE_CALENDAR_CREDENTIALS_JSON` with the new token

---

## Part 6: Secret Storage Requirements

### Development Environment

**Use a `.env` file** (add to `.gitignore`):

```bash
# .env
BUTLER_GOOGLE_CALENDAR_CREDENTIALS_JSON='{"client_id":"...","client_secret":"...","refresh_token":"..."}'
```

Load via:
```bash
source .env
uv run src/butlers/main.py
```

### Production Deployment

**Never use `.env` files in production.** Use one of:

#### Option A: Docker Secrets (Docker Compose / Swarm)

```yaml
services:
  butler:
    image: butlers:latest
    secrets:
      - google_oauth_creds
    environment:
      BUTLER_GOOGLE_CALENDAR_CREDENTIALS_JSON_FILE: /run/secrets/google_oauth_creds
```

#### Option B: Kubernetes Secrets

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: google-oauth
type: Opaque
stringData:
  credentials.json: '{"client_id":"...","client_secret":"...","refresh_token":"..."}'
---
apiVersion: v1
kind: Pod
metadata:
  name: butler
spec:
  containers:
  - name: butler
    env:
    - name: BUTLER_GOOGLE_CALENDAR_CREDENTIALS_JSON
      valueFrom:
        secretKeyRef:
          name: google-oauth
          key: credentials.json
```

#### Option C: Cloud Provider Secret Manager

**AWS Secrets Manager:**
```bash
aws secretsmanager create-secret \
  --name butlers/google-oauth \
  --secret-string '{"client_id":"...","client_secret":"...","refresh_token":"..."}'
```

**GCP Secret Manager:**
```bash
echo '{"client_id":"...","client_secret":"...","refresh_token":"..."}' | \
gcloud secrets create butlers-google-oauth --data-file=-
```

#### Option D: HashiCorp Vault

Store credentials in Vault and retrieve at runtime:
```python
import hvac

client = hvac.Client(url='https://vault.example.com')
secret = client.secrets.kv.v2.read_secret_version(path='butlers/google-oauth')
credentials = secret['data']['data']
```

### Best Practices

1. **Never log credentials** — Redact from logs automatically
2. **Use least privilege** — Rotate tokens if compromised
3. **Audit access** — Log all OAuth token usage
4. **Separate by environment** — Dev, staging, and prod credentials must be distinct
5. **Avoid hardcoding** — Always use environment variables or secret managers

---

## Troubleshooting

### Issue: "Invalid Redirect URI"

**Cause**: The redirect URI in your OAuth request doesn't match what's registered in Google Cloud Console.

**Fix**:
1. Check the exact URL you're accessing (with Tailscale, it should be `https://<your-tailscale-name>.ts.net/...`)
2. Update **APIs & Services > Credentials** to include that exact URI
3. Ensure HTTPS (not HTTP) unless using localhost

### Issue: "Invalid Client Credentials"

**Cause**: Client ID, client secret, or refresh token is incorrect or expired.

**Fix**:
1. Verify `BUTLER_GOOGLE_CALENDAR_CREDENTIALS_JSON` is set and valid JSON
2. Re-obtain a fresh refresh token (Part 2)
3. Check that the client ID and secret match what's in Google Cloud Console

### Issue: Refresh Token Expires After 6 Months

**Cause**: Google invalidates unused refresh tokens after 6 months of inactivity.

**Fix**:
1. Ensure Butlers is running regularly (at least once every 6 months)
2. If it expires, obtain a fresh refresh token (Part 2)
3. Update the env var and restart

### Issue: "Insufficient Permissions"

**Cause**: The scopes granted don't include required permissions.

**Fix**:
1. Check the scopes in your refresh token match the APIs you're calling
2. Obtain a new refresh token with the correct scopes (Part 2)
3. For existing tokens, revoke and regenerate (Part 5)

---

## Summary Checklist

- [ ] Google Cloud Project created
- [ ] Gmail API enabled
- [ ] Google Calendar API enabled
- [ ] OAuth consent screen configured with test user
- [ ] OAuth credentials created (Client ID + Secret)
- [ ] Refresh token obtained and stored in `.env`
- [ ] Tailscale Serve configured for local HTTPS (optional but recommended)
- [ ] OAuth redirect URIs updated to match your domain
- [ ] Butlers started with `BUTLER_GOOGLE_CALENDAR_CREDENTIALS_JSON` set
- [ ] Initial OAuth login tested successfully

Once complete, Butlers can access Gmail and Google Calendar on behalf of your account.
