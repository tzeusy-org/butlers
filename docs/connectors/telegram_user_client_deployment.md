# Telegram User-Client Connector Deployment Guide

Status: Draft (v2-only feature)
Last updated: 2026-02-15

## Overview

This guide provides deployment instructions for the Telegram user-client connector, which ingests message activity visible to a user's Telegram account into the butler ecosystem.

**IMPORTANT PRIVACY NOTICE:**
- This connector ingests personal account traffic and requires explicit user consent
- Clear scope disclosure and privacy safeguards must be in place before deployment
- Review `docs/connectors/telegram_user_client.md` for full privacy requirements

## Prerequisites

### 1. Telegram API Credentials

You need to obtain API credentials from Telegram:

1. Visit https://my.telegram.org
2. Log in with your phone number
3. Navigate to "API development tools"
4. Create a new application to get:
   - `api_id` (numeric)
   - `api_hash` (string)

### 2. User Session String

Generate a session string using Telethon:

```python
from telethon.sync import TelegramClient
from telethon.sessions import StringSession

api_id = 12345  # Your API ID
api_hash = "your-api-hash"

with TelegramClient(StringSession(), api_id, api_hash) as client:
    print("Session string:", client.session.save())
```

**Security:** This session string grants full access to your Telegram account. Store it securely in a secret manager (AWS Secrets Manager, Vault, etc.). Never commit to version control.

### 3. Python Environment

Install the connector with Telethon support:

```bash
# In your butlers environment
uv pip install telethon>=1.36.0

# Or install with optional dependencies
uv sync --extra connectors
```

## Configuration

### Environment Variables

Required variables:

```bash
# Switchboard MCP server connection
export SWITCHBOARD_MCP_URL="http://switchboard:40100/sse"  # Use http://localhost:40100/sse for local dev

# Connector identity
export CONNECTOR_PROVIDER="telegram"
export CONNECTOR_CHANNEL="telegram"
export CONNECTOR_ENDPOINT_IDENTITY="telegram:user:YOUR_USER_ID"

# Telegram user-client credentials are managed via owner contact_info
# (types: telegram_api_id, telegram_api_hash, telegram_user_session).
# Configure them through the dashboard, not environment variables.

# Checkpoint/state
export CONNECTOR_CURSOR_PATH="/var/lib/butlers/connectors/telegram-user-client/cursor.json"
export CONNECTOR_MAX_INFLIGHT="8"
```

Optional variables:

```bash
# Backfill recent messages on first startup (hours)
export CONNECTOR_BACKFILL_WINDOW_H="24"
```

### Feature Gating

This connector is currently a **v2-only draft feature**. To enable:

1. Set explicit feature flag in your deployment config
2. Ensure privacy/consent flow is implemented
3. Configure chat/sender allow/deny lists (future enhancement)
4. Enable audit logging for connector lifecycle events

## Deployment

### Systemd Service (Linux)

**SECURITY WARNING:** The example below uses `EnvironmentFile` for simplicity, but this stores credentials in plaintext on disk. For production deployments, use a secret manager (AWS Secrets Manager, Vault, etc.) with a wrapper script that loads secrets at runtime. See the Docker example for a more secure pattern using Docker secrets.

Create `/etc/systemd/system/telegram-user-client-connector.service`:

```ini
[Unit]
Description=Telegram User-Client Connector
After=network.target switchboard.service
Requires=switchboard.service

[Service]
Type=simple
User=butlers
Group=butlers
WorkingDirectory=/opt/butlers
EnvironmentFile=/etc/butlers/connectors/telegram-user-client.env
ExecStart=/opt/butlers/.venv/bin/telegram-user-client-connector
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

# Security hardening
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/butlers/connectors/telegram-user-client

[Install]
WantedBy=multi-user.target
```

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable telegram-user-client-connector
sudo systemctl start telegram-user-client-connector
sudo systemctl status telegram-user-client-connector
```

### Docker Deployment

Create `docker-compose.yml`:

```yaml
version: '3.8'

services:
  telegram-user-client-connector:
    image: butlers:latest
    command: telegram-user-client-connector
    environment:
      SWITCHBOARD_MCP_URL: http://switchboard:40100/sse
      CONNECTOR_PROVIDER: telegram
      CONNECTOR_CHANNEL: telegram
      CONNECTOR_ENDPOINT_IDENTITY: telegram:user:${TELEGRAM_USER_ID}
      # Telegram user-client credentials come from owner contact_info (dashboard).
      # No secret files needed for TELEGRAM_API_ID/HASH/SESSION.
      CONNECTOR_CURSOR_PATH: /data/cursor.json
      CONNECTOR_MAX_INFLIGHT: "8"
      CONNECTOR_BACKFILL_WINDOW_H: "24"
    volumes:
      - connector-data:/data
    secrets:
      - switchboard_token
      - telegram_api_id
      - telegram_api_hash
      - telegram_session
    depends_on:
      - switchboard
    restart: unless-stopped

