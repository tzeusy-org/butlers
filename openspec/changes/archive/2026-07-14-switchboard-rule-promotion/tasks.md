# Tasks — switchboard-rule-promotion

The tasks below are the sequenced implementation beads proposed in
`docs/plans/2026-07-06-switchboard-rule-promotion-design.md`. Beads 1-3 and 5
(tasks 1, 2, 3, 5 below) shipped 2026-07-05/06 (bu-aga08 -> PR #2975,
bu-h26o9 -> PR #2992, bu-wuwy9 -> PR #2999, bu-x55k3 -> PR #3015). Bead 4
(section 4, the approvals surface) SHIPPED 2026-07-13 (bu-o62bc -> PR #3213);
the owner auto-apply-vs-confirm decision (bu-4pq0s) resolved to auto-apply, so
the batched "bulk-confirm" endpoint (task 4.3) was superseded by auto-mint +
reversible toggle rather than built. Bead 6 (metrics tile, task 6.1) SHIPPED
2026-07-14 (bu-hb61f -> PR #3223: stats endpoint + tile). Bead 7
(sender-normalization de-dup, task 7.1) CLOSED 2026-07-14 (bu-jxsew -> PR #3224):
converged the EMAIL branch of `verdict_log.normalize_sender_key` onto bu-qeaou's
shared `butlers.identity.normalize_email_sender`, keeping a documented
channel-aware wrapper — see 7.1 for the divergence quantification. All 25 tasks
are now complete; this change is ready to ARCHIVE.

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

- [x] 6.1 Stats endpoint/dashboard tile: sessions avoided, pending-suggestion
      count, estimated LLM-session savings (bu-hb61f: `GET
      /api/switchboard/rule-promotion-stats` + `RulePromotionStatsTile` on the
      approvals page; degraded-envelope per source; sessions-avoided derived as
      one avoided LLM session per promoted-rule verdict-log match, labelled an
      estimate)

## 7. De-duplicate sender normalization against bu-qeaou (low priority)

- [x] 7.1 Converge `sender_key` derivation onto bu-qeaou's shared normalizer
      (bu-jxsew). bu-qeaou (PR #2976) shipped the shared *helper*
      `butlers.identity.normalize_email_sender`, NOT a normalized-sender column
      on `public.ingestion_events` (that column was never built), so "read the
      column directly" is not available. The helper is also email-only: it runs
      `email.utils.parseaddr`, which reads the `prefix:id` colons in a
      channel-scoped identity as RFC-2822 route/group syntax and strips
      everything before the last colon. Quantified against the live verdict log
      (8 distinct `sender_key`s): 6/8 would MANGLE under the bare shared helper
      — `owntracks:th` → `th`, `home_assistant:<host>:443` → `443` (a COLLISION
      across distinct HA senders), `telegram:bot:@x` → `@x`, `steam:user:<n>` →
      `<n>`, `spotify:<u>` → `<u>`, `dashboard:web:<uuid>` → `<uuid>`; only the
      2 bare-email keys converge. Persisted email keys are byte-stable (the
      shared and old-local email normalization agree on every realistic `From:`
      form across a 25-input comparison). Resolution (owner-approved option B,
      no re-key / no migration): delegate ONLY the email branch of
      `normalize_sender_key` to the shared helper (canonical email
      normalization, converged), and keep the channel-aware wrapper with a
      lowercase-whole fallthrough for channel-scoped ids — documented in
      `verdict_log.py` with the parseaddr counterexample, and guarded by
      byte-identity regression pins in `test_routing_verdict_log.py` so a future
      "just use the shared helper" simplification trips a named test.
