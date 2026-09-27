## Why

QA publication currently hands GitHub credentials to investigation code while requiring permissions GitHub grant labels do not independently establish. Owner A adopted the exact restricted-publisher contract and repository delivery. This change records the new authority boundary before implementation.

## What Changes

- Remove GitHub credentials from investigation and follow-up environments.
- Bind a trusted deterministic publisher to one immutable validated attempt artifact, repository, branch and PR.
- Validate all externally reachable source/commit/metadata before first push; retain ambiguous outcomes rather than blind retry/deletion.
- Preserve humans as merge/review/approval/queue authority; distinguish effective confinement from coarse provider grants and publisher compromise risk.

## Capabilities
### New Capabilities
- `qa-publication-authority`: process isolation, attempt binding, closed publisher and durable outcome requirements.
### Modified Capabilities
- `qa-investigation-dispatch`: full sandbox, anonymized pipeline and egress requirements.
- `staffer-qa`: security model and dedicated credential ownership.

## Impact

RFC0015, QA manifesto and topology reflect adopted target behavior, not deployed confinement. No implementation, credentials, organization changes, runtime action or live canary. Program bu-mc01y5; source task bu-mc01y5.1. bu-nh37dt/bu-vc90le retain their actual operational outcomes; source or synthetic tests cannot close them.
