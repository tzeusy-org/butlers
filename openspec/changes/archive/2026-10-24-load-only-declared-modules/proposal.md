## Why

Registration constructs every discovered module just to read its name; daemon startup then uses load_all before filtering declarations. This violates the accepted constructor boundary and hides missing declared dependencies behind the complete registry.

## What Changes

- Register class-level names without constructors and use load_from_config in daemon admission and decision-memory metadata.
- Reject unknown names, missing declared dependencies and cycles before provisioning or module effects. Preserve explicit load_all utility behavior, discovery failure/retry semantics and module runtime health/enabled separation.
- Declare Approvals in Health to satisfy Home Assistant's existing mandatory dependency without weakening approval gating.
- Select CLI/daemon migration chains from the same recognized directories, preserving the home_assistant/home alias and private schema routing, without rerunning a butler chain.
- Call the merged ABC default hooks directly; describe credentials_env as daemon requirements without forwarding module secrets to children.

## Authority

This is PR 2 of bu-qozovd.2's accepted 2026-10-22 two-PR plan. The class-name constructor elimination, Load-All requirement amendment, HA/Health reconciliation and migration-directory selection are explicitly recorded there. AC1-AC6 remain unchanged. PR 1's completed ABC/helper change is synced and archived first so its defaults remain in the baseline.

## Non-goals

No shared-query, capability authorization, physical-actuation approval-policy, child-credential, runtime enabled-state or discovery import-policy changes.
