> **ARCHIVED** — This implementation/design plan is historical. Archived on 2026-09-06.
> **Reason:** Implementation plan for the routed-approvals-replayable change; the work landed there.
> **Successor:** `openspec/changes/make-routed-approvals-replayable`.
>
> The doctrine on archival lives in `openspec/specs/docs-information-architecture/spec.md`.

# Replayable Routed Approvals Implementation Plan

**Goal:** Ensure every newly parked Messenger routed delivery can be replayed through
the original registered handler, with truthful Retry failure reporting.

**Architecture:** Introduce one internal route-delivery command builder that owns the
native tool name, normalized kwargs, and immediate execution call. Approval gating
and direct delivery consume the same command. Replace dispatch's null sentinel with a
classified internal result consumed by both Retry endpoints.

**Tech stack:** Python 3.12, asyncio, FastMCP, PostgreSQL JSONB, pytest.

## Task 1: Lock the persisted command contract

**Files:**
- Modify: `tests/integration/test_email_outbound_safety.py`

1. Add exact assertions for parked email send/reply tool names and kwargs.
2. Add equivalent Telegram and WhatsApp send/reply-path assertions.
3. Add a missing-email-thread test proving fail-closed behavior.
4. Run the focused tests and confirm they fail for the current payload mismatch.

## Task 2: Materialize one canonical route command

**Files:**
- Modify: `src/butlers/core_tools/_routing.py`

1. Add the smallest internal command representation/helper matching local style.
2. Build exact email, Telegram, and WhatsApp native commands after routing resolution.
3. Use the command for rule evaluation, parking, and immediate delivery.
4. Run the focused routing tests until green.

## Task 3: Prove approval replay through the registered handler

**Files:**
- Modify: `tests/daemon/test_approval_metadata_wiring.py`
- Modify only if required: approval module/daemon wiring implementation

1. Add a behavior test covering park → approve → dispatch → executed for email reply.
2. Assert the provider delivery method is invoked exactly once with exact arguments.
3. Run the focused daemon test and preserve existing behavior.

## Task 4: Classify dispatch outcomes truthfully

**Files:**
- Modify: `tests/api/test_api_approvals.py`
- Modify: `src/butlers/api/routers/approvals.py`

1. Add failing tests for unreachable transport and reachable handler rejection at
   both Retry endpoints.
2. Introduce a private structured dispatch outcome and safe-detail sanitizer.
3. Map failures to truthful API responses while preserving approved/null-result state.
4. Run focused API tests until green.

## Task 5: Verify and review

1. Validate the OpenSpec change.
2. Run focused pytest suites and Ruff on touched Python.
3. Run broader relevant tests if the focused signal reveals shared-surface risk.
4. Request independent exact-head review and resolve actionable findings.
5. Commit, push `agent/bu-kqnum.10.7`, open a PR, and attach evidence to the Bead.
