# Runtime Isolation and Infrastructure Configuration

> **Purpose:** Document unconditional runtime role enforcement and independent infrastructure diagnostics.
> **Audience:** Operators running Butlers in local or private-network deployments.
> **Prerequisites:** [Docker Deployment](docker-deployment.md), [Grafana Monitoring](grafana-monitoring.md).

## Deployment scope

The live dev fleet runs on Kubernetes in `butlers-dev` and uses the cluster's
existing LGTM stack. The Butlers chart does not deploy Grafana or consume
the retained Compose observability configuration; Compose's `--observability`
flag does not change cluster Grafana access. Follow the site's LGTM access policy and
[Kubernetes Deployment](kubernetes-deployment.md) for the live dev workflow.

This page describes retained production/local Compose deployments. Use the
recipes below only against a database not served by a running Kubernetes fleet;
the Compose launcher refuses a competing fleet. Direct Compose is not a way to
bypass that ownership guard.

## Independent configuration and runtime isolation

Butler runtime pools always establish the configured runtime role before serving queries. A missing, unconfigured, unreadable or unassumable role refuses startup or acquisition, including in development. Each acquire re-establishes the role. There is no deployment-profile opt-out, and this does not provision roles or change dashboard DatabaseManager authority. Use the reviewed bootstrap procedure for role/login/grant prerequisites; do not grant privileges or run an installer as an availability fallback.

Memory side pools retain the already established primary Database owner role. Only that owner's domain and the existing Chronicler `chronicler_mem` private search path are supported; a private schema never selects another runtime role. The private connection checks schema USAGE under the owner role before publication and on every acquire; table privileges remain PostgreSQL-enforced. Missing private-schema access refuses rather than using the connecting login. Current managed bootstrap/memory migration sources do not establish the Chronicler private grants, so working private-memory access remains a managed-topology prerequisite, not a claim supplied by this runtime change.

The retained Compose launcher defaults `GF_AUTH_ANONYMOUS_ENABLED` to `false`. An operator can explicitly set it to `true` for local viewing; the process configuration diagnostic reports enabled anonymous access without an exemption. This does not configure Kubernetes Grafana or authorize a deployment.

Known-default MinIO/Grafana credentials retain loud service/key-name warnings without exposing values. Absence follows the retained Compose default configuration for this diagnostic; it is not proof of the remote service's credential state. The health endpoint separately reports the actual API DatabaseManager role state, conservatively disabled when unavailable. None of these booleans attests that the whole fleet is secure.

## Runtime-probe control remains independent

The signed runtime-probe control path retains its fail-closed key and verifier requirements. Missing documents never authorize an unsigned or shared-bearer probe. Configuration and provisioning remain governed by [Runtime-Probe Control Keys](runtime-probe-control-keys.md); this runtime-role change neither provisions a key nor changes the probe authority.
