## Context

The Butlers codebase has two connector archetypes:

1. **Messaging connectors** (Telegram, WhatsApp, Discord, Gmail) — per-chat buffering, discretion filtering, ingress routing through the Switchboard to specialist butlers, reply-bearing.
2. **Passive signal connectors** (Spotify, Steam, OwnTracks) — single-owner, state-diff-based, no chats, no discretion, no interactive replies. Pure ingestion.

Google Health is unambiguously archetype 2. The design here deliberately mirrors the Spotify and Steam connectors rather than the WhatsApp pattern; any divergence should be justified.

This change also pivots from an earlier draft targeting the Fitbit Web API. The archived research (`docs/archive/health-wearable-draft.md`, 2026-02-19) assumed a Fitbit Personal app tier with no review requirement. Google has since announced the Fitbit Web API turndown for September 2026 with a successor **Google Health API** at `https://health.googleapis.com`. Every Google Health scope is classified Restricted, so the Personal-tier escape hatch is gone. The pivot is mandatory; the only question was whether to build now or wait. Building now is materially cheaper than it would have been greenfield because the Butlers project already has production Google OAuth plumbing for Calendar and Drive.

## Goals / Non-Goals

**Goals:**
- Passive, owner-only ingestion of Google Health data into the Health butler's memory store.
- Pure-Python connector reusing the existing `httpx` + Google OAuth stack — no new toolchain, no new credential silo.
- Read-only MCP tools on the Health butler for LLM-driven querying of sleep, HR, HRV, activity, and SpO2 history.
- Wellness-derived facts land as temporal SPO memory facts, consistent with the `crud-to-spo-migration` direction already in flight.
- Launch against the new API directly — no migration path through the legacy API.

**Non-Goals:**
- Write-back to Google Health (weight logging, food logging, activity annotations). The device is authoritative.
- Multi-account support in v1. The existing multi-account OAuth infrastructure *supports* it, but the Health butler will read from the primary Google account only until there is a concrete use case for multiple wellness sources.
- Webhook / push ingestion. Google Health does not advertise push callbacks; polling is acceptable given device-sync latency.
- Other wearables (Garmin, Withings, Oura, Whoop, Apple Health). Each requires a dedicated connector and is deferred.
- Real-time notifications triggered by wellness events. Insight generation is the Health butler's job during its scheduled jobs, not a connector concern.
- Intraday timeseries persistence. Per-minute HR, per-minute steps, etc. are fetched on demand by query tools; daily summaries are the permanent record.
- Shipping before Restricted-scope verification clears in production mode. Self-hosted deployments can operate under test-mode consent in the meantime.

## Decisions

### D1: No sidecar — pure Python over httpx + existing Google OAuth pipeline

