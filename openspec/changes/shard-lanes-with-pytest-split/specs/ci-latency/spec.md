## REMOVED Requirements

### Requirement: Bounded timestamp evidence for the CI latency objective
**Reason**: bu-29wkwj.3 (owner-authored outcome, 2026-10-20) deletes the latency tooling that measured the orchestration itself: `scripts/ci_latency.py` and the manual `ci-latency.yml` workflow. The 420-second merge-queue objective stays on epic bu-q1hzqn and is measured ad hoc from `gh run list` timestamps.
**Migration**: None. No required or nightly job read the observer.
