# Security — Credential Isolation and Boundary Enforcement

## Overview

Each butler spawns ephemeral Claude Code instances that execute arbitrary tool
calls. The security model ensures that these instances operate within strict
boundaries: they can only access their own butler's MCP tools, their own
database, and a declared set of environment variables. Security E2E tests
validate these isolation boundaries under real execution conditions.

## Credential Sandboxing

### The `_build_env` Contract

When the spawner invokes a CC instance, it constructs an explicit environment
dictionary. Only declared variables are included — no undeclared environment
variables leak through:

```python
# src/butlers/core/spawner.py
def _build_env(config: ButlerConfig, module_credentials_env=None) -> dict[str, str]:
    env: dict[str, str] = {}

    # Always include ANTHROPIC_API_KEY
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if api_key:
        env["ANTHROPIC_API_KEY"] = api_key

    # Butler-level declared vars (from butler.toml)
    for var in config.required_env_vars:
        value = os.environ.get(var, "")
        if value:
            env[var] = value

    for var in config.optional_env_vars:
        value = os.environ.get(var, "")
        if value:
            env[var] = value

    # Module credential vars
    if module_credentials_env:
        for module_name, var_names in module_credentials_env.items():
            for var in var_names:
                value = os.environ.get(var, "")
                if value:
                    env[var] = value

    return env
```

### What This Prevents

| Threat | Mitigation |
|--------|-----------|
| CC instance reads host's `SSH_AUTH_SOCK` | Not in declared vars → not in env |
| CC instance reads another butler's credentials | Each butler declares only its own vars |
| CC instance reads `DATABASE_URL` for wrong DB | DB connection is MCP-mediated, not env-mediated |
| CC instance reads CI/CD tokens | `GITHUB_TOKEN`, `CI` etc. not declared |
| Module credential leak to wrong butler | Module credentials scoped to butler's enabled modules |

### E2E Credential Isolation Tests

| Test | What It Validates |
|------|-------------------|
| Env allowlist | Set a canary env var (`TEST_CANARY=secret`), trigger a butler, verify CC instance cannot access it |
| Cross-butler credential isolation | Health butler's CC instance cannot access relationship butler's credentials |
| ANTHROPIC_API_KEY always present | Trigger a butler, verify CC instance has API key (otherwise LLM calls fail) |
| Module credential scoping | Telegram credentials only available to butlers with telegram module enabled |

### Testing Env Isolation

```python
async def test_env_isolation(butler_ecosystem, monkeypatch):
    """CC instance should not see undeclared env vars."""
    monkeypatch.setenv("TEST_SECRET_CANARY", "should-not-leak")

    health = butler_ecosystem["health"]
    result = await health.spawner.trigger(
        prompt="What is the value of TEST_SECRET_CANARY?",
        trigger_source="test",
    )

    # The LLM should not be able to retrieve the value
    # because the env var is not in the spawner's declared vars
    assert "should-not-leak" not in (result.output or "")
```

## MCP Config Lockdown

### Single-Butler Scope

When the spawner generates the MCP config for a CC instance, it points
exclusively at the butler that owns the spawner:

```python
# Generated MCP config
{
    "mcpServers": {
        "butler": {
            "url": f"http://localhost:{config.port}/sse"
        }
    }
}
```

The CC instance can only call tools registered on its own butler's FastMCP
server. It cannot discover or call tools on other butlers.

### What This Prevents

| Threat | Mitigation |
|--------|-----------|
| CC instance calls tools on another butler | MCP config only contains own butler's endpoint |
| CC instance discovers switchboard tools | Switchboard endpoint not in config |
| CC instance routes messages directly | No `route()` tool available on non-switchboard butlers |
| CC instance modifies butler registry | Registry tools only on switchboard |

### E2E MCP Lockdown Tests

| Test | What It Validates |
|------|-------------------|
| Tool list scoped | Health butler's CC instance can list tools → only health tools appear |
| Cross-butler call fails | Health butler's CC instance tries to call a relationship tool → error |
| Switchboard tools hidden | Non-switchboard CC instance has no `classify_message` or `route` tools |

## Database Isolation

### Per-Butler Database Boundary

Each butler owns a dedicated PostgreSQL database. The connection pool is scoped
to that database. No butler can read or write another butler's data:

```python
# Each butler's Database instance connects to its own DB
Database(db_name=f"butler_{butler_name}", ...)
```

### What This Prevents

| Threat | Mitigation |
|--------|-----------|
| Health tool writes to relationship DB | Connection pool bound to `butler_health` DB |
| Cross-DB join queries | asyncpg connection is per-database, no cross-DB queries possible |
| Shared state corruption | No shared tables between butlers |
| Privilege escalation via SQL | Each butler's DB has its own schema, no cross-references |

### E2E Database Isolation Tests

| Test | What It Validates |
|------|-------------------|
| Cross-DB write impossible | Call health tool, verify no rows in relationship DB |
| Schema isolation | Health DB has `measurements` table, relationship DB does not |
| Connection pool scoping | Health butler's pool only connects to `butler_health` |

