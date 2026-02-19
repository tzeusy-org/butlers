# Google Calendar Setup Runbook (Shared Butler Calendar)

This runbook documents the production setup for Google Calendar in Butlers v1.

> **Deprecation Notice:** The `BUTLER_GOOGLE_CALENDAR_CREDENTIALS_JSON` env var is
> deprecated. Use the dashboard OAuth flow to store credentials in the database and
> set `DATABASE_URL` + `GOOGLE_OAUTH_CLIENT_ID` + `GOOGLE_OAUTH_CLIENT_SECRET` for
> the credential bootstrap. See `docs/oauth/google/setup-guide.md` for details.

## Scope

- Provider: Google Calendar
- Credentials: stored in butler database (DB-first) with env-var fallback
- Legacy env var (deprecated): `BUTLER_GOOGLE_CALENDAR_CREDENTIALS_JSON`
- Event placement: shared Butler calendar (not `primary`)
- Default conflict posture: `suggest`
- Deferred scope: attendee invites/sending invitations

## 1. Provision Google OAuth Credentials

1. Create or select a Google Cloud project.
2. Enable the **Google Calendar API** for that project.
3. Configure the OAuth consent screen for the Google account that will own the calendars.
4. Create an OAuth client ID (Desktop/Web as appropriate for your token bootstrap flow).
5. Authorize with scope:
   - `https://www.googleapis.com/auth/calendar.events`
6. Complete an OAuth flow that returns an offline `refresh_token`.

Required credential fields:

- `client_id`
- `client_secret`
- `refresh_token`

## 2. Configure Google OAuth Credentials

**Recommended: Dashboard OAuth flow (DB-first)**

1. Set the app config env vars (required for the OAuth bootstrap):
   ```bash
   export GOOGLE_OAUTH_CLIENT_ID=your-client-id.apps.googleusercontent.com
   export GOOGLE_OAUTH_CLIENT_SECRET=your-client-secret
   export DATABASE_URL=postgres://butlers:butlers@localhost:5432/butlers
   ```

2. Start the butler and visit `http://localhost:8200/oauth/google/start` to complete the OAuth flow.
   The refresh token is stored in the database automatically.

**Legacy: Environment variable (deprecated, backward-compatible)**

If you cannot use the dashboard flow, set `BUTLER_GOOGLE_CALENDAR_CREDENTIALS_JSON`:

```bash
export BUTLER_GOOGLE_CALENDAR_CREDENTIALS_JSON='{
  "client_id": "your-client-id.apps.googleusercontent.com",
  "client_secret": "your-client-secret",
  "refresh_token": "your-refresh-token"
}'
```

> **Note:** This env var is deprecated. It will continue to work as a fallback but will
> be removed in a future release. Migrate to the DB-first flow when possible.

The daemon validates this env var at startup for calendar-enabled butlers.

## 3. Create Shared Butler Calendar

Use Google Calendar UI and create a single shared calendar for all Butler-managed events:

1. Open calendar settings.
2. Create a new calendar named `Butler` (or similar).
3. Copy the **Calendar ID** (from "Integrate calendar").
4. Use that ID in all calendar-enabled butler configs.

Do not use `primary` for Butler-managed writes.

## 4. Configure Butler Calendar Modules

All calendar-enabled butlers share the same calendar. The configuration uses:

```toml
[modules.calendar]
provider = "google"
calendar_id = "butler@group.calendar.google.com"

[modules.calendar.conflicts]
policy = "suggest"
```

In this repository, the shared calendar ID is configured in:

- `roster/general/butler.toml`
- `roster/health/butler.toml`
- `roster/relationship/butler.toml`

All three butlers write to the same calendar. Per-butler attribution is preserved through event metadata (`butler_name` and `butler_generated` tags in `extendedProperties.private`).

## 5. Runtime Behavior and Operator Expectations

- Butler-created events are written to the shared Butler calendar.
- Events are tagged with the butler's name via `extendedProperties.private.butler_name` for per-butler attribution.
- Default conflict behavior is `suggest` (propose alternatives first).
- Overlap override should only be used when the user explicitly asks to keep a conflict.
- Multiple butlers can write to the shared calendar without interference — events remain distinctly attributable by butler.
