# Tasks — switchboard-rule-promotion

The tasks below are the sequenced implementation beads proposed in
`docs/plans/2026-07-06-switchboard-rule-promotion-design.md`. Beads 1-3 and 5
(tasks 1, 2, 3, 5 below) shipped 2026-07-05/06 (bu-aga08 -> PR #2975,
bu-h26o9 -> PR #2992, bu-wuwy9 -> PR #2999, bu-x55k3 -> PR #3015). Bead 4
(section 4, the approvals surface) SHIPPED 2026-07-13 (bu-o62bc -> PR #3213);
the owner auto-apply-vs-confirm decision (bu-4pq0s) resolved to auto-apply, so
the batched "bulk-confirm" endpoint (task 4.3) was superseded by auto-mint +
reversible toggle rather than built. Beads 6 (metrics tile, task 6.1) and 7
(sender-normalization de-dup, task 7.1) remain UNBUILT in code as of the
2026-07-14 true-up (verified: no rule-promotion-stats endpoint; verdict_log.py
still carries the local sender regex). DO NOT archive this change until tasks
6.1 and 7.1 close.

## 1. Verdict mining substrate

- [x] 1.1 Migration: `switchboard.routing_verdict_log` (columns per design doc
      §1: `ingestion_event_id`, `sender_key`, `source_channel`,
      `verdict_source`, `verdict_action`, `verdict_target`, `matched_rule_id`,
      `session_id`, `decided_at`; indexes on `(sender_key, source_channel,
      decided_at DESC)` and a partial index on `verdict_source = 'llm'`)
- [x] 1.2 Write hook at the rule-bypass sites in `MessagePipeline.process()`
      (`pipeline.py:1875` route_to, `:2041` skip, `:2074` metadata_only) —
      `verdict_source='rule'`
- [x] 1.3 Write hook at the LLM verdict resolution site
      (`_extract_routed_butlers()` consumption at `pipeline.py:2711`, plus the
      LLM-driven skip/metadata_only fallback paths) — `verdict_source='llm'`
- [x] 1.4 Write hook at the pinned-target bypass (PR #2896's
      `control.pinned_target`) — `verdict_source='pinned'`, excluded from
      promotion mining
- [x] 1.5 Local sender-address normalization (reuse
      `ingestion_policy._extract_emails()` logic) into `sender_key`,
      independent of bu-qeaou's landing timeline

## 2. Suggestion schema

- [x] 2.1 Migration: `switchboard.rule_promotion_suggestions` (columns per
      design doc §2)
- [x] 2.2 Migration: `ingestion_rules.promoted_from_suggestion_id` nullable FK
      (additive, no breaking change); document `created_by='promotion'`
      convention value

## 3. Promotion trigger job

- [x] 3.1 Periodic scan grouped by `(sender_key, source_channel)`, skipping
      senders already covered by an enabled rule or an existing
      `pending_review` suggestion (bump `evidence_count` instead)
- [x] 3.2 N-consecutive-same-verdict detection (default N=3) over
      `verdict_source='llm'` rows only
- [x] 3.3 Evidence-quality gate: evidence must span >=2 distinct calendar days
      (guards against single-burst false positives, per design doc D5)
- [x] 3.4 `is_clearly_automated` classifier reusing existing bulk-mail header
      signals (`List-Unsubscribe`, `Precedence`, `Auto-Submitted`,
      `noreply`/`no-reply`/`notifications`/`alerts` local-part prefixes)
- [x] 3.5 Suggestion upsert / evidence-count bump logic

## 4. Approvals-surface integration

- [x] 4.0 Owner confirms or overrides the auto-apply-vs-owner-confirm decision
      (design doc §3 / design.md D4) before this task starts (bu-4pq0s resolved
      to auto-apply; `rule_promotion_apply.AUTO_APPLY_ACTIONS`/`AUTO_APPLY_ACTOR`
      at `roster/switchboard/api/router.py:3626-3643`)
- [x] 4.1 `GET /api/switchboard/rule-promotion-suggestions` (status/type
      filters, pagination) (`router.py:3605`)
- [x] 4.2 `POST /api/switchboard/rule-promotion-suggestions/{id}/confirm`
      (`router.py:3687`)
- [x] 4.3 `POST /api/switchboard/rule-promotion-suggestions/bulk-confirm`
      (batched skip/metadata_only path). SUPERSEDED by the auto-apply decision:
      clearly-automated skip/metadata_only rules auto-mint and surface as
      reversible `auto_applied` cards (`.../{id}/rule-enabled` toggle,
      `router.py:3784`); no bulk-confirm endpoint is built by design
- [x] 4.4 `POST /api/switchboard/rule-promotion-suggestions/{id}/dismiss`
      (`router.py:3728`)
- [x] 4.5 Dashboard banner: individual `route_to` suggestion cards with scope
      description (`rule-promotion-banner.tsx` `PendingCard`); the batched
      "Confirm all N automated senders" affordance was replaced by informational
      `AutoAppliedCard`s per the 4.3 auto-apply pivot
- [x] 4.6 Audit events for suggestion lifecycle transitions (`emit_dashboard_audit`
      at `router.py:3715`/`3771`/`3822`)

## 5. Demotion via spot-check

- [x] 5.1 `PolicyDecision.spot_check: bool` field in
      `src/butlers/ingestion_policy.py`; set on a 1-in-K die roll when a
      matched rule has `created_by='promotion'`
- [x] 5.2 `pipeline.py`: route spot-checked events through normal LLM
      classification instead of the bypass; log the comparison as
      `verdict_source='spot_check'`
- [x] 5.3 Rolling per-rule agreement scoring (last 20 spot-checks, default
      <90% threshold)
- [x] 5.4 Demotion-suggestion creation on sustained disagreement, surfaced via
      the Task 4 dashboard surface; owner-confirmed revoke, never auto-disable
      (creation shipped; the Task 4 surface that confirms/revokes it has not)

## 6. Rule-promotion metrics

- [ ] 6.1 Stats endpoint/dashboard tile: sessions avoided, pending-suggestion
      count, estimated LLM-session savings

## 7. De-duplicate sender normalization against bu-qeaou (low priority)

- [ ] 7.1 Once bu-qeaou ships its normalized sender column on
      `public.ingestion_events`, switch `sender_key` derivation to read it
      directly; delete the local regex duplication from Task 1.5