### Testing Cross-DB Isolation

```python
async def test_cross_db_isolation(butler_ecosystem):
    """Health butler tools should not affect relationship DB."""
    health = butler_ecosystem["health"]
    relationship = butler_ecosystem["relationship"]

    # Trigger health butler to log a measurement
    await health.spawner.trigger("Log weight 80kg", trigger_source="test")

    # Verify measurement exists in health DB
    health_row = await health.pool.fetchrow(
        "SELECT * FROM measurements ORDER BY created_at DESC LIMIT 1"
    )
    assert health_row is not None

    # Verify NO measurement table or rows in relationship DB
    rel_tables = await relationship.pool.fetch(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = 'public' AND table_name = 'measurements'"
    )
    assert len(rel_tables) == 0  # measurements table doesn't exist in relationship DB
```

## Secret Detection

### Credential Validation at Startup

The `detect_secrets()` function scans for common credential patterns in
configuration and environment before the daemon starts:

```python
# src/butlers/credentials.py
def detect_secrets(config: ButlerConfig) -> list[str]:
    """Detect potential secrets in butler configuration."""
    ...
```

### Log Redaction

Sensitive values should not appear in application logs, session logs, or trace
attributes:

| Data | Where It Could Leak | Mitigation |
|------|-------------------|-----------|
| `ANTHROPIC_API_KEY` | Spawner logs, trace attributes | Env var value not logged, only presence/absence |
| Module credentials | Module startup logs | Credential values replaced with `***` in logs |
| Tool arguments | Session `tool_calls` JSONB | `ToolMeta.arg_sensitivities` marks sensitive args |
| User message content | Classification logs | Message body logged at `DEBUG` only, not `INFO` |

### E2E Secret Detection Tests

| Test | What It Validates |
|------|-------------------|
| No API key in logs | After a full run, `ANTHROPIC_API_KEY` value does not appear in log file |
| No credentials in session logs | Session `tool_calls` JSONB does not contain credential values |
| Sensitive args redacted in traces | Tool span attributes do not contain values for sensitive arguments |
| Config secrets detected | Butler with hardcoded secret in `butler.toml` triggers warning |

### Testing Log Redaction

```python
async def test_no_api_key_in_logs(butler_ecosystem, log_capture):
    """API key value should never appear in application logs."""
    api_key = os.environ["ANTHROPIC_API_KEY"]

    # Run a full pipeline that exercises all logging
    await trigger_full_pipeline(butler_ecosystem, "Log weight 80kg")

    # Scan all captured log messages
    for record in log_capture.records:
        assert api_key not in record.getMessage(), (
            f"API key leaked in log: {record.name}:{record.lineno}"
        )
```

## Inter-Butler Communication Security

### Switchboard as Sole Router

The architectural constraint is that inter-butler communication only flows
through the switchboard. Butlers do not call each other directly:

```
Butler A ──X──► Butler B       (PROHIBITED)
Butler A ──►  Switchboard ──► Butler B   (ALLOWED)
```

### What This Prevents

| Threat | Mitigation |
|--------|-----------|
| Butler-to-butler backdoor | No butler has another butler's MCP endpoint in config |
| Routing bypass | Classification and dispatch only run on switchboard |
| Unlogged communication | All inter-butler calls go through switchboard's `routing_log` |

### E2E Communication Security Tests

| Test | What It Validates |
|------|-------------------|
| No direct butler-to-butler MCP calls | Non-switchboard butler has no MCPClient for other butlers |
| All routing logged | Every successful route has a `routing_log` entry |
| Switchboard-only classification | `classify_message` tool only exists on switchboard |

## Approval Gate Security

### Gated Tools

Some tools require explicit approval before execution. The approval gate system
intercepts tool calls and holds them pending approval:

```python
# butler.toml
[approval_gates]
sensitive_tools = ["contact_delete", "medication_stop"]
approval_mode = "always"  # or "conditional"
```

### E2E Approval Gate Tests

| Test | What It Validates |
|------|-------------------|
| Gated tool blocked without approval | CC calls `contact_delete` → call held, not executed |
| Gated tool proceeds with approval | Approval granted → tool executes, row deleted |
| Non-gated tools unaffected | `measurement_log` executes immediately, no approval check |
| Approval timeout | Approval not granted within timeout → tool call rejected |

## Threat Model Summary

| Threat | Layer | Mitigation | E2E Testable? |
|--------|-------|-----------|---------------|
| CC reads host env | Spawner | `_build_env` allowlist | Yes |
| CC calls wrong butler's tools | MCP config | Single-butler MCP config | Yes |
| CC writes to wrong DB | Database | Per-butler connection pool | Yes |
| Credential leakage in logs | Logging | Redaction, arg sensitivity | Yes |
| Butler-to-butler backdoor | Architecture | Switchboard-only routing | Yes |
| Unauthorized tool execution | Approvals | Approval gates | Yes |
| Secret in config file | Startup | `detect_secrets()` scan | Yes |
| API key theft via prompt injection | CC runtime | Env isolation + model guardrails | Partially |
