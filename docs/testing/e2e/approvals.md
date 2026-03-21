# Approvals — Gated Tool Execution Workflow

## Overview

Some butler tools are safety-critical: deleting contacts, stopping medications,
sending external messages. The approval gate system intercepts these tool calls
and holds them pending explicit approval before execution. Approval E2E tests
validate the complete gate lifecycle: configuration, interception, approval
decision, execution, timeout, and denial.

## Approval Architecture

### Configuration

Approval gates are configured in `butler.toml`:

```toml
[approval_gates]
mode = "conditional"  # "none" | "conditional" | "always"

[[approval_gates.gated_tools]]
tool = "contact_delete"
approval_mode = "always"

[[approval_gates.gated_tools]]
tool = "medication_stop"
approval_mode = "always"

[[approval_gates.gated_tools]]
tool = "email_send"
approval_mode = "conditional"
```

### Gate Modes

| Mode | Behavior |
|------|----------|
| `none` | No approval gates active — all tools execute immediately |
| `conditional` | Only tools in `gated_tools` list are gated, and only when their `approval_mode` triggers |
| `always` | All tools in `gated_tools` list require approval on every invocation |

### Per-Tool Approval Modes

| Mode | When Approval Required |
|------|----------------------|
| `always` | Every invocation requires approval |
| `conditional` | Approval required only when sensitive arguments are detected |
| `none` | This specific tool is exempt from gating (override) |

### Argument Sensitivity

Modules declare which tool arguments are sensitive via `ToolMeta.arg_sensitivities`:

```python
# Module declares sensitivity
ToolMeta(arg_sensitivities={"recipient": True, "body": True, "subject": False})
```

When `approval_mode="conditional"`, the gate checks whether any sensitive
arguments have non-trivial values. If all sensitive args are empty or default,
the tool executes without approval.

## Approval Lifecycle

```
Runtime calls gated tool
        │
        ▼
┌───────────────────┐
│  Approval Gate    │
│  intercepts call  │
│                   │
│  Checks:          │
│  - Is tool gated? │
│  - Mode: always?  │
│  - Mode: cond?    │
│    → sensitive     │
│      args present? │
└────────┬──────────┘
         │
    ┌────┴────┐
    │         │
 (gated)  (not gated)
    │         │
    ▼         ▼
┌──────┐  Tool executes
│ Hold │  immediately
│ call │
└──┬───┘
   │
   │  Approval request created
   │  (persisted to approvals table)
   │
   ├────────────────────────┐
   │                        │
   ▼                        ▼
┌──────────┐         ┌──────────┐
│ Approved │         │ Denied / │
│          │         │ Timeout  │
│ Tool     │         │          │
│ executes │         │ Error    │
│          │         │ returned │
│          │         │ to caller│
└──────────┘         └──────────┘
```

## E2E Approval Tests

### Gate Interception

| Test | Setup | Action | Expected Outcome |
|------|-------|--------|------------------|
| Gated tool blocked | `contact_delete` configured as `always` | Runtime instance calls `contact_delete` | Call held, not executed, approval row created |
| Non-gated tool passes | `measurement_log` not in gated list | Runtime instance calls `measurement_log` | Executes immediately, no approval row |
| Conditional gate — sensitive args | `email_send` conditional, `recipient` sensitive | Runtime instance calls `email_send(recipient="user@example.com")` | Gated (sensitive arg present) |
| Conditional gate — no sensitive args | Same config | Runtime instance calls `email_send(recipient="")` | Not gated (sensitive arg empty) |

### Approval Decision

| Test | Setup | Action | Expected Outcome |
|------|-------|--------|------------------|
| Approval granted | Gated tool held | External approval decision: `approved` | Tool executes, result returned to runtime |
| Approval denied | Gated tool held | External approval decision: `denied` | Error returned to runtime, tool not executed |
| Approval timeout | Gated tool held | No decision within timeout | Error returned to runtime, approval row marked `expired` |

### Gate Interaction with Runtime Session

| Test | What It Validates |
|------|-------------------|
| Runtime waits for approval | Runtime session blocks on gated tool, does not terminate prematurely |
| Runtime handles denial | Runtime receives denial error and continues (may try alternative action) |
| Runtime handles timeout | Runtime receives timeout error and reports failure |
| Multiple gated tools in one session | Runtime calls two gated tools → both held, both need approval |

## Testing the Approval Flow

### Setup: Pre-Approved Gate

For E2E tests that need gated tools to execute, a test fixture can auto-approve
pending requests:

```python
@pytest.fixture
async def auto_approver(butler_ecosystem):
    """Background task that auto-approves all pending approval requests."""
    async def _approve_loop():
        while True:
            for butler in butler_ecosystem.values():
                pending = await butler.pool.fetch(
                    "SELECT id FROM approvals WHERE status = 'pending'"
                )
                for row in pending:
                    await butler.pool.execute(
                        "UPDATE approvals SET status = 'approved', decided_at = NOW() "
                        "WHERE id = $1",
                        row["id"],
                    )
            await asyncio.sleep(0.5)

    task = asyncio.create_task(_approve_loop())
    yield
    task.cancel()
```

### Setup: Never-Approve Gate

For testing denial and timeout behavior:

```python
@pytest.fixture
async def never_approver():
    """No approval decisions — all gated tools will timeout."""
    yield  # Do nothing, let approvals expire
```

### Gate Interception Test

```python
async def test_gated_tool_blocked(butler_ecosystem):
    """Gated tool should not execute without approval."""
    relationship = butler_ecosystem["relationship"]

    # Pre-populate a contact to delete
    await relationship.pool.execute(
        "INSERT INTO contacts (name) VALUES ($1)", "Test Contact"
    )

    # Trigger butler to delete the contact (gated tool)
    result = await relationship.spawner.trigger(
        prompt="Delete the contact named 'Test Contact'",
        trigger_source="test",
    )

    # Tool should have been blocked (no auto-approver)
    contact = await relationship.pool.fetchrow(
        "SELECT * FROM contacts WHERE name = 'Test Contact'"
    )
    assert contact is not None  # Still exists — delete was blocked

    # Approval request should exist
    approval = await relationship.pool.fetchrow(
        "SELECT * FROM approvals WHERE tool_name = 'contact_delete' AND status = 'pending'"
    )
    assert approval is not None
```

### Approval Grant Test

```python
async def test_gated_tool_executes_on_approval(butler_ecosystem, auto_approver):
    """Gated tool should execute after approval is granted."""
    relationship = butler_ecosystem["relationship"]

    await relationship.pool.execute(
        "INSERT INTO contacts (name) VALUES ($1)", "Test Contact"
    )

    result = await relationship.spawner.trigger(
        prompt="Delete the contact named 'Test Contact'",
        trigger_source="test",
    )

    # Auto-approver should have approved the delete
    contact = await relationship.pool.fetchrow(
        "SELECT * FROM contacts WHERE name = 'Test Contact'"
    )
    assert contact is None  # Deleted after approval
```

## Approval Audit Trail

Every approval decision is persisted for audit:

```sql
approvals (
    id UUID PRIMARY KEY,
    tool_name TEXT NOT NULL,
    tool_args JSONB NOT NULL,
    session_id UUID REFERENCES sessions(session_id),
    status TEXT NOT NULL,  -- 'pending', 'approved', 'denied', 'expired'
    requested_at TIMESTAMPTZ NOT NULL,
    decided_at TIMESTAMPTZ,
    decided_by TEXT,  -- who approved/denied
    reason TEXT,
)
```

### E2E Audit Trail Tests

| Test | What It Validates |
|------|-------------------|
| Approval row created | After gated tool call, `approvals` table has matching row |
| Status transitions | pending → approved, pending → denied, pending → expired |
| Tool args recorded | Approval row's `tool_args` matches what runtime passed |
| Session linkage | Approval row's `session_id` matches the runtime session |
| Timestamps accurate | `requested_at` before `decided_at` |
| Decided-by recorded | `decided_by` field identifies the approver |

## Edge Cases

### Gated Tool in Scheduled Trigger

When a scheduled task triggers a butler and the runtime session calls a gated tool,
the approval request must still be created. Scheduled triggers are not exempt
from approval gates.

**Test:** Configure a scheduled task that calls a gated tool, fire the scheduler
tick, verify approval request is created.

### Multiple Gates in Sequence

A single runtime session may call multiple gated tools:

```
Runtime session:
  1. state_get("medication-list") → immediate (not gated)
  2. medication_stop("metformin") → HELD (gated, always)
  3. (blocked until #2 approved)
  4. email_send("Doctor", "Stopped metformin") → HELD (gated, conditional)
  5. (blocked until #4 approved)
```

**Test:** Trigger a butler with a prompt that requires two gated actions.
Verify that both approval requests are created and that the session completes
only after both are resolved.

### Race: Approval During Timeout

If approval arrives at the exact moment the timeout fires, the system must
resolve consistently — either the tool executes (approval wins) or it doesn't
(timeout wins), but never both.

**Test:** Set a very short timeout, submit approval at the boundary, verify
the approval row is either `approved` (tool executed) or `expired` (tool
didn't execute), never a mixed state.
