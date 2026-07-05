# Tasks — switchboard-rule-promotion

This change is design-only; the tasks below are the sequenced implementation
beads proposed in `docs/plans/2026-07-06-switchboard-rule-promotion-design.md`,
recorded here for `openspec` completeness tracking. The coordinator files these
as separate beads after owner review of the design.

## 1. Verdict mining substrate

- [ ] 1.1 Migration: `switchboard.routing_verdict_log` (columns per design doc
      §1: `ingestion_event_id`, `sender_key`, `source_channel`,
      `verdict_source`, `verdict_action`, `verdict_target`, `matched_rule_id`,
      `session_id`, `decided_at`; indexes on `(sender_key, source_channel,
      decided_at DESC)` and a partial index on `verdict_source = 'llm'`)
- [ ] 1.2 Write hook at the rule-bypass sites in `MessagePipeline.process()`
      (`pipeline.py:1875` route_to, `:2041` skip, `:2074` metadata_only) —
      `verdict_source='rule'`
- [ ] 1.3 Write hook at the LLM verdict resolution site
      (`_extract_routed_butlers()` consumption at `pipeline.py:2711`, plus the
      LLM-driven skip/metadata_only fallback paths) — `verdict_source='llm'`
- [ ] 1.4 Write hook at the pinned-target bypass (PR #2896's
      `control.pinned_target`) — `verdict_source='pinned'`, excluded from
      promotion mining
- [ ] 1.5 Local sender-address normalization (reuse
      `ingestion_policy._extract_emails()` logic) into `sender_key`,
      independent of bu-qeaou's landing timeline

## 2. Suggestion schema

- [ ] 2.1 Migration: `switchboard.rule_promotion_suggestions` (columns per
      design doc §2)
- [ ] 2.2 Migration: `ingestion_rules.promoted_from_suggestion_id` nullable FK
      (additive, no breaking change); document `created_by='promotion'`
      convention value

## 3. Promotion trigger job

- [ ] 3.1 Periodic scan grouped by `(sender_key, source_channel)`, skipping
      senders already covered by an enabled rule or an existing
      `pending_review` suggestion (bump `evidence_count` instead)
- [ ] 3.2 N-consecutive-same-verdict detection (default N=3) over
      `verdict_source='llm'` rows only
- [ ] 3.3 Evidence-quality gate: evidence must span >=2 distinct calendar days
      (guards against single-burst false positives, per design doc D5)
- [ ] 3.4 `is_clearly_automated` classifier reusing existing bulk-mail header
      signals (`List-Unsubscribe`, `Precedence`, `Auto-Submitted`,
      `noreply`/`no-reply`/`notifications`/`alerts` local-part prefixes)
- [ ] 3.5 Suggestion upsert / evidence-count bump logic

## 4. Approvals-surface integration

- [ ] 4.0 Owner confirms or overrides the auto-apply-vs-owner-confirm decision
      (design doc §3 / design.md D4) before this task starts
- [ ] 4.1 `GET /api/switchboard/rule-promotion-suggestions` (status/type
      filters, pagination)
- [ ] 4.2 `POST /api/switchboard/rule-promotion-suggestions/{id}/confirm`
- [ ] 4.3 `POST /api/switchboard/rule-promotion-suggestions/bulk-confirm`
      (batched skip/metadata_only path)
- [ ] 4.4 `POST /api/switchboard/rule-promotion-suggestions/{id}/dismiss`
- [ ] 4.5 Dashboard banner: individual `route_to` suggestion cards with scope
      description; batched "Confirm all N automated senders" affordance for
      skip/metadata_only
- [ ] 4.6 Audit events for suggestion lifecycle transitions

## 5. Demotion via spot-check

- [ ] 5.1 `PolicyDecision.spot_check: bool` field in
      `src/butlers/ingestion_policy.py`; set on a 1-in-K die roll when a
      matched rule has `created_by='promotion'`
- [ ] 5.2 `pipeline.py`: route spot-checked events through normal LLM
      classification instead of the bypass; log the comparison as
      `verdict_source='spot_check'`
- [ ] 5.3 Rolling per-rule agreement scoring (last 20 spot-checks, default
      <90% threshold)
- [ ] 5.4 Demotion-suggestion creation on sustained disagreement, surfaced via
      the Task 4 dashboard surface; owner-confirmed revoke, never auto-disable

## 6. Rule-promotion metrics

- [ ] 6.1 Stats endpoint/dashboard tile: sessions avoided, pending-suggestion
      count, estimated LLM-session savings

## 7. De-duplicate sender normalization against bu-qeaou (low priority)

- [ ] 7.1 Once bu-qeaou ships its normalized sender column on
      `public.ingestion_events`, switch `sender_key` derivation to read it
      directly; delete the local regex duplication from Task 1.5
