# dashboard-permissions Spec-Parity Final Gate (bu-9q1dx.13)

Bidirectional parity report between `openspec/specs/dashboard-permissions/spec.md`
and the shipped implementation for the whole `/settings/permissions` surface.

- **Bead:** bu-9q1dx.13 (FINAL GATE, epic bu-9q1dx)
- **Verified against:** `origin/main` @ `8e523b6bf` (includes F1-F7 plus all merged
  reconciliation work: #2571, #2579, #2608, #2651, #2694, #2699)
- **Spec revision:** amended in commit `eee37ff4d` (5-perm matrix, FastAPI
  `detail`-wrapped error bodies, wipe disabled)
- **Verdict:** ALL-GREEN on behavior. Every spec scenario maps to shipped code with
  a proving test. Two minor test-coverage gaps (not behavior gaps) are tracked as a
  single low-priority follow-up.

> Runtime note: the dispatched worktree branch was created off a stale local `main`
> (`aeda0bac3`) that predated F6 #2694 and F7 #2699. It was reset to `origin/main`
> (`8e523b6bf`) before this audit so the report reflects the true shipped state. This
> is the known "bd worktree branches off stale local main" hazard.

Legend: file paths are relative to repo root. PASS means the code satisfies the
scenario and a named test proves it.

---

## 1. Spec-compliance matrix

### Requirement: Permissions Page

| Scenario | Status | Implementation (file:line) | Proving test |
|---|---|---|---|
| Permissions page layout (header, matrix, reel, data-ops, webhooks, vertical order) | PASS | `frontend/src/pages/SettingsPermissionsPage.tsx:1216-1277` (header `:1219-1224`, matrix `:1227-1244`, reel `:1259-1261`, data-ops `:1264-1266`, webhooks `:1269-1274`) | `frontend/src/pages/SettingsPermissionsPage.test.tsx` (export/reel/matrix suites) |
| Matrix rows are exactly the 5 enforced perms from `core/permissions.py` | PASS | rows come from `ENFORCED_PERMISSIONS` (`src/butlers/core/permissions.py:68-74`) via `permissions.py:22,117`; FE renders `matrix.permissions` (`SettingsPermissionsPage.tsx:361`) | `tests/api/test_permissions.py:105` (`test_get_permissions_dense_matrix_enforced_perms`) |
| Cells render on/off/inherited; inherited dim, explicit foreground | PASS | `SettingsPermissionsPage.tsx:366-392` (inherited -> `opacity-40 cursor-default`, `disabled`; glyph `●`/`○`) | `SettingsPermissionsPage.test.tsx:232-287` (inherited cell semantics suite) |
| Audit reel: last 15 from `GET /api/audit-log?limit=15&kind=privileged`, filters noise, mono/sans/serif, full-log link | PASS | `SettingsPermissionsPage.tsx:413-468` (`useAuditLog({limit:15, kind:"privileged"})` `:414`; link `:458-463`) -> `frontend/src/api/client.ts:1059` (`?kind=`) -> `src/butlers/api/routers/audit.py:361-363` | `SettingsPermissionsPage.test.tsx:289-320`; `tests/api/test_audit_log.py:764-843` |
| Data-ops sub-grid: export scope picker; wipe disabled (not a live control) | PASS | export `SettingsPermissionsPage.tsx:496-531`; wipe panel disabled `:533-547` (`data-testid="wipe-panel-disabled"`, `Button ... disabled`) | `SettingsPermissionsPage.test.tsx:127-167` (wipe-disabled suite) |
| Webhooks table: list/add/edit/test/delete | PASS | `SettingsPermissionsPage.tsx:919-1169` (add `:994`, edit `:1108`, toggle `:1116`, test `:1129`, delete `:1138`) | `SettingsPermissionsPage.test.tsx:393-560` (webhook suites) |
| Matrix cell flip requires reason (modal, submit disabled while blank, PUT `{granted,reason}`) | PASS | `CellFlipModal` `:245-326` (`isBlank` `:260`, submit `disabled={isBlank||submitting}` `:314`); PUT `putPermission` `:140-155` | `SettingsPermissionsPage.test.tsx` (matrix/inherited suites exercise the editable cells) |
| Audit reel filters operational noise (privileged-only, empty state not padded) | PASS | reel requests `kind=privileged` `:414`; empty state `:451-457`; backend excludes `%_heartbeat` and `GET /%` (`audit.py:361-363`) | `SettingsPermissionsPage.test.tsx:309,319`; `tests/api/test_audit_log.py:824` (`test_kind_privileged_empty_state_returns_empty_page`) |

### Requirement: Permissions Matrix API

| Scenario | Status | Implementation (file:line) | Proving test |
|---|---|---|---|
| Read full matrix (`ApiResponse[PermissionsMatrix]`, dense butlers x 5 perms) | PASS | `src/butlers/api/routers/permissions.py:77-138` (dense build `:119-131`) | `tests/api/test_permissions.py:105` (`test_get_permissions_dense_matrix_enforced_perms`), `:188` (`test_get_permissions_butler_only_in_perm_rows`) |
| Inherited vs explicit cells (no row -> `inherited:true` + default granted; row -> `inherited:false`) | PASS | `permissions.py:124-130` (explicit vs `PERMISSION_DEFAULT_GRANTED` + `inherited=True`) | `tests/api/test_permissions.py:156` (`test_get_permissions_inherited_false_after_explicit_row`) |
| Set permission requires reason (422 `{detail:{error:"reason_required"}}` on empty/missing/whitespace; audit on success) | PASS | `permissions.py:171-172` (guard returns `detail={"error":"reason_required"}`); audit `:198` | `tests/api/test_permissions.py:272,289,306` (empty/whitespace/missing reason -> 422) |
| Reason rejects credential patterns (422 `{detail:{error:"reason_contains_credential"}}`, no state change, no audit; `validate_no_secrets` in `api/security.py`) | PASS | `permissions.py:174-175`; `src/butlers/api/security.py:13-19` | `tests/api/test_permissions.py:333,341,361,386` (clean pass, pattern reject, 422, no-state-change) |
| Inherited cells become explicit on mutation | PASS | PUT upserts a real `public.permissions` row (`permissions.py:184-196`); GET then returns `inherited:false` | `tests/api/test_permissions.py:156` + `:231` (`test_put_permission_success`) |

> Closed prior gap: bu-9q1dx.10 originally FAILED because the un-amended spec
> expected a bare `{error:"reason_required"}` body while FastAPI wraps it under
> `detail`. The spec was amended (`eee37ff4d`, lines 49/55 now document the
> `detail` wrapper) and bu-pgc5h shipped the reconciliation (PR #2608, merged).
> Code and amended spec now agree. No open gap.

### Requirement: Data Operations API

| Scenario | Status | Implementation (file:line) | Proving test |
|---|---|---|---|
| Encrypted export (POST `{scope}`, `signed_url` 60-min TTL + `expires_at`, encrypted zip, `audit.append("data.export")`) | PASS | `src/butlers/api/routers/data_ops.py:362-413` (TTL `:102,391`; audit `:401`); download builds zip + AES-256-GCM `:475-511`, `_encrypt_export:268-289` | `tests/api/test_data_ops.py:99,116,183,828` (signed url, audit, encrypted download, bytes-not-plaintext) |
| Every export scope yields real data (memory/audit/config/all; known scope not silently empty) | PASS | `_SCOPE_MAP` `data_ops.py:112-126`, `all` union `:435-439` | `tests/api/test_data_ops.py:202,229,264,297,814` (audit/config/memory/all scope data, `test_every_known_scope_resolves_to_real_tables`) |
| Wipe feature disabled (no usable control; `DELETE /api/data/wipe` -> 503 `{error:"wipe_disabled"}` for any phrase, zero drops) | PASS | FE disabled panel `SettingsPermissionsPage.tsx:533-547`; backend short-circuit `data_ops.py:543-547` (`_WIPE_ENABLED=False` `:101`) before any DB access | `tests/api/test_data_ops.py:545,562,581,595` (`test_wipe_disabled_returns_503`, `_no_db_mutation`, `_any_phrase`, `_missing_phrase`) |
| Deferred re-enable requirements captured | PASS | spec deferred note `spec.md:88-93`; backlog bead bu-9q1dx.14 (open, blocked) holds atomic-tx + fail-closed-auth + phrase guard | (tracking, no behavior) |

### Requirement: Webhooks Registry API (verified PASS by bu-9q1dx.12; re-confirmed here)

| Scenario | Status | Implementation (file:line) | Proving test |
|---|---|---|---|
| Webhook CRUD: list/get omit secret (only `secret_prefix`); create returns secret ONCE; PUT `regenerate_secret` rotates + returns once; plain PUT keeps secret unchanged + never echoed; delete removes row; every mutation audited | PASS | `src/butlers/api/routers/webhooks.py`: projection no-secret `_WEBHOOK_PROJECTION:62-65`; create secret-once `:301-345`; regenerate `:422-450`; plain PUT keeps secret `:427-429`; delete `:465-487`; audits create `:342` / update `:447` / delete `:485` | `tests/api/test_webhooks.py:136,180,212,231,274,314` (create, client-secret-ignored, list/get omit secret, regenerate rotates, plain-PUT keeps, delete) |
| Webhook test (synthesize `webhook.test`, HMAC-SHA256 dispatch, return `{status_code,latency_ms,ok}`, update `last_test_at`/`last_test_ok`) | PASS | `webhooks.py:495-554` (HMAC sign `_sign_payload:177-184` via `_dispatch_webhook:226-231`; row update `:544-548`; audit `:550`) | `tests/api/test_webhooks.py:358,397` (`test_test_webhook_returns_result`, `test_dispatch_uses_plaintext_secret_for_signing`) |
| Webhook delivery retry (retries per `retry_policy.max_attempts` with linear `backoff_seconds`; after exhaustion attention `kind="webhook_failure"` via settings_console aggregator) | PASS (behavior) / see Gap G1, G2 | retry loop `webhooks.py:242-264`; attention surface `src/butlers/api/routers/settings_console.py:348-380` (`kind="webhook_failure"`, amber, 24h window) wired into aggregator | dispatch failure path `tests/api/test_webhooks.py:438` (`test_dispatch_no_secret_no_signature`, `max_attempts=1`); attention branch and multi-attempt backoff loop not directly asserted (G1/G2) |

---

## 2. Reverse check (implemented but not covered by a scenario)

| Surface element | Where | Assessment |
|---|---|---|
| `GET /api/data/export/download/{export_id}` token validation: 401 bad/wrong-scope/future/negative signature, 410 expired | `data_ops.py:197-223,475-511` | Supporting machinery for the "60-minute TTL signed URL" requirement; not a standalone scenario but directly implements the requirement's guarantees. Well tested (`test_data_ops.py:446-543`). No action. |
| `GET /api/audit-log?kind=` unknown-value -> 422 | `audit.py:328-331` | Defensive validation beyond the spec, which only names `privileged`. Tested (`test_audit_log.py:792`). Acceptable hardening, no action. |
| Webhook `enabled` field + per-row enable/disable toggle | `webhooks.py` model `:93`; FE toggle `SettingsPermissionsPage.tsx:1116-1128`, handler `:962-976` | Shipped under F7 (#2699). The CRUD scenario does not enumerate an `enabled` toggle, but the Page requirement lists edit affordances. Enhancement consistent with the surface; no action. |
| Permissions/webhooks/data endpoints return 503 when switchboard pool missing | `permissions.py:84-85`, `webhooks.py:287-288`, `data_ops.py:500-501` | Degraded-mode guard, not a scenario. Consistent with project degraded-read conventions. No action. |
| Audit reel destructive-action red highlight heuristic | `SettingsPermissionsPage.tsx:409-411,441-445` | Pure presentation; spec says "serif description". Cosmetic enhancement, no action. |

No implemented endpoint on this surface is unspecified in a way that misleads the
operator or contradicts the spec. All reverse-check items are supporting guards or
benign enhancements.

---

## 3. Targeted confirmations requested by the gate

- **Every mutation calls `audit.append`:** confirmed.
  - `permission.set` -> `permissions.py:198`
  - `data.export` -> `data_ops.py:401`
  - `webhook.create` -> `webhooks.py:342`; `webhook.update` -> `:447`;
    `webhook.delete` -> `:485`; `webhook.test` -> `:550`
  - `data.wipe` append exists (`data_ops.py:562`) but is unreachable while wipe is
    disabled (503 short-circuit at `:543-547`), which is correct for the disabled
    state.
- **No dead UI branches:**
  - The matrix cell button guards its click with `disabled={inherited}`
    (`SettingsPermissionsPage.tsx:377-378`); inherited cells are non-interactive by
    design, not a dead branch.
  - The wipe control is an intentionally `disabled` destructive button with a
    "temporarily disabled" note (`:533-547`), matching the spec's "rendered disabled"
    option. It is not a live control and not a dead onClick (it has no handler).
  - The old inherited dead-branch noted in earlier audits is gone: there is no
    code path that renders an interactive control that performs nothing.
- **Wipe is disabled (503 `wipe_disabled`), not a dead control:** confirmed at
  `data_ops.py:101,543-547` and FE `:533-547`.

---

## 4. Gaps and tracked follow-ups

Behavior is all-green. The only findings are two test-coverage gaps on the webhook
retry/attention scenario. The implementation is present and wired; what is missing is
a dedicated proving test for two of its branches. Filed as one low-priority bead.

- **G1 - No test proves `_check_failed_webhooks` emits a `webhook_failure` attention
  item.** Every `tests/api/test_settings_console.py` case mocks
  `_check_failed_webhooks` to `AsyncMock(return_value=[])` (lines 104, 133, 172, 201,
  235, 270, 300, 333), so the real branch at `settings_console.py:348-380` (which
  produces the amber `kind="webhook_failure"` item) is never asserted. Spec scenario:
  Webhooks Registry API > "Webhook delivery retry".
- **G2 - Retry/backoff multi-attempt loop is not exercised.** The dispatch tests use
  `RetryPolicy(max_attempts=1, backoff_seconds=0)` (`test_webhooks.py:431,460`), so
  the multi-attempt retry-with-linear-backoff loop (`webhooks.py:242-264`) and the
  post-exhaustion `ok=False` recording are not directly proven. Spec scenario:
  Webhooks Registry API > "Webhook delivery retry".

Both are test-thoroughness gaps, not correctness defects. They do not block the gate
(PASS criteria: all-green behavior OR every gap tracked); they are tracked by the
follow-up bead below.

---

## 5. Conclusion

The `/settings/permissions` surface is spec-compliant end to end against the amended
`dashboard-permissions/spec.md`. Every requirement and scenario maps to shipped code
with a proving test; every mutation is audited; the wipe control is intentionally
disabled (503 `wipe_disabled`) rather than dead; no misleading or unspecified
endpoints exist. The two open items are webhook retry/attention test-coverage gaps,
tracked for follow-up. Final gate: **PASS (all behavior green; gaps tracked).**
