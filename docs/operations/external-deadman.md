# Database-gated outside heartbeat

The Dashboard's existing supervised `external_deadman` loop remains disabled
when `EXTERNAL_DEADMAN_URL` is absent. Missing configuration is not a successful
ping. The existing content-blind `ExternalDeadmanUnconfigured` assurance
condition and QA staleness policy remain unchanged.

When configured by an authorized operational workflow, each ten-minute tick:

1. Acquires the Dashboard's existing Switchboard pool with a five-second bound.
   A fixed transaction advisory lock serializes this one producer's claims.
2. Checks every server-configured daemon against the registry and receiver
   projection, including configured daemons whose pools failed to open. A
   missing row, malformed value, old boot, expired observation, route refusal,
   unknown state or quarantine withholds the heartbeat. An explicit `paused`
   policy is the existing fleet controller's intentional exclusion.
3. Commits one content-blind audit tick claim, then reads it through a separate
   acquisition. Unavailable tables, failed commit or missing readback withhold
   the heartbeat. The claim is not a success marker.
4. Sends one bodyless HTTP(S) GET to the configured target, with redirects and
   environment proxies disabled, and without reading the response body. Only
   2xx succeeds. Transport logs from this task omit the credential-bearing URL.
5. Records the existing success marker with a fixed target and the tick UUID;
   neither the endpoint nor raw failure text is persisted or logged.

The overall tick has a 30-second cancellation deadline, HTTP has a ten-second
bound, and pool acquisition/release uses five seconds. Cooperative driver and
HTTP cleanup is awaited; no detached retry task is created. Cancellation
propagates to the existing lifespan supervisor and shutdown owner.

A UTC cadence bucket determines a stable tick UUID. The committed claim stops
another process, loop restart or retry from resending that tick. A lost reply
or cancellation after sending is uncertain: the outside monitor may already
have received it, so that tick is never retried. A failed success-marker write
returns non-success locally. Database and HTTP commits cannot be made atomic;
a successful HTTP request proves the immediately preceding database/fleet
observation, not future database availability. No endpoint-specific failure
suffix, arbitrary status body or monitor protocol is invented.

An adverse tick is held immediately. The calendar deadman's separate two-tick
escalation does not authorize sending a healthy outside heartbeat during a
known bad external-deadman tick. Full semantic probes/recovery remain outside
this correction.

## Optional Helm plumbing

`dashboardApi.externalDeadman.enabled` defaults to `false`. Enabling rendering
requires `urlSecretRef.name` and `urlSecretRef.key`, pointing to an existing
Secret supplied by a separately authorized operational workflow. The chart
renders `valueFrom.secretKeyRef`, never an endpoint value. `intervalSeconds`
defaults to 600 and must be positive and finite at runtime. The existing
Compose environment contract remains compatible.

This change does not create a Secret, configure a monitor, open an account,
install an outside witness, change egress policy or activate/deploy the loop.
Rollback disables the optional configuration and preserves audit history;
never delete claims to force a resend. Same-host state is an early warning,
not proof that the independent outside witness itself is alive.

Prefer HTTPS for the operator-configured endpoint. Existing HTTP endpoint support is preserved.
