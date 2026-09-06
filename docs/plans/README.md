# Working Design Dependencies

> **Purpose:** Identify the loose plans that still have a live design or verification dependency.
> **Audience:** Maintainers following a plan reference or deciding whether it can be retired.

Required behavior belongs in capability specs; pending changes belong in
OpenSpec. These remaining documents are retained for the specific dependencies
below. Their presence does not grant implementation or deployment authority.

| Retained document | Why it remains | Governing destination |
|---|---|---|
| [Switchboard rule promotion](2026-07-06-switchboard-rule-promotion-design.md) | Routing code, historical migrations, and migration tests cite numbered design sections | [Rule-promotion spec](../../openspec/specs/switchboard-rule-promotion/spec.md) |
| [Telemetry distillation](2026-07-06-telemetry-distillation-design.md) | Active design and adapter references still depend on its source-specific analysis | [Telemetry change](../../openspec/changes/chronicler-telemetry-distillation/) |
| [Connector candidates](2026-07-10-connector-roadmap-proposal.md) | Unselected research options remain an owner decision, not commitments | [Perception roadmap RFC](../../about/legends-and-lore/rfcs/0018-connector-scope-and-deferral-rationale.md) |
| [Beads projection exporter](../superpowers/plans/2026-08-13-beads-projection-exporter.md) | Contract tests read this exact packet; implementation tasks remain in its change | [Beads bridge](../architecture/beads-runtime-data-bridge.md) |
| [Approval delivery recovery](../superpowers/plans/2026-08-13-durable-approval-delivery-intent-recovery.md) | Exact-path contract tests and pending approval/implementation gates | [Approval recovery change](../../openspec/changes/durable-approval-delivery-intent-recovery/) |
| [Codex rotation provenance](../superpowers/plans/2026-08-13-generation-fenced-codex-auth-rotation-provenance.md) | Exact-path contract tests and outstanding generation-fencing work | [Rotation provenance change](../../openspec/changes/generation-fenced-codex-auth-rotation-provenance/) |
| [WhatsApp identity design](../superpowers/specs/2026-08-24-whatsapp-identity-reconciliation-design.md) | Active proposal and spec deltas reference its decision detail | [WhatsApp repair change](../../openspec/changes/repair-whatsapp-identity-reconciliation/) |

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
