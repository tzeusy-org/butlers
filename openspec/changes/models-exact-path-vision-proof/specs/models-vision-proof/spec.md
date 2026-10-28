## ADDED Requirements

### Requirement: Owner Models states and capability edits

Models SHALL distinguish declared true/false/unknown, latest check outcome and applied proof applicability. It SHALL expose Verify vision and explicit Enable/Apply/Refresh actions; no raw true checkbox or text-Verify proof substitution.

ID: REQ-models-vision-proof-001
Source: adopted-contract.md SHA256 d46d758108064d4ea5d00cab2a94a8ebc82a18f54dea5a2b9393b7f1cc25e11e; model-catalog exact-path evidence; owner A 2026-09-27
Scope: v1-mandatory

#### Scenario: Owner Models states and capability edits boundary

- **WHEN** a historical true row has no new harness binding
- **THEN** it remains historically declared, no current proof is fabricated and unrelated edits remain available

### Requirement: Purpose-bound operation admission

An owner-only operation SHALL require a distinct purpose/version capability and separately provisioned exact-target operational envelope that binds the exact runtime/model/account tuple, the procedure version's fixed maximum output and the per-call spend reservation. Text tokens, missing caps or a call whose fixed maximum output or spend cannot be enforced MUST launch nothing.

ID: REQ-models-vision-proof-002
Source: adopted-contract.md SHA256 d46d758108064d4ea5d00cab2a94a8ebc82a18f54dea5a2b9393b7f1cc25e11e; model-catalog exact-path evidence; owner A 2026-09-27; as amended by RFC0036 Amendment 1 (bu-o7lumy)
Scope: v1-mandatory

#### Scenario: Purpose-bound operation admission boundary

- **WHEN** a request lacks matching authority or enforceable launch/turn/token/wall/spend caps
- **THEN** it ends Not run with a bounded reason before provider or attachment effects

### Requirement: Diagnostic selection preserves ordinary authority

The vision proof SHALL address only the exact authorized catalog row and SHALL NOT pass through model resolution, Spawner dispatch or any vision-fit exception. Ordinary routing, fit, quota and auth gates are unchanged and no ordinary caller, text-probe token, fallback, retry, resume or temporary catalog bit may reach or imitate the proof call.

ID: REQ-models-vision-proof-003
Source: adopted-contract.md SHA256 d46d758108064d4ea5d00cab2a94a8ebc82a18f54dea5a2b9393b7f1cc25e11e; model-catalog exact-path evidence; owner A 2026-09-27; as amended by RFC0036 Amendment 1 (bu-o7lumy)
Scope: v1-mandatory

#### Scenario: Diagnostic selection preserves ordinary authority boundary

- **WHEN** ordinary dispatch or a text-probe token attempts to reach the proof call
- **THEN** it is denied and normal image fitting remains unchanged
- **AND** no fit exception exists in model resolution for the proof to use

### Requirement: Same-tuple production-path controls

A proof SHALL make exactly three model-function calls (RFC 0038 D18) against the same exact runtime/model/account tuple: a positive control carrying the synthetic image inline, a text-only control carrying no image part, and a removed-image control whose image is removed after creation and before the request is assembled. Controls differ only in the image part. Each call SHALL be one tool-less, single-turn, structured-output request with the procedure version's fixed maximum output and no retry, failover or resume, and its typed output SHALL be compared by deterministic code before any verdict.

ID: REQ-models-vision-proof-004
Source: adopted-contract.md SHA256 d46d758108064d4ea5d00cab2a94a8ebc82a18f54dea5a2b9393b7f1cc25e11e; model-catalog exact-path evidence; owner A 2026-09-27; as amended by RFC0036 Amendment 1 (bu-o7lumy)
Scope: v1-mandatory

#### Scenario: Same-tuple production-path controls boundary

- **WHEN** a control's typed output is missing, unparseable, refused or truncated, a negative control recovers its hidden comparator, or a different tuple is substituted
- **THEN** the operation cannot produce promotable evidence

### Requirement: Private sentinel and content-blind records

