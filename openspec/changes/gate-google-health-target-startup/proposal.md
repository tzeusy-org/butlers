## Why

Google Health can begin ingestion after Switchboard starts but before Health
accepts routes. The confirmed startup race leaves source events failed without
automatic target recovery. A process-level HTTP 200 does not prove routability.

## What Changes

- Add a Google Health Helm startup gate using Health's existing internal identity
  endpoint, after the existing OAuth and Switchboard gates.
- Require the expected identity and literal route acceptance, with finite request
  and overall deadlines and bounded, content-free diagnostics.
- Keep connector runtime behavior and every other connector deployment unchanged.

## Capabilities

### Modified Capabilities

- `connector-google-health`: add the deployment-startup readiness requirement.

## Impact

The shared Helm chart, one startup probe script, focused HTTP/render tests and
deployment documentation change. No daemon protocol, database, OAuth scope,
connector polling behavior, replay or live rollout changes. The gate prevents a
startup race; it cannot recover earlier failures or prevent later target outages.
