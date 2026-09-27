## 1. Executable-command contract

- [x] 1.1 Add failing regression coverage for the inventoried producer
  contracts, handler registration, and historic-row truthfulness.
- [x] 1.2 Define exact owning-daemon command declarations and validate them
  against registered FastMCP handlers during daemon startup.

## 2. Replayable producer paths

- [x] 2.1 Add the approval-gated Switchboard connector-disconnect handler and
  prove park → approve → dispatch soft-deletes the intended registry row.
- [x] 2.2 Add the approval-gated Relationship memory-reclassify handler, enable
  its native gate, and prove direct park → approve → dispatch updates only the
  intended active fact.
- [x] 2.3 Reject connector token-rotation requests before parking when no
  credential reference can be replayed, with a redacted error audit signal.

## 3. Verification

- [x] 3.1 Run focused API, daemon, module, and relationship curation tests;
  run relevant lint and format checks.
- [x] 3.2 Validate the OpenSpec change strictly and review the final diff for
  no historic-row rewrite, no secret leakage, and no retry-time guessing.
