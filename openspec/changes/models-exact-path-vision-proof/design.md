## Adopted design

RFC0036 at `about/legends-and-lore/rfcs/0036-models-exact-path-vision-proof.md` is the single normative design home, including its storage/API, restricted ingress/retention, and diagnostic executor sections. adopted-contract.md is preserved byte-for-byte as provenance. source-map.md and allocation.json map actual seams and separate source delivery from real evidence.

## Publication versus implementation

The source map is an inspection snapshot, not ownership transfer. Refresh each reservation before dispatch. No exact CLI executor is yet proven eligible; source/mock-transport enforcement, durable epoch issuance/verification/startup and shared-snapshot backup/restore remain mandatory implementation work. Live environment/account/cost authority remains absent. No doctrine isolation exception or normal tool-authority expansion is introduced.

## Amendment 1: executor conforms to RFC 0038 D18

RFC 0038 D18 lists RFC 0036 vision probes among model functions: tool-less, single-turn, structured-output calls made by deterministic code (`0038-goal-runtime-and-capability-authorization.md`, D18 "Model functions" bullet, lines 814-820 at base 8f75653ed, and the RFC disposition row for 0036 at line 932; the bead cites 810-817 and 928, the same passages at an earlier base). The source-only measurement in RFC 0036 (PR #4360, bu-s1u4br.1) found both installed CLIs unsupported, so the CLI/MCP diagnostic profile cannot be built as written. This amendment replaces that executor and keeps everything else.

Kept unchanged: the three controls (positive, text-only, removed-image) on the exact tuple, independent runner-private sentinels, the purpose-bound capability and operational envelope, durable per-control slots and total spend reservation, content-blind append-only receipts, the restore epoch, explicit Enable/Apply/Refresh, and the exact-row compare-and-swap Apply with managed applicability and historical preservation.

Changed:
- One tool-less, single-turn, structured-output call per control, made by deterministic code through the runtime adapter's tool-less model-function mode (no MCP server, every built-in tool denied; the mode `generalist-lead-focused-delegation/specs/runtime-opencode/spec.md` defines; the catalog probe in `core/runtime_probe_control/coordinator.py:288-297` passes `mcp_servers={}` and `max_turns=1` but does not deny built-in tools, so REQ-011 rests on the adapter's tool-less mode and ends Not run where an adapter cannot produce it), with the image inline (positive control only), a fixed maximum output set by the procedure version and bound in the capability, and the exact runtime_type/model_id/runtime-CLI version/account path. No retry, failover or resume. An adapter that cannot enforce a required property ends Not run with a named reason.
- No goal invocation, no MCP-configured session, no `attachment_view` call inside a control, no Spawner diagnostic entry and no routing fit exception. The call targets its exact authorized row, so `REQ-models-vision-proof-003` and the "Fit Before Ranking" sentence now say no exception exists.
- The removed-image control removes the synthetic image after creation and before the request is assembled; text-only carries no image part. Both negatives must return `NO_IMAGE` and never recover their hidden comparator.
- A model-function result is third-party content to any goal (D18); only the deterministic comparator reads the typed answer field.
- MCP image delivery is no longer re-proved per row. Its wire shape stays pinned by the existing tests `test_attachment_view_returns_image_content_block_for_image` and `test_registered_attachment_view_emits_image_block_without_structured_content` in `tests/core/test_attachment_view.py` plus `tests/tools/test_attachments.py`. These tests pin only what the butler server emits; runtime-side forwarding of an MCP image block into inference is no longer proven per row or version and is an accepted unproven residual (bu-m6tgig option A), not a pinned property. No new live-proof requirement is added.

Requirement names are unchanged (except REQ-012, renamed to say "wire shape") so existing references hold. The kept canonical THEN line ("end-to-end probe of that exact `runtime_type`, `model_id`, runtime/CLI version, and account path") is exercised by the adapter mode, so it is not superseded and not frozen; the kept "direct `attachment_view()` unit test ... insufficient evidence" clause stays true, since the wire-shape tests are server-side only. "Same-tuple production-path controls" now means the production model-function path for the exact tuple, not the CLI/Spawner path. The clause it supersedes in the canonical `model-catalog` spec ("the probe proves an MCP image content block reaches inference") is replaced in the delta and frozen in `scripts/spec-overwrite-baseline.json` under `models-exact-path-vision-proof/model-catalog/Model Catalog Capability Envelope`, scenario "Vision capability requires exact-path evidence".

Consequence and alternative: per PR #4360 both installed CLIs were measured unsupported for the earlier diagnostic profile, so there may be no provable row today under option (a); rows stay Not run and Unknown until an adapter can enforce the tool-less single-turn bounded mode. Option (b) is a direct provider call: it would rewrite the kept THEN line (for example to the exact runtime_type, model_id and account credential on the model-function seam, runtime/CLI version not exercised), freeze that second clause beside the first, and reduce provable rows to those with a deterministic provider credential. The draft takes (a); (b) is the owner's alternative.

Open items (not decided here): the concrete maximum-output value and the structured-output schema belong to the implementation under the operational envelope; real-provider runs keep their per-run owner authorization (tasks.md 3.1). The pre-existing adoption digest in `adopted-contract.md` is untouched; this amendment is recorded beside it.

### Overlaps (section 0 steps in tasks.md)

- `generalist-lead-focused-delegation`: merged PR #4891 already records vision probes as D18 model functions and assigns this amendment to bu-o7lumy. Its ledger and design are preserved exactly. The runtime adapter tool-less mode is the governing seam; this proof does not use the Spawner. Re-check current shared requirements before implementation.
- `harden-runtime-auth-and-breaker-attention`: edits other `model-catalog` and `dashboard-model-settings` requirements (circuit breaker, verify-all, catalog identity). No shared requirement name; re-read before implementation.

## Conformance and authority

The 2026-10-21 conforming-text disposition permits drafting and review against
adopted RFC 0038; this candidate claims no new owner adoption. The retained
option (a) exercises the exact runtime/CLI tuple and fails Not run when the
adapter cannot enforce it. Option (b) is an unimplemented alternative requiring
a separate contract amendment and owner decision, not a choice blocking this
conforming candidate. Independent review must verify the D18 boundary and the
explicit residual; provider evidence and landing remain separately gated.
