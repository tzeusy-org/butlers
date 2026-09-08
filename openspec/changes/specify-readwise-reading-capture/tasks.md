## 1. Owner execution gate (prerequisite to every task below)

- [ ] 1.1 Confirm the owner has provisioned a Readwise subscription/access token and given
  explicit consent to read their highlight content, per this proposal's "Reserved for Owner
  Review". No task below may start against a real token before this is satisfied; local
  development/tests use fixture data only.
- [ ] 1.2 Re-verify the Readwise API facts in `design.md` (endpoint shape, rate limits, pagination
  fields) against current official docs before implementing — this draft's evidence is a
  point-in-time fetch (2026-09-09).

## 2. RFC amendments (implementation-time, per D10)

- [ ] 2.1 Amend `about/legends-and-lore/rfcs/0003-switchboard-routing-and-ingestion.md` with a new
  numbered amendment registering the `reading`/`readwise` canonical channel/provider pairing,
  following the existing Amendment 1/2 format (Summary / Changes made / Backward compatibility).
- [ ] 2.2 Amend `about/legends-and-lore/rfcs/0004-identity-and-contact-resolution.md`'s registered
  `info_type` table with `readwise_token` (`secured = yes`), following Amendment 2/3's format.
- [ ] 2.3 Update `roster/switchboard/tools/routing/contracts.py` (`SourceChannel`,
  `SourceProvider`, `_ALLOWED_PROVIDERS_BY_CHANNEL`) to make the pairing real, with a focused
  contract test asserting the new pairing validates and no existing pairing is affected.

## 3. Credential and evidence schema

- [ ] 3.1 Add `resolve_owner_entity_info(pool, "readwise_token")` wiring, mirroring the existing
  Steam/Spotify owner-entity credential resolution helpers, with a failing-first unit test for
  absent/present/invalid-shape credential rows.
- [ ] 3.2 Immediately before authoring the migration, fetch the target branch and allocate the next
  core Alembic revision (after `core_222_entity_graph_edges_concierge_grant.py` at drafting time —
  re-check the actual head at implementation time; do not reserve a number here) for
  `connectors.readwise_highlights`: guarded `IF NOT EXISTS` table/indexes, conditional
  `connector_writer` DML grant, conditional read-only grant to the adapter's future consuming
  role(s). Add upgrade/downgrade integration tests against real PostgreSQL (absent-role guard,
  grant shape, downgrade drops cleanly).
- [ ] 3.3 Allocate the next Switchboard migration (after `033_dead_letter_unanswerable_category.py`
  at drafting time) seeding the global `readwise:highlight:` `metadata_only` substring policy rule,
  mirroring `030_switchboard_spotify_spoken_metadata_only.py`. Add a behavior test proving the rule
  cannot spawn or route a butler session while other channels remain pass-through (mirrors
  `roster/switchboard/tests/test_spotify_spoken_policy_bypass.py`).

## 4. Connector implementation

- [ ] 4.1 Add failing-first connector tests (suggested path: `tests/connectors/test_readwise.py`)
  covering: token validation success/failure, full backfill on empty cursor, incremental poll with
  `updatedAfter`, in-cycle `pageCursor` pagination drain, watermark advance only after full drain,
  unchanged-highlight dedup no-op, edited-highlight distinct event, equal-timestamp sibling
  identity, reconciliation-poll tombstoning without a new ingest event, 429/`Retry-After` handling,
  5xx backoff, empty-library healthy state, and credential-revocation transition to `error`.
- [ ] 4.2 Implement `src/butlers/connectors/readwise.py` (or the repo's current connector module
  convention at implementation time) satisfying every scenario in
  `specs/connector-readwise/spec.md`, reusing `cursor_store`, the shared `ConnectorMetrics`,
  `IngestionPolicyEvaluator`, and the filtered-event/replay-drain helpers already used by Steam and
  Spotify — no new base-contract abstraction.
- [ ] 4.3 Wire the connector into the deployment/process-supervision path the repo uses for other
  connectors at implementation time (Docker Compose service, `roster/*` registration, or
  equivalent), gated behind credential presence per Scenario "Credential absent at startup".

## 5. Verification and handoff (this drafting change)

- [x] 5.1 Ground every provider-behavior claim in Readwise's official public API documentation
  (fetched 2026-09-09) rather than invented behavior; retire Pocket with official shutdown
  evidence.
- [x] 5.2 Run `openspec validate specify-readwise-reading-capture --strict`,
  `python3 scripts/check_spec_overwrites.py`, `python3 scripts/check_countable_tasks.py`, and
  `make check-guards`; fix any failures before opening the PR.
- [x] 5.3 Confirm no code, migration, credential, dashboard, or runtime file changed in this
  packet — only `about/legends-and-lore/rfcs/0018-...md` and
  `openspec/changes/specify-readwise-reading-capture/**`.