The Google Health API is a plain OAuth2 REST API. `httpx` is already a connector dependency. Token refresh, account resolution, and scope enforcement are already handled by the existing Google OAuth pipeline feeding Calendar and Drive. The connector imports from `butlers.connectors.google_oauth` (or wherever Calendar's refresh lives today) rather than implementing token lifecycle locally.

**Alternative considered:** A purpose-built Google Health client library.
**Rejected because:** The endpoint surface the butler actually uses is small (~9 data type bundles), making a thin async httpx wrapper simpler than any generated client. If Google ships a Python SDK during the migration window, the wrapper can be swapped with no spec impact.

### D2: Connector as standalone process, mirroring connector-spotify

The Google Health connector is a separate OS process, not an in-daemon module. It joins the Switchboard via MCP exactly like the other connectors. Docker compose gets a `connector-google-health` service.

```yaml
connector-google-health:
  image: butlers-app:latest
  entrypoint: ["/app/scripts/dev_entrypoint.sh", "connectors/google-health",
               "uv", "run", "--frozen", "--no-dev", "python", "-m", "butlers.connectors.google_health"]
  command: []
  environment:
    <<: *connector-env
    CONNECTOR_PROVIDER: google_health
    CONNECTOR_CHANNEL: wellness
    CONNECTOR_HEALTH_PORT: "40086"  # next free port; confirm at implementation
  volumes:
    - ./logs:/app/logs
  networks: [db, backend, egress]  # egress needed for health.googleapis.com; see connector-gmail precedent
  depends_on:
    log-init:
      condition: service_completed_successfully
    migrations:
      condition: service_completed_successfully
    butlers-up:
      condition: service_healthy
    oauth-gate:
      condition: service_completed_successfully  # required for every Google-OAuth connector
  restart: unless-stopped
```

Matches the live `connector-spotify` block at `docker-compose.yml:270-291` line-for-line except for: (a) `CONNECTOR_CHANNEL: wellness` / `CONNECTOR_PROVIDER: google_health`, (b) addition of the `egress` network (Google API calls leave the tailnet, as with `connector-gmail`), (c) a fresh health port. No sidecar, no multi-stage Docker build changes, no EXTRAS gate.

### D3: Credentials reuse the existing Google OAuth infrastructure — no new User Secrets

Earlier drafts (against the Fitbit Web API) proposed owner-scoped credentials via the User Secrets tab. Under the Google Health API that pattern would be duplicative: the owner's Google account is already linked for Calendar and Drive, with a refresh token stored on the companion entity in `entity_info` and scope state tracked in `public.google_accounts.granted_scopes`.

**Design:**

- `FITBIT_CLIENT_ID`, `FITBIT_CLIENT_SECRET`, `FITBIT_REFRESH_TOKEN` do **not** exist. They are not needed.
- The existing `GOOGLE_OAUTH_CLIENT_ID` / `GOOGLE_OAUTH_CLIENT_SECRET` stored in `butler_secrets` under category `google` cover the Google Health API too — it's the same OAuth client.
- The owner's Google account row in `public.google_accounts` gains new entries in `granted_scopes` after the owner re-consents through `GET /api/oauth/google/start?force_consent=true&scope_set=health`.
- The refresh token on that account's companion entity (in `entity_info`) is re-issued by Google to cover the expanded scope set. No new persistence path.
- Access tokens are never persisted — the connector refreshes in-memory per process.

**Rationale:**

- **No credential silo proliferation.** Every Google-authenticated integration in this codebase flows through one place. Adding a second place for "Fitbit data via Google OAuth" would be cargo-culted.
- **Scope *delivery* is a solved problem — scope *selection* is net-new.** `google-multi-account-oauth` documents the `force_consent=true` mechanism, which correctly produces a new refresh token covering an expanded scope set. But the current endpoint hard-codes the scope string and has no selector parameter; `scope_set=health` is introduced by this change. Seen against code, this is "extend the OAuth endpoint with a named-scope-set registry", not "invoke the existing scope-upgrade path."
- **Per-account scope toggle UI is also net-new.** The current `GoogleOAuthSection.tsx` renders `granted_scopes` as a read-only CSV. This change introduces the per-scope-set toggle component (see `dashboard-google-accounts` delta).
- **No surprise for the owner.** The same "Connect Google Account" card in the dashboard that granted Calendar access now offers a Google Health toggle — once built. No separate "Connect Fitbit" experience.

**Additive schema:** The test-mode warning and the dashboard status card require a `metadata JSONB` column and a `last_token_refresh_at TIMESTAMPTZ` column on `public.google_accounts`. If either is already present, the migration is a no-op for that column. The change is still "no new tables," but it is not "no schema change." See the `google-multi-account-oauth` delta for the migration requirement.

### D4: New source channel `wellness`, provider `google_health`

Channel naming debate:

- `health` — rejected, collides with the Health butler's name
- `fitness` — rejected, too narrow; Google Health ingests sleep, SpO2, HRV, and physiology signals beyond fitness
- `wellness` — accepted; broad enough to accommodate future wearables without another channel split

Provider is `google_health` (not `fitbit`) because the API surface we build against is Google Health, not Fitbit. A Pixel Watch, a Wear OS device, or any third-party app that writes to Health Connect on Android and syncs to Google Health feeds the same API — the device brand is irrelevant at the channel level. Future integrations would register additional providers under the same channel: `wellness/oura`, `wellness/withings`.

**RFC 0003 amendment required.** The spec delta for `butler-switchboard` carries the inline amendment note, same pattern as `connector-steam` and `whatsapp-connector`. `tasks.md` lists the RFC markdown edit as a concrete task.

### D5: Data landing — SPO memory facts, not dedicated tables

The health butler is already mid-migration from dedicated CRUD tables to SPO temporal facts (see `openspec/changes/crud-to-spo-migration/specs/predicate-taxonomy.md`). Creating per-metric tables now would introduce cruft that the migration would then have to unwind.

Google Health-derived facts land with `scope='health'`, `entity_id=owner_entity_id`, and predicates:

| Predicate | `valid_at` | `content` | Key `metadata` fields |
|---|---|---|---|
| `sleep_session` | session start | `"Slept Xh Ym (Z% efficiency)"` | `session_id`, `end_time`, `duration_ms`, `efficiency`, `minutes_asleep`, `minutes_awake`, `stages: {deep,light,rem,wake}` |
| `sleep_stage_summary` | session start | `"Deep X, Light Y, REM Z min"` | `session_id`, full `stages` breakdown |
| `measurement_resting_hr` | date 00:00 local | `"Resting HR: N bpm"` | `value`, `heart_rate_zones` |
| `measurement_hrv` | date 00:00 local | `"HRV: N ms"` | `daily_rmssd`, `deep_rmssd`, `coverage` |
| `measurement_spo2` | date 00:00 local | `"SpO2: avg %"` | `avg`, `min`, `max` |
| `measurement_breathing_rate` | date 00:00 local | `"Breathing: N bpm"` | `value` |
| `measurement_steps` | date 00:00 local | `"Steps: N"` | `value`, `distance_km`, `floors` |
| `measurement_active_minutes` | date 00:00 local | `"Active: N min"` | `very_active`, `fairly_active`, `lightly_active`, `sedentary` |
| `measurement_vo2_max` | date 00:00 local | `"VO2 Max: range"` | `range_low`, `range_high`, `midpoint` |

Naming note: measurement-shaped records use the `measurement_{type}` pattern established by `crud-to-spo-migration/specs/predicate-taxonomy.md`. `measurement_resting_hr` is distinct from the pre-existing `measurement_heart_rate` predicate: the former is a daily aggregate derived from continuous monitoring, the latter is a point-in-time manual reading. All nine predicates are registered in `predicate_registry` via the health-butler migration (tasks.md §8).

The raw Google Health JSON payload is carried in each `ingest.v1` envelope's `payload.raw`; the predicate-keyed facts are produced by the Health butler's ingestion handler, not by the connector.

The Google Health API also exposes a **Reconciled Stream** that collapses multiple-source conflicts (e.g. Fitbit device + Pixel Watch + manual entry). The connector MUST consume the reconciled stream rather than per-source streams to avoid double-counting. This is one of the few architectural improvements the new API offers over the legacy Fitbit surface.

### D6: Rate-limit discipline

Google Health API rate limits are not documented in the migration guide as of this change. The connector MUST:

- Capture and expose rate-limit headers on every response via the existing metrics surface.
- Treat 429 as backoff-until-reset using whatever header Google Health returns (pattern: `Retry-After` if present, else exponential backoff).
- Budget conservatively: ~9 calls per daily sync (one per data type bundle), one sync per 30 minutes, plus ad-hoc drilldowns from the LLM. This is comparable to the Fitbit 150 req/hr budget assumed earlier.

Confirming actual limits is a Task-1 deliverable under `tasks.md`.

### D7: No discretion layer, no per-chat buffering

Unlike messaging connectors, wellness data has no chats, no third-party senders, and no privacy-sensitive content to filter. The connector submits directly to the Switchboard without a discretion evaluator.

### D8: Restricted-scope verification — deferred to production deployment, not a blocker for development

All Google Health scopes are classified Restricted. In practice this means:

- **Test mode** (default for a freshly-registered OAuth client): up to ~100 test users explicitly added by the developer in Google Cloud Console can grant the scopes. Refresh tokens expire after 7 days unless the app is marked "internal" for a Google Workspace.
- **Production mode**: requires Google's privacy + security review. Once approved, any user can grant the scopes and refresh tokens are long-lived.

For a self-hosted single-owner Butlers deployment, test mode is sufficient to operate: the owner is the developer, adds themselves as a test user, and the 7-day refresh token expiry becomes a re-consent nudge every week. **Acceptable, but friction.**

For the open-source project more broadly — where downstream users install the butler on their own machines — the OAuth client they register is *their* client, not the Butlers project's. Each downstream user either operates in test mode or submits their own verification. The project can provide a verification-package template as a deliverable of this change but cannot verify on behalf of downstream users.

**Decision:** ship the connector with test-mode support; include a deployment guide explaining the test-mode vs production-mode tradeoff; track the verification-package template as a non-blocking deliverable.

### D9: Failure modes

| Mode | Behaviour |
|---|---|
| Scopes not granted on the primary Google account | Connector starts in degraded mode (health = `degraded`), emits no events, periodically re-checks `google_accounts.granted_scopes`. Dashboard renders a "Grant Google Health scopes" CTA. |
| Refresh token revoked / scope downgraded | Mark the Google account's `status = 'revoked'` via the existing pipeline; emit a dashboard health event directing the owner to re-consent. Do NOT crash-loop. |
| 429 rate limit | Sleep until reset, then resume. Warning metric. |
| Google Health API 5xx | Exponential backoff (5s → 5min) with jitter. Heartbeat continues. |
| Device not syncing | Not a connector failure. The connector keeps polling; stale data simply means no new events. The Health butler may surface gaps during scheduled jobs. |
| Test-mode refresh token 7-day expiry | Dashboard surfaces a "Re-consent Google Health" banner starting 48 hours before expiry. Same UI pattern as expired OAuth elsewhere. |

## Risks / Trade-offs

- **Restricted-scope verification is an unknown.** If Google's review bar rejects self-hosted single-user applications as non-viable "apps," the project has no production-mode path and every downstream user operates in test mode indefinitely. This is survivable (test mode works, with friction) but unpleasant. Verification should be attempted early to surface the outcome.
- **API is young.** `https://health.googleapis.com/v4/...` is brand-new as of the 2026 migration. Endpoint drift, data type additions, and breaking changes between now and GA are likely. The connector's ingestion handler should be defensive against unexpected fields; the predicate mapping in the Health butler should be small enough that schema drift is an afternoon fix, not a rewrite.
- **Reconciled Stream semantics.** Consuming the reconciled stream means trusting Google's multi-source conflict resolution. For a user with multiple wearables, this is strictly better than per-source ingestion. For a user with only one device, the reconciled stream equals the single-source stream and the decision is inert. Either way, no downside.
- **Health data sensitivity.** Sleep/HR/HRV timeseries are medically-adjacent. No new `craft-and-care` standard is introduced; `security-and-secrets.md` already declares this class of data sensitive. Reviewers must treat Google-Health-touching PRs under the "security review triggers" list.
- **Timeline pressure.** Google's "launch by end of May 2026" guidance is advisory, not a hard deadline, because this is a net-new integration (not a migration). If the change slips to Q3 2026 it still works — the legacy API turndown is simply not relevant to us.

## Migration Plan

No data migration. This is a net-new integration.

Deployment sequence:

1. Ship connector + module + OAuth scope additions + docker-compose service in one change.
2. Owner re-consents their Google account via the dashboard, granting Google Health scopes.
3. Connector backfills the last 30 days of daily summaries on first successful scope check.
4. Steady-state polling resumes.

## Open Questions

- **Actual Google Health API rate limits** — not documented in the migration guide. Discovered during §5.5 implementation and captured in the connector spec + `research-notes.md` at that point; not a blocker on the change as a whole.
- **Exact endpoint paths for each data type bundle** — the migration guide shows `/v4/users/me/profile` but not the full per-type surface. Resolved during §4/§5 implementation. Spec scenarios are written in terms of "the connector fetches sleep data via the Google Health API" rather than specific paths so they stay resilient as the surface firms up.
- **Does Google Health return an equivalent of Fitbit's `activities/active-zone-minutes` in the same data shape?** The legacy Fitbit response had well-documented fields; the Google Health consolidation renames some of them. Direct impact on `active_minutes_daily` metadata.
- **Should intraday timeseries land as facts, or be fetched on demand only?** Current preference in D5: on-demand only. Re-evaluate if insight generation needs pre-indexed intraday data.
- **Verification package scope.** If we pursue production-mode verification for the canonical Butlers OAuth client, the privacy policy + demo video + security questionnaire are real work. Tracked as a deliverable but explicitly non-blocking for development.