Sentinels SHALL exist only in volatile runner comparison state and image pixels. No raw prompts/results, expected values, metadata, screenshots, low-entropy sentinel hashes or arbitrary exception text may enter durable evidence or normal diagnostic capture. Exact server-owned control manifests bind every control to its tuple, image-part state and expected typed result class.

ID: REQ-models-vision-proof-005
Source: adopted-contract.md SHA256 d46d758108064d4ea5d00cab2a94a8ebc82a18f54dea5a2b9393b7f1cc25e11e; model-catalog exact-path evidence; owner A 2026-09-27; as amended by RFC0036 Amendment 1 (bu-o7lumy)
Scope: v1-mandatory

#### Scenario: Private sentinel and content-blind records boundary

- **WHEN** a foreign context, extra request part, tool definition or forbidden sentinel channel is observed
- **THEN** the operation refuses/fails and no passing receipt or widened read results

### Requirement: Durable replay budgets and cancellation

Operations SHALL reserve total budget and per-control launch slots durably before execution; retry/reload cannot repeat a consumed or ambiguous slot. Cancellation/expiry prevent new launches; interruption preserves truthful status and no automatic retry.

ID: REQ-models-vision-proof-006
Source: adopted-contract.md SHA256 d46d758108064d4ea5d00cab2a94a8ebc82a18f54dea5a2b9393b7f1cc25e11e; model-catalog exact-path evidence; owner A 2026-09-27
Scope: v1-mandatory

#### Scenario: Durable replay budgets and cancellation boundary

- **WHEN** two tabs retry or a process crashes after launch
- **THEN** status reconnects or records interruption without a second provider call

### Requirement: Explicit proof application and exact CAS

Verify SHALL be evidence-only even for true rows. Enable/Apply/Refresh SHALL explicitly and atomically compare the complete expected row/envelope and live path, bind the receipt and merge only vision=true, preserving other keys.

ID: REQ-models-vision-proof-007
Source: adopted-contract.md SHA256 d46d758108064d4ea5d00cab2a94a8ebc82a18f54dea5a2b9393b7f1cc25e11e; model-catalog exact-path evidence; owner A 2026-09-27
Scope: v1-mandatory

#### Scenario: Explicit proof application and exact CAS boundary

- **WHEN** a fresh pass exists for a stale managed or historical true row
- **THEN** routing/binding stay unchanged until explicit Apply/Refresh CAS succeeds

### Requirement: Managed applicability and historical preservation

New managed proof SHALL gate image-required routing by exact applied identity; stale managed evidence excludes it. Historical unbound declarations SHALL retain prior behavior until explicit conversion and never be silently cleared, upgraded to proof or backfilled from brand claims.

ID: REQ-models-vision-proof-008
Source: adopted-contract.md SHA256 d46d758108064d4ea5d00cab2a94a8ebc82a18f54dea5a2b9393b7f1cc25e11e; model-catalog exact-path evidence; owner A 2026-09-27
Scope: v1-mandatory

#### Scenario: Managed applicability and historical preservation boundary

- **WHEN** runtime/model/account/config changes on a managed row
- **THEN** applied evidence becomes stale and new image-required fitting refuses it until fresh explicit application

### Requirement: Accessible status and safe rollback

Models SHALL expose bounded progress/errors, keyboard focus/announcements and resumable status. Rollback MUST stop pending launches and retain receipts plus fail-closed managed eligibility; ordinary text verification remains available.

ID: REQ-models-vision-proof-009
Source: adopted-contract.md SHA256 d46d758108064d4ea5d00cab2a94a8ebc82a18f54dea5a2b9393b7f1cc25e11e; model-catalog exact-path evidence; owner A 2026-09-27
Scope: v1-mandatory

#### Scenario: Accessible status and safe rollback boundary

- **WHEN** owner cancels/navigates/reloads or feature is disabled
- **THEN** no hidden retry/automatic promotion occurs and state remains honest and accessible


### Requirement: All new vision enablement is proof-bound

