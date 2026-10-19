## 1. Read the recorded heartbeat

- [x] 1.1 Replace the loopback proxy on `GET /api/steam/connector/health` with a read of the newest Steam row in `switchboard.connector_registry`.
- [x] 1.2 Derive liveness with the shared `derive_liveness` policy; stale, offline, future-dated and absent heartbeats read `not_running`.
- [x] 1.3 Report an unreadable registry as `unknown` with `registry_available=false`.
- [x] 1.4 Return `active_accounts` null and `connector_health` null; never attribute the connector-wide state to one account.

## 2. Verification

- [x] 2.1 Unit tests with outbound sockets forbidden cover each state, the unreadable registry, and a two-account case.
- [x] 2.2 A real-database test pins the registry query to Steam heartbeats and the `steam:config` exclusion.
- [x] 2.3 Run strict OpenSpec validation and the overwrite, archive and citation guards.
