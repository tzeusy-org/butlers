## 1. Parked awaiting-credentials state

- [x] 1.1 Add failing connector regressions for the never-connected startup
  path, the parked health state, the in-place activation transition, and the
  still-fatal non-unconfigured credential fault.
- [x] 1.2 Split the never-connected case into its own credential exception and
  park `start()` on it instead of propagating.
- [x] 1.3 Report the parked state as `degraded` / `awaiting_credentials`
  through the heartbeat and `/health`, under a sentinel endpoint identity.
- [x] 1.4 Re-check on the existing credential cadence and activate in place,
  rebinding the heartbeat to the resolved identity.
