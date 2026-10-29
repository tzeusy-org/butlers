# Retire legacy deployment entrypoints

## Why

Helm is the production deployment path and now records release outcomes through
`butlers deployments record` (bu-viat6h.5). The adopted no-production-Compose
decision and bu-dss7he.14 retire the unused Compose production command and
observability overlay. Development Compose, protected restore-drill isolation,
Helm entrypoints, deployment history and cluster monitoring assets remain.

This is the first retirement stage. bu-dss7he.19 owns complete development
Compose removal; restore and ambient-listener replacements keep their existing owners.
No live deployment, restore, firewall or data deletion is authorized.

## What Changes

- Remove the legacy deployment CLI/module and the production/observability Compose entrypoints.
- Preserve Helm recording, deployment history, dev Compose isolation and cluster assets.
- Sync only this completed bounded retirement; later dev Compose removal stays separate.

## Impact

Affected capabilities: deployment-and-drift, system-overview-page, build-reproducibility.
This source candidate does not waive admission, independent review or hosted/protected gates.