volumes:
  connector-data:

secrets:
  switchboard_token:
    external: true
  telegram_api_id:
    external: true
  telegram_api_hash:
    external: true
  telegram_session:
    external: true
```

Start the connector:

```bash
docker-compose up -d telegram-user-client-connector
docker-compose logs -f telegram-user-client-connector
```

## Operational Monitoring

### Health Checks

Monitor connector health:

```bash
# Check systemd status
sudo systemctl status telegram-user-client-connector

# Check logs for errors
sudo journalctl -u telegram-user-client-connector -f

# Verify cursor advancement
cat /var/lib/butlers/connectors/telegram-user-client/cursor.json

# Check Switchboard ingest metrics
curl http://localhost:40100/metrics
```

### Key Metrics

Monitor these metrics:

- **Connector uptime**: Should remain connected without frequent disconnects
- **Message ingest rate**: Compare with expected account activity
- **Ingest acceptance rate**: Should be >99% (excluding duplicates)
- **Checkpoint lag**: Time between last processed message and current time
- **Error rate**: Failed ingest submissions or normalization errors

### Common Issues

**Connection failures:**
- Verify Telegram credentials are valid
- Check network connectivity to Telegram servers
- Ensure session string is not expired/revoked

**Ingest MCP errors:**
- Verify SWITCHBOARD_MCP_URL is reachable
- Review Switchboard logs for validation errors

**High checkpoint lag:**
- Increase CONNECTOR_MAX_INFLIGHT for higher throughput
- Check for Switchboard API performance issues
- Verify network latency between connector and Switchboard

## Privacy & Compliance

### User Consent

Before enabling this connector, you MUST:

1. Obtain explicit written consent from the account owner
2. Disclose which chats/message types will be ingested
3. Document retention period and data handling practices
4. Provide clear opt-out instructions

### Scope Controls

Recommended scope controls (to be implemented):

- **Chat allowlist**: Only ingest from specific chat IDs
- **Chat denylist**: Exclude sensitive chats (e.g., work, medical)
- **Sender filtering**: Allow/deny specific sender IDs
- **Content redaction**: Strip sensitive patterns before ingest

### Audit Trail

Log these events:

- Connector start/stop with timestamp and operator
- Configuration changes (scope, credentials)
- Session rotation/revocation events
- Privacy-related errors (scope violations, redaction failures)

### Credential Rotation

Rotate credentials regularly:

- **Session strings**: Every 90 days (production) or after suspected compromise
- **API credentials**: Follow Telegram's best practices
- **Switchboard tokens**: Follow platform token rotation policy

## Troubleshooting

### Telethon Not Found

If you see "Telethon is not installed":

```bash
# Install Telethon
uv pip install telethon>=1.36.0

# Or reinstall butlers with connector extras
uv sync --extra connectors
```

### Session Expired

If session is expired or revoked:

1. Generate a new session string (see Prerequisites)
2. Update the `telegram_user_session` entry in owner contact_info via the dashboard
3. Restart the connector

### Duplicate Messages

Duplicate ingestion is normal and expected during:

- Connector restarts (checkpoint replay)
- Backfill on first startup
- Network retries

Switchboard handles deduplication automatically.

## Security Hardening

### Production Checklist

- [ ] Store all credentials in secret manager (not environment files)
- [ ] Enable audit logging for all connector operations
- [ ] Run connector with minimal OS privileges (non-root user)
- [ ] Use encrypted checkpoint storage
- [ ] Configure network firewall rules (allow only Switchboard + Telegram)
- [ ] Set up alerting for connector failures
- [ ] Document incident response procedures
- [ ] Implement credential rotation automation
- [ ] Review privacy controls quarterly
- [ ] Test disaster recovery procedures

### Defense in Depth

- Connector should run in isolated environment (container/VM)
- Network segmentation between connector and other services
- Encrypted transit (TLS) for Switchboard API calls
- Regular security audits of connector configuration
- Automated credential expiry enforcement

## References

- [Connector Interface Contract](interface.md)
- [Telegram User-Client Connector Spec](telegram_user_client.md)
- [Switchboard API Authentication](../switchboard/api_authentication.md)
- [Telethon Documentation](https://docs.telethon.dev/)
