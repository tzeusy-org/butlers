# Connector Documentation Changelog

This file tracks updates to connector documentation to ensure synchronization with implementation.

## 2026-02-15 - Conformance Tests and Runbooks

### Added
- **tests/integration/test_connector_conformance.py**: End-to-end conformance tests covering:
  - Connector-to-ingest acceptance for Telegram and Gmail
  - Dedupe replay behavior validation
  - Downstream routing handoff structure verification
  - Checkpoint recovery testing
  - Error handling (HTTP errors, rate limits)

- **docs/runbooks/connector_operations.md**: Operational runbook covering:
  - Deployment modes (polling vs webhook for Telegram, watch+history for Gmail)
  - Checkpoint recovery procedures
  - Cutover operations from module-owned to connector-owned ingestion
  - Rollback operations
  - Monitoring and alerting guidance
  - Troubleshooting common issues
  - Complete environment variable reference

### Documentation Sync Status

| Document | Implementation | Status | Notes |
|----------|---------------|--------|-------|
| `docs/connectors/interface.md` | `src/butlers/connectors/*` | ✅ Synced | Contract matches implementation |
| `docs/connectors/telegram_bot.md` | `src/butlers/connectors/telegram_bot.py` | ✅ Synced | Polling and webhook modes documented |
| `docs/connectors/gmail.md` | `src/butlers/connectors/gmail.py` | ✅ Synced | Watch+history flow documented |
| `docs/connectors/connector_ingestion_migration_delta_matrix.md` | N/A | ✅ Current | Migration plan up-to-date |
| `docs/runbooks/connector_operations.md` | Runtime behavior | ✅ Synced | Covers deployed connector operations |

### Known Gaps and Follow-Up Items

The following gaps were identified during conformance test and documentation review:

1. **Telegram User Client Connector** (referenced in `docs/connectors/telegram_user_client.md`)
   - Status: DRAFT, not yet implemented
   - Scope: v2/gated feature requiring explicit user consent
   - Follow-up bead: butlers-zb7.5 (blocked, not yet ready)
   - Notes: User-client ingestion requires additional privacy controls beyond bot ingestion

2. **Discord Connector** (referenced in `docs/connectors/draft_discord.md`)
   - Status: DRAFT, v2-only
   - Scope: Future connector for Discord bot/webhook ingestion
   - Follow-up bead: TBD (not part of current epic)
   - Notes: Draft spec exists but no implementation planned for v1

3. **Ingest API Authentication**
   - Current state: Bearer token auth via `SWITCHBOARD_API_TOKEN`
   - Gap: No public documentation for token generation/rotation
   - Follow-up: Document token lifecycle in Switchboard API docs
   - Issue: butlers-zb7.2 covers API exposure but not auth lifecycle

4. **Connector Metrics/Observability**
   - Current state: Structured logging exists
   - Gap: No standardized metrics export (Prometheus, etc.)
   - Follow-up: Add metrics instrumentation for production monitoring
   - Priority: P2 (nice-to-have for v1, required for scale)

5. **Multi-Tenant Connector Deployment**
   - Current state: One connector instance per endpoint identity
   - Gap: No guidance for horizontal scaling with coordinated checkpointing
   - Follow-up: Document lease-based coordination for replicas
   - Priority: P3 (future enhancement)

6. **Gmail Watch Pub/Sub Integration**
   - Current state: Polling-based history fetch
   - Gap: No Pub/Sub push notification integration
   - Follow-up: Implement optional Pub/Sub mode for lower latency
   - Priority: P2 (optimization, polling works for v1)

7. **Connector Health Check Endpoint**
   - Current state: No HTTP health endpoint
   - Gap: Kubernetes readiness/liveness probes would fail
   - Follow-up: Add `/health` endpoint to connector runtimes
   - Priority: P1 for production deployment

### Conformance Test Coverage

✅ **Covered:**
- Telegram ingest acceptance and envelope structure
- Gmail ingest acceptance and envelope structure
- Dedupe replay behavior (both connectors)
- Routing handoff field validation
- Checkpoint recovery (restart scenarios)
- HTTP error handling (5xx, rate limits)

❌ **Not Covered (future work):**
- End-to-end routing verification (requires Switchboard integration)
- Long-running stability tests (crash recovery, multi-day runs)
- Webhook mode integration tests (require ngrok/public endpoint)
- Gmail OAuth token refresh edge cases
- Concurrent connector coordination tests
- Performance/load tests

### Next Steps

1. File follow-up beads for identified gaps (see above)
2. Run conformance tests as part of CI/CD pipeline
3. Monitor production connector deployments for undocumented edge cases
4. Update runbook with lessons learned from operational incidents
