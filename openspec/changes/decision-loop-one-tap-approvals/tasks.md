# Tasks — decision-loop-one-tap-approvals

## 1. Structured decision dossier

- [ ] 1.1 Approvals-chain migration `approvals_003`: add `blast_radius TEXT` and `reversibility TEXT` (nullable, CHECK enums) to `pending_actions`; migrate legacy plain-string evidence entries to typed `text` entries
- [ ] 1.2 Extend gate metadata extraction: accept `_blast_radius`/`_reversibility`, validate enums; typed-evidence entries `{type, ref, note}` with strict validation and no runtime coercion
- [ ] 1.3 Enforce required `why` for non-owner-target gated calls with structured retryable error; exempt owner-role bypass path
- [ ] 1.4 Surface dossier fields in `ApprovalDetail` + approvals API responses; render in dashboard action detail
- [ ] 1.5 Update roster skills/prompts that call gated tools to supply the dossier kwargs

## 2. Push on park (approval_request notify intent)

- [ ] 2.1 Extend `notify.v1` envelope + validation with `intent = "approval_request"` and `actions` payload (verb, token, deep link)
- [ ] 2.2 Emit deterministic templated push from the gate park path via Switchboard `deliver`; one push per action (dedup by action_id)
- [ ] 2.3 Quiet-hours deferral reusing `core/approvals_policy.py`; deferred pushes flush after the window; expiry clock unaffected
- [ ] 2.4 Burst collapse: >3 parks in 10 min → single digest message with dashboard deep link
- [ ] 2.5 Messenger channel rendering: telegram inline keyboard; email/WhatsApp fallback = summary + dashboard link

## 3. One-tap Telegram decisions

- [ ] 3.1 `module-telegram`: `reply_markup` (inline keyboard) support on send/reply; keyboard-removal + message-edit helper
- [ ] 3.2 Callback token mint/verify helper (HMAC-SHA256 over action_id+verb_char+requested_at, daemon-internal key, 64-byte-safe format using single-character verb codes)
- [ ] 3.3 `telegram_bot` connector: handle `callback_query` — verify owner channel, answerCallbackQuery ack, route decision to approvals decision routes with actor `human:owner@telegram`
- [ ] 3.4 Edit originating message on resolution (approved/rejected/expired) and on already-decided taps; non-owner taps ignored + logged
- [ ] 3.5 Security review of the callback path against RFC 0017 (owner-routing safety) checklist

## 4. Decision memory writeback

- [ ] 4.1 Deterministic fact templates: `decision:approval_tally` upsert per (fingerprint, entity) with counts/last_action_id/version metadata; `decision:standing_rule` on rule mint/revoke
- [ ] 4.2 Hook writeback into terminal transitions (reject; approve+execution outcome) in executor/decision routes; entity-link via existing channel-identity resolution; fail-open on memory-module absence
- [ ] 4.3 Verify tally facts appear in spawn-time memory context recall for the owning butler

## 5. Fingerprint v2

- [ ] 5.1 `compute_fingerprint` v2 over module-declared safety-critical args (fallback all-args); `fingerprint_version` columns on `autonomy_approval_history` + `autonomy_suggestions` (migration `approvals_004` or folded into `approvals_003`)
- [ ] 5.2 Version-scoped counts/threshold checks; promotion suggestions pin exactly the fingerprinted args
- [ ] 5.3 Dashboard suggestion scope description reflects v2 pinned-args basis

## 6. Verification

- [ ] 6.1 Unit + integration suites per design.md Test Strategy (integration on real Postgres, not mocked pool)
- [ ] 6.2 E2E: park → push → tap Approve → executed → message edited → dashboard provenance shows telegram actor
- [ ] 6.3 `openspec validate decision-loop-one-tap-approvals --strict` green; specs synced on archive
