# Tasks

> All open decisions are recorded as ADRs in `design.md` §Decisions. Tasks below
> are implementation-only. Sequencing constraint: §1 (pairing) must land before
> §2/§3 are exercised end-to-end — unregistered pairings are rejected at
> `IngestSourceV1` validation.

## 1. Channel/Provider Pairing (Switchboard + RFC)

- [ ] 1.1 Expand `_ALLOWED_PROVIDERS_BY_CHANNEL["wellness"]` to
  `frozenset({"google_health", "home_assistant"})` in
  `roster/switchboard/tools/routing/contracts.py`.
- [ ] 1.2 Amend RFC 0003 (`about/legends-and-lore/rfcs/`) with the
  `wellness/home_assistant` pairing, following the existing "Amendments
  Applied" pattern (summary + backward-compatibility note, referencing this
  change).
- [ ] 1.3 Contract tests: `wellness/home_assistant` accepted;
  `wellness/google_health` unchanged; `wellness/<other>` still rejected with
  `invalid_source_provider`.
- [ ] 1.4 Integration test: a `wellness/home_assistant` envelope traverses the
  sw_007 rule onto the policy-bypass path (no LLM spawn), mirroring
  `roster/switchboard/tests/test_wellness_policy_bypass.py`.

## 2. HA Connector Classifier + Dual Emission

- [ ] 2.1 Implement `WellnessClassifier` (rule table per design ADR-1) in the
  HA connector package; pure function of
  `(entity_id, device_class, unit_of_measurement, attributes, state)` →
  `metric | None`. Non-numeric states return `None`.
- [ ] 2.2 Wire config per ADR-2: `HA_WELLNESS_PROMOTION_ENABLED`,
  `HA_WELLNESS_RULES_EXTRA` (JSON, validated at startup with a clear error on
  malformed input), `HA_WELLNESS_ENTITY_DENYLIST`; extend
  `HAConnectorConfig.from_env` and spec §10 env-var docs.
- [ ] 2.3 Add `build_wellness_envelope()` to
  `src/butlers/connectors/home_assistant_envelope.py` producing the
  `payload.raw.wellness_measurement` shape from design ADR-4 (same
  `external_event_id` as the `home_assistant` emission; `valid_at` =
  `time_fired`; human-readable `normalized_text`).
- [ ] 2.4 Hook classification into `_dispatch()` after the three-layer filter
  verdict, for BOTH the WebSocket path and the REST polling fallback path;
  emit `home_assistant` first, then `wellness`; advance the checkpoint once,
  after both submissions (transient secondary failure leaves the checkpoint
  un-advanced — replay dedupes per channel).
- [ ] 2.5 Prometheus counters for classifier outcomes
  (promoted/skipped-non-numeric/denylisted, labeled by metric) and
  channel-labeled submission metrics.
- [ ] 2.6 Unit tests: default rule table coverage (systolic/diastolic mmHg,
  weight, bpm, glucose, steps), ambient exclusions (room temperature NOT
  promoted), denylist, rules-extra extension, non-numeric skip, disabled
  flag, dual-emission ordering + single checkpoint advance, replay dedup.

## 3. Health Butler Provider Dispatch + Idempotency

- [ ] 3.1 Refactor `translate_wellness_envelope`
  (`roster/health/tools/wellness_ingest.py`) into a provider dispatch:
  `google_health` arm byte-for-byte preserved (existing tests must pass
  unmodified); `home_assistant` arm new; unknown providers rejected with a
  labeled `health_wellness_ingest_total` outcome.
- [ ] 3.2 Implement the `home_assistant` translator: validate
  `wellness_measurement` shape (numeric value, `metric`, `valid_at`,
  `source_entity_id`); predicate `measurement_{metric}`; owner entity
  resolution as today; `metadata = {provider, source_entity_id, unit, value}`;
  rejection metrics for malformed payloads.
- [ ] 3.3 Sender validation per design ADR-4: HA arm does NOT consult
  `list_health_scoped_accounts`; acceptance pins provider + payload shape.
  Google arm's validation untouched.
- [ ] 3.4 Provider-agnostic idempotency key per design ADR-5:
  `sha256("wellness|{owner_entity_id}|{scope}|{predicate}|{valid_at_iso}")[:32]`
  passed explicitly to `memory_store_fact`.
- [ ] 3.5 Tests: well-formed HA envelope → one fact with correct
  predicate/valid_at/metadata; duplicate delivery → no-op returning existing
  fact id; same `(predicate, valid_at)` pre-stored under the agnostic key →
  no-op; distinct `valid_at` → two facts; malformed payload variants →
  rejected, no fact; google_health regression suite unchanged.

## 4. End-to-End + Docs

- [ ] 4.1 E2E test (or integration test at the highest practical seam): a
  Withings-shaped `state_changed` event (mmHg systolic entity) entering the HA
  connector produces exactly one `measurement_blood_pressure_systolic` fact in
  the health scope, with no LLM session spawned.
- [ ] 4.2 Update `openspec/specs/connector-home-assistant/spec.md` env-var
  section (§10) and `docs/` connector documentation for the three new knobs.
- [ ] 4.3 Note in `roster/health/AGENTS.md` that HA-promoted measurements
  appear as `measurement_*` facts with `metadata.provider =
  "home_assistant"` (so runtime sessions interpreting trends know the
  provenance field).
