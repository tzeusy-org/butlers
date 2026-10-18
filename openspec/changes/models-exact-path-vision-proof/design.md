## Adopted design

RFC0036 at `about/legends-and-lore/rfcs/0036-models-exact-path-vision-proof.md` is the single normative design home, including its storage/API, restricted ingress/retention, and diagnostic executor sections. adopted-contract.md is preserved byte-for-byte as provenance. source-map.md and allocation.json map actual seams and separate source delivery from real evidence.

## Publication versus implementation

The source map is an inspection snapshot, not ownership transfer. Refresh each reservation before dispatch. No exact CLI executor is yet proven eligible; source/mock-transport enforcement, durable epoch issuance/verification/startup and shared-snapshot backup/restore remain mandatory implementation work. Live environment/account/cost authority remains absent. No doctrine isolation exception or normal tool-authority expansion is introduced.

## Amendment 1: executor is an RFC 0038 D18 model function (proposed, awaiting owner adoption)

RFC 0038 D18 lists RFC 0036 vision probes among model functions: tool-less, single-turn, structured-output calls made by deterministic code (`0038-goal-runtime-and-capability-authorization.md`, D18 "Model functions" bullet, lines 814-820 at base 8f75653ed, and the RFC disposition row for 0036 at line 932; the bead cites 810-817 and 928, the same passages at an earlier base). The source-only measurement in RFC 0036 (PR #4360, bu-s1u4br.1) found both installed CLIs unsupported, so the CLI/MCP diagnostic profile cannot be built as written. This amendment replaces that executor and keeps everything else.

Kept unchanged: the three controls (positive, text-only, removed-image) on the exact tuple, independent runner-private sentinels, the purpose-bound capability and operational envelope, durable per-control slots and total spend reservation, content-blind append-only receipts, the restore epoch, explicit Enable/Apply/Refresh, and the exact-row compare-and-swap Apply with managed applicability and historical preservation.

Changed:
- One tool-less, single-turn, structured-output call per control, made by deterministic code, with the image inline (positive control only), a fixed maximum output set by the procedure version and bound in the capability, and the exact runtime/model/account tuple. No retry, failover or resume.
- No CLI session, no MCP session, no `attachment_view` call inside a control, no Spawner diagnostic entry and no routing fit exception. The call addresses its exact authorized row directly, so `REQ-models-vision-proof-003` and the "Fit Before Ranking" sentence now say no exception exists.
- The removed-image control removes the synthetic image after creation and before the request is assembled; text-only carries no image part. Both negatives must return `NO_IMAGE` and never recover their hidden comparator.
- A model-function result is third-party content to any goal (D18); only the deterministic comparator reads the typed answer field.
- MCP image delivery is no longer re-proved per row. Its wire shape stays pinned by the existing tests `test_attachment_view_returns_image_content_block_for_image` and `test_registered_attachment_view_emits_image_block_without_structured_content` in `tests/core/test_attachment_view.py` plus `tests/tools/test_attachments.py`. No new live-proof requirement is added.

Requirement names are unchanged so existing references hold. "Same-tuple production-path controls" now means the production model-function path for the exact tuple, not the CLI/Spawner path. The clause it supersedes in the canonical `model-catalog` spec ("the probe proves an MCP image content block reaches inference") is replaced in the delta and frozen in `scripts/spec-overwrite-baseline.json` under `models-exact-path-vision-proof/model-catalog/Model Catalog Capability Envelope`, scenario "Vision capability requires exact-path evidence".

Open items (not decided here): the concrete maximum-output value and the structured-output schema belong to the implementation under the operational envelope; real-provider runs keep their per-run owner authorization (tasks.md 3.1). The pre-existing adoption digest in `adopted-contract.md` is untouched; this amendment is recorded beside it.

### Overlaps (section 0 steps in tasks.md)

- `generalist-lead-focused-delegation` (RFC 0038): it removes domain-butler sessions and rewrites `core-spawner`/`model-failover`, and D18 explicitly leaves model functions outside goals. Because this proof no longer uses the Spawner, the cutover cannot break it; re-check only if that change alters the model-function definition.
- `harden-runtime-auth-and-breaker-attention`: edits other `model-catalog` and `dashboard-model-settings` requirements (circuit breaker, verify-all, catalog identity). No shared requirement name; re-read before implementation.