Every create, clone, import, generic update or other write introducing vision=true, and every identity-changing edit retaining true including a historical row, SHALL require a matching proof-bound application. Only unchanged historical declarations are grandfathered; missing managed binding is not an enabling exception.

ID: REQ-models-vision-proof-010
Source: exact adopted contract User experience and Receipt/application sections
Scope: v1-mandatory

#### Scenario: Historical clone cannot inherit vision authority
- **WHEN** an alias is cloned or a historical true row changes relevant identity
- **THEN** true cannot be retained or created without a passing applicable proof and explicit Apply CAS

### Requirement: Vision proof executor is an RFC 0038 D18 model function

The proof executor SHALL be deterministic code making tool-less, single-turn, structured-output model calls as defined by RFC 0038 D18 (model functions), run through the runtime adapter's tool-less model-function mode for the exact `runtime_type`, `model_id`, runtime/CLI version and account path of the row. The call SHALL carry no MCP server and no built-in tool, one user turn, a structured output schema, the inline image part (positive control only) and the procedure version's fixed maximum output. It SHALL NOT start a goal invocation or an MCP-configured session, and SHALL treat the model's typed output as third-party content that only the deterministic comparator reads. Spend reservation SHALL be made durably before each call and SHALL cover the fixed maximum output. An adapter or tuple that cannot enforce any required property (tool-less configuration, single turn, fixed maximum output, spend bound, inline image) SHALL end the operation Not run with a named reason before any provider request; no CLI diagnostic profile, proxy or credential broker is introduced.

ID: REQ-models-vision-proof-011
Source: RFC0036 Amendment 1; RFC0038 D18 (about/legends-and-lore/rfcs/0038-goal-runtime-and-capability-authorization.md, Model functions bullet, and the RFC 0036 row of its RFC disposition table); bu-o7lumy
Scope: v1-mandatory

#### Scenario: Proof call has no tools and a bounded output
- **WHEN** a control call is assembled
- **THEN** it SHALL carry no MCP server and no tool definition, one user turn, the inline image part only for the positive control, and the fixed maximum output
- **AND** an adapter that cannot produce that configuration SHALL end the operation Not run with a named reason before any provider request

#### Scenario: Model output never becomes authority
- **WHEN** a control returns text that asks for an action or names a tool
- **THEN** the comparator SHALL read only the typed answer field and SHALL take no other action
- **AND** the operation SHALL record at most a bounded typed verdict

### Requirement: MCP attachment wire shape is pinned by existing tests

The server-side wire shape of MCP image delivery (the image content block returned by `attachment_view` without a `structuredContent` override, and its refusal cases) SHALL stay pinned by the existing tests `test_attachment_view_returns_image_content_block_for_image` and `test_registered_attachment_view_emits_image_block_without_structured_content` in `tests/core/test_attachment_view.py` and the attachment tool tests in `tests/tools/test_attachments.py`. These tests pin only what the butler server emits. Runtime-side forwarding of an MCP image block into inference is NOT proven per row or per runtime version after this change; that is an accepted unproven residual (the bu-m6tgig trade-off), not a pinned property. The vision proof SHALL establish only that the exact model reads an inline image on the exact tuple. No new live-proof requirement SHALL be added for MCP delivery, and an ordinary session's image routing SHALL still require an applicable proof under REQ-models-vision-proof-008.

ID: REQ-models-vision-proof-012
Source: RFC0036 Amendment 1; tests/core/test_attachment_view.py; tests/tools/test_attachments.py; bu-o7lumy
Scope: v1-mandatory

#### Scenario: Wire-shape regression is caught by the existing tests
- **WHEN** `attachment_view` stops returning an image content block or lets `structuredContent` override it
- **THEN** the named existing tests SHALL fail
- **AND** no per-row live proof SHALL be required to detect it

#### Scenario: Runtime-side forwarding stays an unproven residual

- **WHEN** a runtime version drops or rewrites an MCP image block before inference
- **THEN** no requirement of this program claims to detect it
- **AND** the proof and the tests SHALL NOT be described as covering runtime-side forwarding
