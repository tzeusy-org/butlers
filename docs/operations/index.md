# Operations

> **Scope:** Running Butlers in production and maintaining it.
> **Belongs here:** Docker deployment, environment config, monitoring, scaling, troubleshooting.
> **Does NOT belong here:** First-time dev setup (see [Getting Started](../getting_started/index.md)), module development.

- [Docker Deployment](docker-deployment.md) — Docker Compose setup, production deployment
- [Deployment Posture](deployment-posture.md) — dev vs hardened posture, Grafana anon-viewer gating
- [Environment Config](environment-config.md) — configuration reference, secrets directory
- [Runtime-Probe Control Keys](runtime-probe-control-keys.md) — signer/verifier documents, provisioning, restart-driven rotation
- [Backup and Restore](backup-restore.md) — backup cadence, restore drill, integrity verification
- [Data Retention](data-retention.md) — per-table retention decisions, opt-in pruners
- [Grafana Monitoring](grafana-monitoring.md) — dashboards, tracing, logging
- [Tailnet Health Monitoring](tailnet-health-monitoring.md) — canonical strict-TLS health probe handoff
- [Connector Scaling](connector-scaling.md) — horizontal scaling for connectors
- [Decision Beads](decision-beads.md) — owner-decision bead convention and its linter
- [Troubleshooting](troubleshooting.md) — common issues, debugging, health checks
- [Runtime Attention](runtime-attention.md) — runtime-attention paging path and its stored functions
- [Image Bump Procedure](image-bump-procedure.md) — updating pinned service image tags
- [Fleet and QA-Patrol Conditions](fleet-condition-controller.md) — independent fleet condition, QA patrol age, and per-butler handoff
- [Receiver-Derived Routing Cutover](receiver-derived-routing-cutover.md) — transitional: flag cutover and rollback boundary until production activation
