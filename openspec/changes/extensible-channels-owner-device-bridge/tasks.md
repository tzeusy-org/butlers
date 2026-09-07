## 1. Authority and Baseline Gates

- [ ] 1.1 Obtain independent exact-head semantic and security review of RFC 0033 and all six capability deltas; correct every blocking finding before presenting an owner decision.
- [ ] 1.2 Record exact owner approval of the reviewed artifact, including provider, account/number capability, public ingress route, call-event subset, SMS privacy profile, finite retention period, and continued SMS-egress disposition.
- [ ] 1.3 Refresh the cited official provider contracts and obtain account-specific confirmation of regional number capability, registration requirements, webhook/auth availability, and any idempotency or reconciliation primitive; keep the owner-device slice blocked if any required fact is unproven.
- [ ] 1.4 Reconcile the exact landed state of PR #4046 (`bu-poven`), PR #3960 (`bu-7exe4.2`), RFC 0023, and their active deltas; rebuild any colliding block against the then-current baseline without importing unapproved behavior.

## 2. Catalog Representation and Grants

- [ ] 2.1 Add an additive core migration for `public.source_channel_catalog` with bounded token checks, exact pair uniqueness, enablement, timestamps, and migration-only write ownership.
- [ ] 2.2 Seed the exact 20 legacy pairs and add a fail-closed parity check that compares the full static and catalog sets before propagation or enforcement.
- [ ] 2.3 Add real-PostgreSQL migration tests proving complete/idempotent seeding, constraints, Switchboard read access, denial of connector/butler/Messenger/dashboard runtime writes, and non-destructive downgrade refusal where catalog-only dependencies remain.

## 3. Catalog Validation, Cache, and Read Surface

- [ ] 3.1 Introduce the bounded source token type and one Switchboard-owned semantic validator that reads immutable complete catalog snapshots before deduplication or persistence.
- [ ] 3.2 Implement 60-second refresh, 300-second maximum staleness, atomic whole-snapshot swap, and safe error categories without payload or identity echo.
- [ ] 3.3 Extend `tests/core/test_routing_contracts.py` with malformed, unknown, disabled, mismatched, known-cached, unseen-during-failure, stale-cache, partial-refresh, and concurrent-snapshot behavior.
- [ ] 3.4 Extend `tests/integration/test_connector_conformance.py` so every existing connector pair matches the seed and a catalog-only regression pair passes after enforcement without a Literal or pair-matrix edit.
- [ ] 3.5 Add `GET /api/ingestion/source-catalog` and its field-by-field content-blind DTO; test healthy, genuinely empty, and source-unavailable envelopes plus absence of all mutation routes.
- [ ] 3.6 Add the bounded owner read presentation using the Dispatch status/error/focus conventions; test keyboard operation, unavailable-state honesty, and absence of connector health, credentials, identities, bodies, and outbound capability.

## 4. Additive Enforcement and Rollback

- [ ] 4.1 Propagate bounded source tokens through envelope construction, connector conformance, direct dashboard ingress, filters, persistence, identity resolution, and relationship consumers while static/catalog parity remains enforced.
- [ ] 4.2 Run collection and contract tests for every source consumer, then cut semantic source validation to the catalog only and remove static semantic authority without changing `ingest.v1` field meanings.
- [ ] 4.3 Add upgrade/rollback tests proving all legacy pairs preserve behavior, catalog-only pairs are refused by the rollback binary, and historical ingestion rows are neither replayed nor rewritten.

## 5. Source-Aware Interactions and Discord

- [ ] 5.1 Add database-enforced daily interaction identity over entity, source channel, date, direction, and attested source endpoint; preserve one incoming and one outgoing fact per tuple.
- [ ] 5.2 Move grouped message `valid_at` to the deterministic earliest real event time and migrate every interaction writer before retiring hour-offset tables.
- [ ] 5.3 Add Discord to the explicit interaction source map using `discord:<user_id>` `has-handle` resolution and only the shipped bot-token event shape.
- [ ] 5.4 Extend `tests/jobs/test_interaction_sync.py` with Discord resolution, actual Dunbar-input change, repeated run, concurrent run, thirteenth scoring source, two-channel same-day, two-endpoint same-day, unresolved/degraded, and rollback-compatibility cases.
- [ ] 5.5 Extend `tests/connectors/test_discord_user_connector.py` with stable provider message/user/endpoint identity and reconnect replay coverage; assert no OAuth v2 token, scope, consent, or direct-message behavior is introduced.

