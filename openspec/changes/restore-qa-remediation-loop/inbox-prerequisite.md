# Canonical QA inbox source-acceptance prerequisite

**Graph key:** `INBOX_SOURCE_ACCEPTANCE`. A separate external prerequisite task, assigned to the owner and blocked by the same planning HOLD; not an eighth implementation child. C depends on its closure. Its one bounded outcome is verified predecessor source acceptance and accountable ownership, not another inbox implementation.

Observed: full Beads snapshot on 2026-10-14 identifies `bu-s3qvp` as original durable-dashboard recovery owner. It is closed, but its last exact-merge receipt describes only task 1.3, PR #3735 (`00eed76bdf2695d43e999ad5f7c91d1fb87470a7`), and explicitly leaves tasks 1.4+ gated. Active `durable-dashboard-terminal-action-recovery/tasks.md` still has QA inbox tasks 1.4–1.7 unchecked. Neither a closed parent nor the earlier contract PR proves the authenticated QA inbox landed. No separate current owner was found; do not silently reopen or take over a foreign task.

## Dispatch Readiness Packet

Outcome/non-goals: reconcile the original owner's remaining QA-inbox source obligation and record exact accepted implementation receipts; no authentication, inbox or message-replay implementation under this task, no live invocation or new credential access.

Governing intent: vision Rules 3 and 6; active durable-dashboard-terminal-action-recovery `REQ-staffer-qa-001`, `REQ-staffer-qa-003`, `REQ-staffer-qa-004` and tasks 1.4–1.7. Baseline `b747a55a530364d987fb37457c343193e9362fe7`.

Surface map: original `bu-s3qvp` closure/PR receipts, current canonical QA module auth and report tool, durable inbox/receipt lookup source, migrated role boundaries and Switchboard handoff. Consume the pre-existing work; no competing store or owner.

Behavior matrix: success means authenticated principal-bound inbox, stable canonical finding identity, fenced pending/claim/ack and idempotent receipt lookup have exact landed code and executing proof. Failure or missing source keeps this prerequisite blocked and names the missing task and accountable original-owner follow-up. Concurrent coordinators must reconcile one canonical owner rather than opening duplicate implementation work. Replay/ambiguous effects remain under the existing terminal-action contract; source acceptance does not activate its observe-only runtime. Rollback of this read-only verification changes no runtime; revoke acceptance if its source/contract is superseded.

Documentation impact: append exact source/verification receipts and canonical remaining-owner references to this prerequisite and its consuming C packet. If original-owner continuation needs rematerialization, the coordinator uses that original approved spec/scope and separate gate; do not invent new inbox authority.

## Acceptance Criteria

Read actual implementation paths and all source-check receipts for authenticated router-only admission (wrong subject/audience/spoofed caller rejected), restart-safe inbox discovery, stable canonical identity, same-payload receipt idempotence, changed-payload conflict and fenced acknowledgement. Reuse existing behavior-executing migrated-Postgres/auth/receipt tests and exact hosted check/guards/frontend receipts; a spec checkbox or parent closure is insufficient. Tests: +0 ~0 -0. No new test-only bead or duplicate guard. Record exact landed source hashes, requirement-to-code/gate mapping and current accountable predecessor owner. Close only once all those receipts are accepted; otherwise remain blocked with concrete missing-source condition. C stays blocked independently of the main HOLD closing. No implementation/deployment/replay is authorized by closing this verification task.
