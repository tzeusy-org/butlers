# Operations

> **Scope:** Running Butlers in production and maintaining it.
> **Belongs here:** Docker deployment, environment config, monitoring, scaling, troubleshooting.
> **Does NOT belong here:** First-time dev setup (see [Getting Started](../getting_started/index.md)), module development.

- [Docker Deployment](docker-deployment.md) — Docker Compose setup, production deployment
- [Kubernetes Deployment](kubernetes-deployment.md) — Helm chart for k3s (`butlers` / `butlers-dev` namespaces), cutover from compose
- [Workload Egress Profiles](egress-profiles.md) - reviewed per-workload egress and the shared gateway (default off; RFC 0038 S7)
- [Public MCP Read Surface](public-mcp-read-surface.md): target setup for the second database `butlers_public`, its replication and the public server on a fresh environment, homelab and Butlers steps, the owner step cadence (RFC 0039; not yet built)
- [Deployment Posture](deployment-posture.md) — dev vs hardened posture, Grafana anon-viewer gating
- [Environment Config](environment-config.md) — configuration reference, secrets directory
- [Runtime-Probe Control Keys](runtime-probe-control-keys.md) — signer/verifier documents, provisioning, restart-driven rotation
- [Backup and Restore](backup-restore.md) — backup cadence, restore drill, integrity verification
- [Off-Node Backup Copy](offsite-backup-copy.md) — encrypted restic copy of the dumps to the owner's tailnet device (disabled by default; owner-gated steps)
- [Memory Authority Demotion](memory-authority-demotion.md) — operator-only, default dry-run demotion of laundered owner-authority memory; unresolved evidence and approval boundary
- [Data Retention](data-retention.md) — per-table retention decisions, opt-in pruners
- [Grafana Monitoring](grafana-monitoring.md) — dashboards, tracing, logging
- [Tailnet Health Monitoring](tailnet-health-monitoring.md) — canonical strict-TLS health probe handoff
- [Connector Scaling](connector-scaling.md) — horizontal scaling for connectors
- [Decision Beads](decision-beads.md) — owner-decision bead convention and its linter
- [Ambient Projections](ambient-projections-home-assistant.md) — glance formats (JSON, text, 1-bit PNG) and a Home Assistant package with an edge staleness sensor
- [Troubleshooting](troubleshooting.md) — common issues, debugging, health checks
- [Runtime Attention](runtime-attention.md) — runtime-attention paging path and its stored functions
- [QA Publication Boundary](qa-publication-boundary.md) — durable publication operations and ambiguous-lineage holds (publication disabled)
- [Image Bump Procedure](image-bump-procedure.md) — updating pinned service image tags
- [Fleet and QA-Patrol Conditions](fleet-condition-controller.md) — independent fleet condition, QA patrol age, and per-butler handoff
- [Relationship Effective-Time Cutover](relationship-effective-time-cutover.md) — draft hard cutover: stop every client, lock-and-refuse G drops the legacy index
- [Receiver-Derived Routing Cutover](receiver-derived-routing-cutover.md) — transitional: flag cutover and rollback boundary until production activation
- [Coordinator Guardrails](coordinator-guardrails.md) — beads coordinator reconciliation rules and bd quirks