## 6. Watched Source Profile

- [ ] 6.1 Implement the reusable Watched Source connector profile by composing connector-base filtering, filtered-event flush, replay drain, checkpoint, heartbeat, metrics, rate limits, backoff, and shutdown contracts.
- [ ] 6.2 Add behavior tests for first baseline, restart resume, duplicate provider event, lost Switchboard result with stable retry identity, filtered-event flush failure, checkpoint rollback, and independent per-source backoff.
- [ ] 6.3 Add authenticated-webhook contract tests for valid signature, invalid signature, stale timestamp, wrong account/number, duplicate event, out-of-order event, bounded body, and content-blind failure telemetry.
- [ ] 6.4 Add deactivation/revocation race tests proving no newly observed event crosses the linearized stop boundary and accepted history is not replayed, rewritten, or silently deleted.

## 7. Owner-Device Inbound Bridge

- [ ] 7.1 After Tasks 1.2 and 1.3 only, add the selected provider adapter, exact catalog pair migration, Tier 2 owner credential types, authenticated ingress route, and approved event mapping without adding outbound SMS.
- [ ] 7.2 Implement call-lifecycle metadata with monotonic state handling and no audio, recording, transcription, media fetch, stream, or call-control behavior.
- [ ] 7.3 Implement the approved SMS privacy profile and retention enforcement; test metadata-only omission or content-enabled protected ingestion, expiry, and absence from logs, metrics, status, browser DTOs, and generic audit metadata.
- [ ] 7.4 Add content-blind setup/status/revocation API and UI behavior; test server-derived actor attribution, repeated activation, partial-configuration rollback, immediate pending feedback, keyboard/focus behavior, safe recovery copy, and credential/value non-disclosure.
- [ ] 7.5 Add provider-adapter tests for signed inbound events, stable provider IDs, duplicates, out-of-order callbacks, account/number binding, rate/backoff behavior, revocation, and provider/API partial failures using recorded synthetic fixtures only.

## 8. Outbound SMS Remains Separately Gated

- [ ] 8.1 Preserve and test the current unsupported `notify(channel="sms")` result across catalog and owner-device ingress implementation; prove no provider adapter call, recipient resolution, approval parking, or success record occurs.
- [ ] 8.2 If the owner later elects outbound SMS, author and obtain approval for a separate delta covering adapter registration, reachability, recipients, per-message approval, Messenger defense-in-depth, immutable attempts, provider reconciliation/ambiguity, partial effects, and rollback against the then-current RFC 0023 authority.
- [ ] 8.3 In that separate implementation only, add failure-injection tests for no-adapter/config refusal, unapproved send, pre-handoff safe retry, provider acceptance before timeout, ambiguous result without blind resend, confirmed reconciliation, partial audit failure, restart, and rollback.

## 9. Final Contract and Evidence Pass

- [ ] 9.1 Run strict OpenSpec validation, spec trace authoring/strict checks at their applicable lifecycle stages, overwrite protection, applicable repository guards, link checks, and source-reference verification against the exact implementation head.
- [ ] 9.2 Run the repository test planner and the right-sized targeted migration, contract, API, UI, audit, connector, interaction, concurrency, and rollback suites; use terminal hosted CI as broad exact-head evidence.
- [ ] 9.3 Re-run independent semantic/security review after any correction that changes behavior or trust boundaries, and report only evidence from the exact reviewed head.

This specification does not require a live provider canary or a predicted number of tests. Any
future live provider, credential, public-ingress, deployment, activation, or message-send operation
requires explicit authority at that time.
