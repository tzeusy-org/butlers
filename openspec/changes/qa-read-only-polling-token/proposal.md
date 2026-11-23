## Why

Investigators run as root in the daemon's pod, so a write-scoped GitHub token held by the daemon is
reachable by them. The write token has no use today (no publisher exists) and polling needs only
read access. The owner approved the direction on `bu-l34qcx`; rotating the token and provisioning the
read token are owner steps (`bu-ejow5p.16`).

## What changes

- The daemon resolves only `BUTLERS_QA_GH_READ_TOKEN` (PR polling, dashboard PR panel, credential
  status). It never resolves `BUTLERS_QA_GH_TOKEN`; a leftover key is detected by presence only,
  refused and reported once per patrol.
- A missing read token leaves PR status `unknown`, never `closed`.
- Modifies the "dispatcher keeps the token" clause of the Publication unavailable scenario of
  `REQ-qa-investigation-dispatch-001` to "a read-only token", and states that investigator
  containment is not enforced.

## Not decided here

The pod boundary (`bu-ejow5p.11`), the publisher, token rotation and secret deletion.

Tracking: `bu-ejow5p.1`. Spec wording lands in a PR marked `(needs owner approval)`.
