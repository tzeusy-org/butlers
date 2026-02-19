# Google OAuth Documentation

This directory contains comprehensive documentation for setting up and operating Google OAuth 2.0 authentication with Butlers, enabling secure access to Gmail and Google Calendar APIs.

## Documents

### [setup-guide.md](./setup-guide.md)

**For**: Initial OAuth setup and first-time configuration.

Covers:
- Google Cloud Console project creation
- OAuth consent screen configuration
- Gmail API and Google Calendar API enablement
- OAuth 2.0 credentials creation
- Refresh token acquisition (interactive flow via OAuth Playground or Python)
- Local HTTPS setup with Tailscale Serve
- Required scopes for Gmail and Calendar
- Secret storage best practices
- Troubleshooting setup issues

**Start here** if you're setting up Google OAuth for the first time.

### [operational-runbook.md](./operational-runbook.md)

**For**: Day-to-day operations, monitoring, and troubleshooting.

Covers:
- Verifying OAuth token validity
- Credential rotation and updates
- Token refresh failures and recovery
- Scope management and addition
- Multi-account setups
- Monitoring and alerting
- Disaster recovery procedures
- Migration between environments

**Use this** when managing OAuth in production or troubleshooting token issues.

## Quick Start

1. **First time?** Read [setup-guide.md](./setup-guide.md) from top to bottom.
   - Should take ~30 minutes to complete all steps
   - You'll end up with a valid refresh token and working OAuth integration

2. **Need to troubleshoot?** Jump to the relevant section in [operational-runbook.md](./operational-runbook.md).
   - Look for your error message
   - Follow the diagnostic and recovery steps

3. **Operating in production?** Review [operational-runbook.md](./operational-runbook.md) Section 7 (Monitoring and Alerting).

## Key Concepts

### OAuth Tokens

- **Access Token**: Short-lived (1 hour), used to call Google APIs. Automatically refreshed.
- **Refresh Token**: Long-lived (6 months or indefinite), used to obtain new access tokens. Must be stored securely.

### Scopes (Permissions)

Butlers requires these scopes:
- `https://www.googleapis.com/auth/gmail.modify` — Read and organize Gmail emails
- `https://www.googleapis.com/auth/calendar` — Read and create Google Calendar events

### HTTPS Requirement

Google OAuth requires HTTPS for redirect URIs (except localhost). Use **Tailscale Serve** for local development.

### Secret Storage

Never commit OAuth secrets to git. Use:
- Environment variables (development)
- Kubernetes secrets (Kubernetes)
- AWS Secrets Manager, GCP Secret Manager, or HashiCorp Vault (cloud/self-hosted)

## Environment Variable Reference

```bash
# Required: JSON-encoded OAuth credentials
BUTLER_GOOGLE_CALENDAR_CREDENTIALS_JSON='{"client_id":"...","client_secret":"...","refresh_token":"..."}'
```

## Common Tasks

| Task | Guide |
|------|-------|
| Initial setup | [setup-guide.md](./setup-guide.md) |
| Get refresh token | [setup-guide.md Part 2](./setup-guide.md#part-2-obtaining-refresh-tokens) |
| Local HTTPS with Tailscale | [setup-guide.md Part 3](./setup-guide.md#part-3-local-https-setup-with-tailscale-serve) |
| Fix "Invalid Redirect URI" | [setup-guide.md Troubleshooting](./setup-guide.md#issue-invalid-redirect-uri) |
| Rotate credentials | [operational-runbook.md 2.1](./operational-runbook.md#21-planned-credential-rotation-no-downtime) |
| Handle compromised token | [operational-runbook.md 2.2](./operational-runbook.md#22-emergency-credential-replacement-token-compromised) |
| Debug token errors | [operational-runbook.md Section 3](./operational-runbook.md#section-3-troubleshooting) |
| Add a new scope | [operational-runbook.md 5.2](./operational-runbook.md#52-adding-a-new-scope) |

## Troubleshooting Checklist

If Butlers fails to authenticate:

1. **Is the env var set?**
   ```bash
   echo $BUTLER_GOOGLE_CALENDAR_CREDENTIALS_JSON
   ```
   If empty, see [operational-runbook.md 3.1](./operational-runbook.md#31-error-calendarcredentialerror--must-be-set-to-a-non-empty-json-object)

2. **Is the JSON valid?**
   ```bash
   echo $BUTLER_GOOGLE_CALENDAR_CREDENTIALS_JSON | jq .
   ```
   If invalid, see [operational-runbook.md 3.2](./operational-runbook.md#32-error-calendarcredentialerror--is-missing-required-fields)

3. **Can the token be refreshed?**
   Check logs for `CalendarTokenRefreshError`. See [operational-runbook.md 3.3](./operational-runbook.md#33-error-calendartokenrefresherror-failed-to-refresh-access-token)

4. **Do you have the right scopes?**
   Check logs for 403 Forbidden errors. See [operational-runbook.md 3.5](./operational-runbook.md#35-error-calendarrequesterror--api-request-failed-403-insufficient-permissions)

## Related Documents

- [Calendar Module Documentation](../../modules/) — Implementation details for the calendar module
- [Gmail Connector Documentation](../connectors/gmail.md) — Gmail ingestion setup
- [Project Overview](../../) — Architecture and design principles

## Support

For issues not covered in these docs:

1. Check the Google OAuth documentation: https://developers.google.com/identity/protocols/oauth2
2. Review your Google Cloud Console OAuth consent screen configuration
3. Verify redirect URIs match exactly what's in your Butlers deployment
4. Enable debug logging: `export BUTLERS_LOG_LEVEL=DEBUG`

---

**Last Updated**: 2025-02-19

**Scope**: Butlers v1 MVP, Calendar module
