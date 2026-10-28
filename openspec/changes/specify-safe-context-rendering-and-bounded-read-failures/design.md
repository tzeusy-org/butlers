## Status and authority

This complete proposed text is the deliverable of bu-dhloi1.17 for bu-dhloi1.2. Baseline: b725c9ff0276bd49889eb3bbbef821dc078e28a5. Scheduling release bu-276pli permits this work, not exact-text RFC adoption, switch execution or deployment. No exact text has been adopted by this draft.

The change is aligned with vision.md's sovereignty/trust boundaries and deterministic-daemon rule, architecture.md's shared-context/pull topology and RFC0038 D5's provenance/exposure rules. Five pillars already exist. This is a medium trust-boundary amendment, with one existing capability and no new service. The motif is safe situational awareness without turning observations into instructions or storage outages into authorization. The feature-amendment workflow is used; no new direction decision is inferred.

## Typed projection

The only initial value-producing contract is General's deterministic calendar adapter: (`meeting`, `busy`) or (`focused`, `focused`). The producer may classify a title into these existing types; it must not copy the title into context value, metadata or returned state. Type/value validation and fixed strings prevent prompt syntax from crossing the boundary. A user/tool-provided `source=calendar` is not a provenance credential. Unknown values are omitted, not escaped/truncated into acceptance. All other context types remain available as type/confidence summaries; this does not remove their storage/read capability or add a new signal. New typed values require a separate explicit contract amendment.

## Owner mutation boundary

Model-mediated set/clear must use the verified current invocation and server-resolved owner entity or authenticated owner device. `get_current_runtime_session_routing_context` in src/butlers/core/tool_call_capture.py rejects a diagnostic locator without invocation binding. Its presence alone is still not owner identity: the implementation must resolve the actual bound caller, following the owner derivation seam in src/butlers/modules/memory/content_authority.py without accepting an explicitly supplied context dict as a new public authority. Missing/system/unknown/third-party provenance refuses; a source-channel string, prompt statement, confidence or mutation UUID is insufficient. Deterministic role-scoped producer calls remain distinct internal paths. Existing DND generation, immutable mutation ID and gateway checks are preserved, including the active canonical-dnd-generation-guard delta.

## Read outcome and finite state

| Observation | Sleep producer | Routine attention |
| --- | --- | --- |
| Successful valid policy/context | Apply existing predicates/TTL | Apply ordinary suppression and delivery rules |
| Successful absent, disabled or malformed policy | Clear/avoid only Health-owned sleep | No fabricated quiet-hours hold; still evaluate independent suppressors |
| Transient read failure with confirmed finite state | Keep original timestamps; no new assertion | Hold/refuse current attempt; any durable hold uses only confirmed finite anchor |
| Failure without anchor, or anchor expires | No synthetic sleeping/DND/wake time | End attempt unavailable without sending; never create an indefinite hold or silently destroy pending work |
| Read recovery | Read current policy, then set/clear | Re-evaluate current expiry/dedup/policy before release |

This introduces no new outage timer. A durable hold may use only the original confirmed context expiry (and consumer max-hold if applicable) or confirmed policy end. Repeated failure must not reset set_at, roll expiry forward or infer a timezone. Once an anchor expires, the finite hold ends; an unavailable read is still not a successful all-clear. A failed attempt is not an indefinitely running task. If durable storage itself is unavailable, report unavailable and perform no send or claimed enqueue; preserve pending-work ownership for its normal retry/expiry mechanism. Recovery is eligibility for normal evaluation, never proof of delivery or permission to duplicate a send. There is no new retry scheduler/catch-up route in this contract.

## Existing numerical defaults and consumer boundaries

These values are observed at baseline, not retuned by this change:

| Contract | Default | Maximum / boundary | Source |
| --- | --- | --- | --- |
| DND signal TTL | 2h | 24h | src/butlers/context_bus.py `_TTL_CONFIG`; canonical DND gateway remains authoritative |
| Sleeping signal TTL | 8h | 12h | src/butlers/context_bus.py `_TTL_CONFIG` |
| Meeting TTL | 1h | 4h | same source |
| Focused TTL | 2h | 8h | same source |
| Insight routine max-hold from continuous set_at | DND 4h, focused 4h, meeting 2h, sleeping 10h, traveling 6h | Independent of context TTL; earlier signal expiry still applies | roster/switchboard/tools/insight/broker.py `_CONTEXT_MAX_HOLD` |
| Shared direct-notify suppressors | DND/sleeping only | Latest active expires_at; DND reason precedence does not shorten anchor | src/butlers/core/attention_ledger.py `get_suppressing_context` |
| Quiet-window policy | Stored owner hours/IANA zone | `[start,end)`; equal endpoints empty; exact end UTC, no extra hour | src/butlers/core/approvals_policy.py shared helpers |

The insight broker's 4/4/2/10/6h caps do not become a new cap on direct notify. Sleep's exact policy end is still subject to the existing 12h context TTL clamp. DND's database operation remains its own TTL normalization authority. An outage does not renew any of these values. An expired suppression anchor is not a new delivery or bypass authority.

Authorized deterministic safety/urgent paths stay ahead of routine gating. The existing attention priority threshold 90 and notify high mapping in src/butlers/core/attention_ledger.py are observed compatibility facts, not permission for model/source text to manufacture safety provenance. No existing hard owner preference or approval/recipient rule is bypassed by this amendment.

## Requirement and same-PR test accounting

Every changed clause belongs to bu-dhloi1.2, with one existing behavior-test species extended at the actual seam:

| Requirement | Existing seam | Future same-PR change / surviving behavior |
| --- | --- | --- |
| REQ-context-bus-009 | tests/core/test_context_bus.py `test_format_context_preamble_and_validation` | Replace raw Paris/title rendering expectations with typed allowlist and hostile/forged omissions; retain empty/multi-signal/confidence/vocabulary coverage |
| REQ-context-bus-011 | tests/jobs/test_context_producers.py calendar producer cases + `test_classify_calendar_signal` | Keep human/generated/all-day/malformed provenance/TTL/sibling controls; change title-bearing value/metadata/result expectations and prove hostile title never propagates |
| REQ-context-bus-012 | roster/general/tests/test_tools.py context tool cases; src/butlers/core/tool_call_capture.py existing binding contract | Extend actual registered tools with verified owner versus third-party/forged/missing/unbound cases; preserve existing validation/DND receipt/replay controls; locate the real binding fixture before authoring |
| REQ-context-bus-013 | tests/jobs/test_context_producers.py `test_sleep_producer_uses_shared_exact_policy_anchor` | Preserve exact-end/invalid-zone/no-guessed-timezone; distinguish outage retention, expiry and recovery |
| REQ-context-bus-016 | tests/core/test_attention_ledger_suppression.py existing lookup-failure and policy/context precedence cases | Replace fail-open-on-read-error expectation with finite unavailable behavior, retain successful invalid/empty-policy, precedence/expiry and authorized safety bypass; exercise actual delivery caller as needed |

Tests are unchanged in this contract PR (Tests:+0~0-0). The named tests are inspection evidence and future seams, never executed implementation proof here. In bu-dhloi1.2 changed behavior may change its pinning test only with this adopted requirement and explicit case/assertion survivor accounting; all unaffected cases, parameters and assertions remain. Use the repository's current required admission/evidence protocol, not a new parallel test process.

## Scope completeness and adoption

Authorization/privacy, failure/expiry/recovery, replay and repeated-failure nonrenewal are explicit scenarios. No new migration or externally writable value type is introduced. This is backend contract text; a new UI, accessibility interaction, perception feature and performance benchmark are N/A. Existing finite task/DB bounds are not changed. No new timer or timezone is chosen.

Before bu-dhloi1.17 closes, independent normative/conformance review must bind the final RFC0009 clause text plus delta/docs to an immutable commit. Then the owner must adopt that exact text through the repository RFC path, recording identity/date/commit and affected sections. Draft PR creation, security-intent approval, scheduling release, green validation and independent review do not substitute for that adoption. Only after adoption and landing is the prerequisite satisfied for bu-dhloi1.2. Do not sync/archive this delta or mark the implementation task complete here.
