## Why

Operator-facing security surfaces currently risk presenting a failed status
probe as though it were known state: an audit-log failure can look like an
empty history, and a Telegram session-status failure can look like an
unconfigured setup. The owner needs an explicit, safely retryable unavailable
state without exposing credentials or changing authentication behavior.

## What Changes

- Distinguish a successful empty privileged-audit response from an unavailable
  audit source in the Permissions page audit reel, including retry and visibly
  degraded cached rows.
- Distinguish Telegram session-status loading, unavailable, and successful
  unready states in the Passport setup flow, with a safe status retry.
- Preserve the existing Telegram setup, consent, session-auth, and secret
  handling contracts when a status probe succeeds.
- Add focused rendered regression coverage for the availability states.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `dashboard-permissions`: audit-reel availability failures are named and
  retryable rather than rendered as a no-history state.
- `butler-secrets`: Telegram user-session status loading and failures are
  distinct from successful unready setup state and expose no credential data.

## Impact

- `frontend/src/pages/SettingsPermissionsPage.tsx` and its focused tests.
- `frontend/src/components/relationship/TelegramSessionSetup.tsx` and its
  focused tests.
- Narrow OpenSpec deltas only; no API, persistence, authorization, consent,
  or credential-storage changes.
