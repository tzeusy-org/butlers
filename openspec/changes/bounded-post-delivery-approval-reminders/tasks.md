## 1. Exact successor artifact (`bu-17noe`)

- [x] 1.1 Record settled Direction B without reopening RFC 0023 adoption or its
  separately released repository implementation.
- [x] 1.2 Trace the real channel registry/order, RFC 0023 handoff states and
  recovery constants, `ActionStatus`, owner-activity writers, and proactive
  insight denominator/numerator writers before selecting policy.
- [x] 1.3 Define exact acknowledgement evidence, 24-hour evidence freshness,
  registered-channel eligibility/order, 4-hour/24-hour reminder slots, two-
  ordinal lifetime bound, missing/stale evidence behavior, safe hold,
  homecoming deduplication, and disengagement isolation.
- [x] 1.4 Preserve canonical expiry, RFC 0021 quiet hours and burst behavior,
  RFC 0023 same-key recovery, and ambiguous-effect no-resend.
- [x] 1.5 Keep this change specification-only with `Tests: +0 ~0 -0` and no
  implementation, migration, provider use, notification, deployment, runtime,
  rollout, activation, or live effect.

## 2. Draft verification and independent review

- [x] 2.1 Run strict validation for this named OpenSpec change.
- [x] 2.2 Run the whole-body overwrite/same-requirement scan without changing
  the overwrite baseline; confirm this successor adds no competing `MODIFIED`
  block while RFC 0023's active delta remains unarchived.
- [x] 2.3 Run countable-task, documentation, and repository guards plus
  `git diff --check`.
- [ ] 2.4 Obtain independent exact-head security review covering actor
  derivation, content-blind storage, channel allowlisting, ambiguity
  non-laundering, and absence of action decision/execution authority.
- [ ] 2.5 Obtain independent exact-head product review covering cadence/count,
  channel order, evidence freshness, safe hold, homecoming bounds, and
  disengagement semantics; resolve every finding and record the reviewed
  commit/digest.

## 3. Exact owner adoption gate

- [ ] 3.1 Obtain owner adoption of the exact independently reviewed RFC 0035
  and OpenSpec artifact. Direction B, RFC 0023 adoption, draft merge, review,
  and prior implementation release do not satisfy this gate.

## 4. Future implementation after adoption

- [ ] 4.1 First archive and sync the implemented RFC 0023 OpenSpec change, then
  rebuild successor deltas against the resulting current baselines. Preserve
  every unrelated whole requirement clause and scenario.
- [ ] 4.2 Add append-only action/presentation acknowledgement evidence,
  attention episode/safe-hold state, reminder ordinals, and owner-presence
  epochs with content-blind fields and closed reason vocabularies.
- [ ] 4.3 Add deterministic reminder admission with the exact channel
  intersection/order, +4h/+24h schedule, two-ordinal lifetime budget, expiry,
  quiet-hours, burst-cohort, defer, replay, and concurrency fences.
- [ ] 4.4 Add the explicit authenticated attention-acknowledgement surface and
  truthful reachability/safe-hold projection without caller-asserted actor,
  provider identifiers, callback material, raw payloads, or action mutation.
- [ ] 4.5 Add one bounded homecoming summary per owner-presence epoch, including
  ambiguity exclusion, 20-row/7-day limits, expired non-actionability, quiet
  hours, and replay-safe handoff.
- [ ] 4.6 Preserve the proactive-insight writer set and add a contract guard
  proving approval attention paths never write its denominator/numerator.

## 5. Future verification and activation gates

- [ ] 5.1 Add behavior-executing success, failure, replay, and concurrency
  coverage for every scenario in `approval-attention-reminders/spec.md`, using
  real PostgreSQL for locks, unique identities, races, and writer isolation.
- [ ] 5.2 Run exact-node/file tests, source-writer guards, real-PostgreSQL
  contract/integration lanes, strict OpenSpec, whole-body overwrite,
  countable-task, repository guards, and terminal exact-head hosted CI. Record
  the future implementation's actual `Tests: +a ~b -c` separately.
- [ ] 5.3 Obtain fresh exact-head security/product review after implementation.
- [ ] 5.4 Keep migrations, rollout flags, deployment, runtime activation,
  channel/provider use, and live sends disabled until each receives its own
  explicit authority. Archive only after implementation is reviewed and
  merged; archival does not authorize activation.
