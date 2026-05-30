# Design

## Scope

This change extends the Google Health connector so its identity domain is the *set* of Google accounts in `public.google_accounts` whose `granted_scopes` is a superset of the three `googlehealth.*` URLs, rather than the single `is_primary=true` row. The runtime, ingest envelope shape, and dashboard surface area all flex per-account; the underlying poll engine, scope-restricted token minting, cursor store, and chronicler adapter are unchanged in spirit but plurally instantiated.

## Why now

The Google Health connector landed under a single-owner v1 invariant: at most one Google account, hard-coded to `is_primary=true`. Multi-account Google OAuth (`google-multi-account-oauth`) made it possible for owners to attach more than one Google account, but the Health connector never opted in. The cost surfaces as silent data loss when the upstream Fitbit→Google Health Connect bridge migrates to a non-primary account (which it can do during a Pixel device swap, Fitbit migration, or account-restructuring on Google's side).

The discovery captured in `archive/2026-04-24-google-health-connector/research-notes.md` ("Data-availability discovery — multi-account topology") is the first observed instance. We expect more as Google consolidates Fitbit and Health Connect cloud sync.

## Per-account state model

```
Connector
├── _accounts: dict[account_uuid, OwnerContext]
│   └── OwnerContext = {email, entity_id, refresh_token (via shared pipeline), cached_access_token, token_expires_at}
└── _resources: dict[(account_uuid, resource), ResourceState]
    └── ResourceState = {next_poll_monotonic, last_cursor, backfill_done, ...}
```

`_resolve_owner_and_scopes` becomes:

```python
async def _resolve_owner_and_scopes(self):
    accounts = await list_health_scoped_accounts(self._shared_pool)
    new_uuids = {a.id for a in accounts}
    cur_uuids = set(self._accounts)
    # Tear down accounts that lost scopes or were deleted
    for gone in cur_uuids - new_uuids:
        await self._teardown_account(gone)
    # Spin up new accounts
    for added in new_uuids - cur_uuids:
        await self._spinup_account(next(a for a in accounts if a.id == added))
    # Refresh the existing ones in case email or status changed
    for known in cur_uuids & new_uuids:
        self._accounts[known].refresh_from(next(a for a in accounts if a.id == known))
```

Adds and removals at runtime are observable in the heartbeat (separate row per account in `switchboard.connector_registry`).

## Token caching

Per-account access-token cache: `OwnerContext.cached_access_token + token_expires_at`. The scope-restricted mint path (added by the prior fix) is invoked per account with the account's own refresh token. There is no shared access-token cache across accounts — the API rejects cross-account use.

## Cursor key shape

Existing key: `(connector_type, endpoint_identity)` where `endpoint_identity = google_health:user:<email>:<resource>`.

New key (proposed): `(connector_type, endpoint_identity)` where `endpoint_identity = google_health:user:<email>:<account_uuid>:<resource>`.

The `<account_uuid>` segment is included even when email is unique because an owner can re-add the same Google account (rotation, force_consent) and get a new `google_accounts.id` — the cursor must follow the account row, not the email.

**Migration:** a one-shot SQL UPDATE prefixes existing `google_health:user:<email>:<resource>` cursors with the matching `account_uuid` from `public.google_accounts.email`. Idempotent; safe to re-run.

## Envelope identity

Existing: `event.external_event_id = "google_health:<resource>:<YYYY-MM-DD>"` (or `:sleep_session:<id>`).

The collision risk is real if two accounts hold overlapping date-keyed summaries. Two options:

1. **Prefix with email/uuid in the key.** `google_health:<email>:<resource>:<YYYY-MM-DD>`. Breaking change for downstream consumers (chronicler adapter, ingestion_events.external_event_id rows). Requires backfill UPDATE for the existing 3 historical rows.
2. **Keep the key, dedupe on collision.** Accept the lossiness when two accounts report the same data for the same date (rare; resolves to "last write wins"). Smaller change but lossier.

We propose option 1 — explicit > implicit, and the backfill is trivial (3 rows today).

## Dashboard surface

Today `/api/connectors/google-health/status` returns one `GoogleHealthStatusResponse`. With multiple accounts it must return either:

- A list of per-account responses + a top-level summary (`primary_account_email`, `total_sleep_sessions_7d`, etc.), OR
- A single response with `accounts: list[AccountStatus]` nested inside.

The latter is more future-proof; the former is closer to the existing model. Either way it is a breaking schema change for the dashboard card. We propose nesting `accounts: list[AccountStatus]` inside the existing response and adding a `primary_account_email: str | None` summary field; the dashboard renders one card per account with the existing widgets.

## Out of scope

- Per-account poll-interval overrides. All accounts share the connector-level `poll_intervals` config in v1.
- Cross-account dedup of dataPoints. If the same Fitbit syncs to two Google accounts (rare), we ingest both copies; chronicler-side adapter can dedup later if needed.
- Re-keying the wellness routing target. The Health butler is still the single recipient; envelopes from all accounts route to it.

## Risks

- **Quota.** Each account is its own quota subject against `health.googleapis.com`. With current 30-min sleep / activity / HR intervals × 2 accounts, we issue ~96 calls/day per account, well under documented quotas.
- **Token mint storms.** Per-account access tokens expire on their own 60-min cycle; staggered mint by account index avoids a thundering herd.
- **Connector restart behaviour.** All cached access tokens are dropped on restart and re-minted on first poll per account. Mint failures for one account must not block polls for another.

## Test surface

- Unit: `list_health_scoped_accounts` filters by status + scope superset.
- Unit: connector teardown on scope revocation for one account does not affect another's polls or cursors.
- Integration: simulate 2 accounts with `_StubTransport`; assert two `connector_registry` rows and per-account envelopes.
- Migration: cursor backfill leaves existing single-account installs unchanged in observable behaviour.

## Decisions

These were left open in earlier drafts of `tasks.md`. They are now closed so bead workers have no open questions.

### ADR-1: Dashboard schema change ships in this same change

**Decision.** The `/api/connectors/google-health/status` response gains a `accounts: list[AccountStatus]` field in this change. Top-level summary fields stay (computed as worst-of across accounts) so existing dashboard cards do not break.

**Rationale.** Versioning the endpoint (`/v2/status`) is overkill for an internal dashboard with one consumer (our own frontend). A nested `accounts` field is additive on the response and the worst-of summary keeps single-account installs visually identical. The frontend render update lives in a separately-tracked frontend bead but the API change ships here.

**Alternatives considered.** (a) Bump to `/v2/status` — rejected, dashboard is internal; (b) Keep one summary object — rejected, silently hides per-account state.

### ADR-2: External-event-identity key shape

**Decision.** `event.external_event_id` becomes `google_health:<email>:<resource>:<id>` and `control.idempotency_key` follows the same shape. The 3 existing rows are migrated by an Alembic step.

**Rationale.** Two accounts can legitimately report the same `<resource>:<date>` (e.g. both Pixel and Fitbit syncing steps to two Google accounts). Without prefixing, the ingest pipeline would dedupe across accounts and silently drop one. Explicit > implicit. 3-row backfill is trivial.

**Alternatives considered.** (a) Keep key, dedup on collision — rejected, silently lossy; (b) Use `account_uuid` instead of email in the key — rejected, email is already the canonical `google_user_id` in envelopes (`source.endpoint_identity = google_health:user:<email>`), keeping symmetric is cheaper.

### ADR-3: Cursor-key SQL migration vs. lazy rewrite

**Decision.** A one-shot SQL migration rewrites existing `cursor_store` rows on deploy. No lazy rewrite path in code.

**Rationale.** Cursor reads happen once per connector start per resource — there is no hot read path. A pre-deploy migration is simpler to reason about than a fallback read path that has to know about the old shape. The migration is idempotent (re-running is a no-op) and reversible (strip the `:<account_uuid>:` segment).

### ADR-4: Per-account credential resolution path

**Decision.** Per-account refresh tokens are read directly via `google_credentials._resolve_entity_refresh_token(pool, companion_entity_id)` in the connector, NOT routed through `resolve_owner_entity_info()`.

**Rationale.** `resolve_owner_entity_info()` is hard-coded to "the primary owner account" (see `src/butlers/google_credentials.py` and `openspec/specs/core-credentials/spec.md:62, 110, 117-135`). Generalising it to take an account selector would touch every Google connector; we'd rather keep that helper stable and have this connector own its multi-account read pattern. The underlying `_resolve_entity_refresh_token` is already public-by-convention (used by multiple Google connectors).

**Alternatives considered.** Add a paired delta to `core-credentials` exposing `resolve_account_refresh_token(account_uuid)` — deferred. If a second connector needs per-account refresh tokens later, promote this read pattern into the shared helper at that time. Pre-doing it for one consumer is premature abstraction.
