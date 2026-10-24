## Why

Owner decision bu-g2zuv2 = A (2026-10-21), accepted in bu-99t5s3.6, removes engineering tracker tooling from the product. S1 retired the Helm bridge and frontend. S2 removes the remaining readers and writers in source.

## What Changes

- Remove the five retired API operations, exclusive DTOs, jobs, schedules, transports, snapshot reader and Compose/deploy export plumbing.
- Withdraw their three dashboard API requirements and the superseded unimplemented exporter/write-bridge changes.
- Preserve approvals, QA, owner authentication, callback privacy, tracker confinement and the local decision-bead/Dolt workflow.
- Retain all intent/prompt tables and migration ancestry. DDL is a later separately reviewed step after deployed removal and stopped old readers/writers.

## Impact

No runtime replacement or optional digest is introduced. Existing history remains until the separately authorized destructive migration; source retirement does not attest deployment.
