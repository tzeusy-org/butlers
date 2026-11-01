# Working Design Dependencies

> **Purpose:** The only plan home. Identify the loose plans that still have a live design or verification dependency.
> **Audience:** Maintainers following a plan reference or deciding whether it can be retired.

Required behavior belongs in capability specs; pending changes belong in
OpenSpec. These remaining documents are retained for the specific dependencies
below. Their presence does not grant implementation or deployment authority.

| Retained document | Why it remains | Governing destination |
|---|---|---|
| [Switchboard rule promotion](2026-07-06-switchboard-rule-promotion-design.md) | Routing code, historical migrations, and migration tests cite numbered design sections | [Rule-promotion spec](../../openspec/specs/switchboard-rule-promotion/spec.md) |
| [Telemetry distillation](2026-07-06-telemetry-distillation-design.md) | Active design and adapter references still depend on its source-specific analysis | [Telemetry change](../../openspec/changes/chronicler-telemetry-distillation/) |
| [Connector candidates](2026-07-10-connector-roadmap-proposal.md) | Unselected research options remain an owner decision, not commitments | [Perception roadmap RFC](../../about/legends-and-lore/rfcs/0018-connector-scope-and-deferral-rationale.md) |
| [Beads projection exporter](2026-08-13-beads-projection-exporter.md) | Contract tests read this exact packet; implementation tasks remain in its change | [Beads projection exporter change](../../openspec/changes/beads-projection-exporter/) |
| [Approval delivery recovery](2026-08-13-durable-approval-delivery-intent-recovery.md) | Exact-path contract tests and pending approval/implementation gates | [Approval recovery change](../../openspec/changes/archive/2026-10-05-durable-approval-delivery-intent-recovery/) |
| [Codex rotation provenance](2026-08-13-generation-fenced-codex-auth-rotation-provenance.md) | Exact-path contract tests and outstanding generation-fencing work | [Rotation provenance change](../../openspec/changes/generation-fenced-codex-auth-rotation-provenance/) |
| [System prompt authority](system-prompt-authority-decision.md) | Proposed governing-spec amendment awaiting the owner's roster vs `system_prompt_history` authority decision | [Roster identity overlay change](../../openspec/changes/specify-roster-identity-owner-operations-overlay/) |
| [WhatsApp identity design](2026-08-24-whatsapp-identity-reconciliation-design.md) | Active proposal and spec deltas reference its decision detail | [WhatsApp repair change](../../openspec/changes/repair-whatsapp-identity-reconciliation/) |
| [Shape condensation plan](2026-10-03-shape-condensation-plan.md) and its [evidence tables](2026-10-03-shape-condensation-evidence.md) | Governs the doctrine, spec and RFC consolidation program; its phase gates and owner decisions are live until the last phase-2 change archives | Doctrine amendments (`about/heart-and-soul`), one OpenSpec change per spec family |
| [Core 208 fleet rollout packet](2026-09-05-core-208-conversation-anchor-fleet-rollout.md) | Historical rollout packet for bu-psarp / PR #3960; retained as a cutover evidence template, with the recorded October 8 supersession at its head | [Relationship cutover authorization change](../../openspec/changes/authorize-relationship-effective-time-cutover/) |
| [Reliability and liveness packets](2026-09-23-liveness-control-plane-reliability-packet.md) and its [beads plan](2026-09-23-reliability-beads-plan.md) | Evidence packets cited by the control-plane liveness changes (moved from the retired `docs/reviews/`) | [Liveness change](../../openspec/changes/restore-butler-control-plane-liveness/) |
| [Completed-work packets](2026-10-13-completed-work-packet.md) ([details](2026-10-13-completed-work-details.md), [inventory](2026-10-13-completed-work-inventory.md), [reproductions](2026-10-13-completed-work-reproductions.md)) | Evidence behind the October 13 completed-work audit (moved from `docs/reviews/`) | Beads filed from the audit |
| [QA month packet](2026-10-14-qa-month-packet.md) | Accepted monthly packet that owns the evidence for the QA remediation direction (moved from `docs/reviews/`) | [QA remediation change](../../openspec/changes/restore-qa-remediation-loop/) |

## Maintenance and Verification

When the owning change converges, reconcile each still-applicable clause into
its canonical home and repoint its callers before removing the loose body.
Do not preserve a completed implementation recipe merely because it has a
historic path. The [successor map](../archive/README.md) records the recipes
already retired in this pass.

Before changing a retained path, search docs, specs, code, tests, and skills for
its basename. In particular, the Beads projection, approval recovery, and Codex
rotation packets are read by tests under `tests/contracts/`; a text-reference
search alone is not permission to remove their assertions.
