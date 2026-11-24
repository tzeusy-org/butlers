## Why

The investigator was told the error context was "all the information available" and to correlate
session ids it cannot read, then to write evidence lines nobody could check. The dispatcher holds
the database authority; it should read what it can observe, anonymize it, and hand the agent a
read-only bundle whose items carry ids and digests.

## What Changes

- The dispatcher builds an anonymized evidence bundle (failing sessions, tool-call failures,
  matching log lines, call-site git history, recent dispatch outcomes) before launch, records it
  and its digest on the finding, and writes a read-only copy to the agent's input area.
- The investigation prompt names the bundle and no longer claims the error context is complete.
- Notes citing bundle items and their verification are a separate change.

## Impact

No migration. `qa_findings.structured_evidence.evidence_bundle` holds the manifest and bodies.
