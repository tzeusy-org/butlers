## Why

The Spotify connector treats a never-connected Spotify account as a fatal
startup error: `_resolve_credentials()` raises out of `start()`, the process
exits non-zero, Docker restarts it, and the cycle repeats indefinitely. An
unconfigured optional connector is an expected steady state, not a failure.
The crashloop burns a container slot, floods the logs with an identical
traceback per restart, and makes a genuinely broken connector
indistinguishable at a glance from one the owner simply has not set up.

The rest of the fleet already has a convention for this: Gmail, Calendar, and
Drive managers log `no qualifying accounts found at startup. Running in
idle/degraded mode`, Google Health keeps a degraded heartbeat under the
sentinel identity `google_health:degraded`, and Steam reports
`steam:no_accounts`. Spotify is the outlier.

## What Changes

- Distinguish "never connected" from every other credential fault so only the
  former is non-fatal.
- Park an unconfigured connector in a stable, observable `degraded` state
  under a sentinel endpoint identity instead of exiting, and re-check on the
  existing 60s credential-recheck cadence.
- Activate in place when the owner connects an account — no restart required.
- Keep every post-configuration credential fault fatal or `error`-stated
  exactly as before, and keep parked-state log volume to one line per state
  change rather than one traceback per restart.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `connector-spotify`: the connector lifecycle gains an explicit
  awaiting-credentials parked state.

## Impact

Affected systems are the Spotify connector's startup sequence, health-state
reporting, and poll loop, plus focused connector tests. No credential, OAuth
scope, migration, API, dashboard, module, or routing change.
