## 1. Trusted Finance provenance

- [x] 1.1 Reserve server-only provenance on trusted Gmail and owner transaction/subscription entry
  paths; preserve exact Gmail `source_endpoint_identity` and reject or ignore caller attempts to
  assert either producer or endpoint authority.
- [x] 1.2 Preserve trusted provenance through bulk, deduplication, split, correction, and
  subscription upsert paths without upgrading legacy/defaulted values.
- [x] 1.3 Derive each recurring group's complete contributing producer/endpoint set and resolve
  exactly one supported pair or `unknown`; two Gmail endpoints are mixed ownership.
- [x] 1.4 Keep the registered `track_subscription_fact` property-fact writer outside current
  dedicated-table recurrence readers; if later adopted, apply the same reserved attestation or
  classify it unmeasurable.

## 2. Shared RFC 0029 endpoint prerequisite — continued bu-8cdl1.3 ownership

- [x] 2.1 Extend connector measurability and persistence to require the
  exact `(connector_type, endpoint_identity)` pair; migrate the shared schema/helper/API with
  `producer_endpoint_identity` without weakening producer-role RLS.
- [x] 2.2 Transition existing Health call sites and rows: preserve null
  endpoints for `owner`, require server-proven endpoints for connector rows, classify unprovable
  legacy rows unmeasurable, and provide no guessed endpoint or type-only compatibility fallback.
- [x] 2.3 Add migrated-PostgreSQL schema/helper/Health tests plus the
  dead-endpoint/healthy-sibling matrix in both row orders.

## 3. Finance runtime adoption after the shared prerequisite

- [x] 3.1 Persist `finance:recurrence:{recurring-group-id}` and
  `finance:subscription-renewal:{subscription-id}` through the shared expected-signals helper,
  carrying required `producer_endpoint_identity` for connector sources.
- [x] 3.2 Keep SimpleFIN and all unsupported/mixed/unprovable sources unmeasurable until an
  independently approved producer-liveness contract exists.
- [x] 3.3 Keep declared renewal dates distinct from expected transaction observations and preserve
  the current forward-looking reminder path.

## 4. Consumers and presentation

- [x] 4.1 Expose Finance recurrence measurability without direct cross-schema reads or inferred
  payment-state fields.
- [x] 4.2 Ensure APIs, subscription audit, the Finance tab, and proactive output never turn
  unmeasurable or absent-without-policy into missed-renewal, payment, cancellation, paused, or
  stopped wording.

## 5. Verification

- [x] 5.1 Add migrated-PostgreSQL tests that kill the exact Gmail endpoint after
  `next_expected_date` while a sibling endpoint stays healthy, in both row orders, and prove
  `unmeasurable` with no candidate or owner-behavior wording.
- [x] 5.2 Test stale, dead/offline, degraded, paused, missing, unreadable, mixed, SimpleFIN,
  manual/import, source-message-only, backfill, split, and legacy provenance.
- [x] 5.3 Prove healthy/current Gmail and attested owner sources produce the RFC 0029 state while
  no unapproved missed-recurrence candidate is emitted.
- [x] 5.4 Prove existing forward-looking annual renewal and predicted-bill policies retain their
  current windows, categories, priorities, deduplication, cooldown, expiry, and wording.
- [x] 5.5 Keep an executable tool-registration guard for `track_subscription_fact` and its
  outside-current-inputs classification so a new reader cannot silently create authority.
