# Google Calendar Setup Runbook (Dedicated Butler Subcalendars)

This runbook documents the production setup for Google Calendar in Butlers v1.

## Scope

- Provider: Google Calendar
- Credentials env var: `BUTLER_GOOGLE_CALENDAR_CREDENTIALS_JSON`
- Event placement: dedicated Butler subcalendars (not `primary`)
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

## 2. Set Required Butler Credential Env Var

Set `BUTLER_GOOGLE_CALENDAR_CREDENTIALS_JSON` to a JSON object containing the OAuth fields:

```bash
export BUTLER_GOOGLE_CALENDAR_CREDENTIALS_JSON='{
  "client_id": "your-client-id.apps.googleusercontent.com",
  "client_secret": "your-client-secret",
  "refresh_token": "your-refresh-token"
}'
```

The daemon validates this env var at startup for calendar-enabled butlers.

## 3. Create Dedicated Butler Subcalendars

Use Google Calendar UI and create a dedicated calendar per scheduling butler (example names):

- `Butler - General`
- `Butler - Health`
- `Butler - Relationship`

For each new calendar:

1. Open calendar settings.
2. Copy the **Calendar ID** (from "Integrate calendar").
3. Use that ID in the matching butler config.

Do not use `primary` for Butler-managed writes.

## 4. Configure Butler Calendar Modules

Each scheduling butler should include:

```toml
[modules.calendar]
provider = "google"
calendar_id = "butler-general@group.calendar.google.com"
default_conflict_policy = "suggest"
```

In this repository, the dedicated IDs are configured in:

- `roster/general/butler.toml`
- `roster/health/butler.toml`
- `roster/relationship/butler.toml`

## 5. Runtime Behavior and Operator Expectations

- Butler-created events are written to the configured dedicated subcalendar.
- Default conflict behavior is `suggest` (propose alternatives first).
- Overlap override should only be used when the user explicitly asks to keep a conflict.
- Attendee invites are out of v1 scope; do not add attendees or send invitations.
- Recurrence updates are series-scoped in v1 (`recurrence_scope="series"`).

## 6. Validate End-to-End

1. Start the relevant butler(s) with calendar module enabled.
2. Call `calendar_list_events` and verify:
   - response `calendar_id` matches the configured subcalendar ID
   - events are read from the expected dedicated calendar
3. Create a test event with `calendar_create_event`.
4. Confirm it appears in the dedicated subcalendar (not in `primary`).
5. Create an intentional overlap request and verify the assistant proposes alternatives by default.
