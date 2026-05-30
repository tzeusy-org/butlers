# Adapter Error Surface Audit — 2026-05-25

**Issue:** bu-ojiij.5  
**Context:** bu-ojiij (Model catalog same-tier failover epic)  
**Siblings implemented:** .1 resolver, .2 classifier, .3 Spawner orchestration

## Purpose

Audit each adapter's error surface to determine whether the failover classifier
(`src/butlers/core/failover_classifier.py`, bu-ojiij.2) can reliably distinguish:

- **Failover-eligible**: systemic pre-invocation failures (rate limit, auth, model-unavailable,
  timeout, MCP discovery) — before any tool call was executed.
- **Hard-terminal (no-failover)**: any failure after tool calls, or guardrail/business failures.
- **Adapter-internal retry**: Codex/MCP discovery retries that are NOT cross-model failover events.

---

## Adapter Audit Table

| Adapter | Pre-tool-call failure signal | Tool-call boundary detection | Adapter-internal retry visibility | Rate-limit / Auth / Model-unavailable / Timeout coverage | Gap → fix applied |
|---|---|---|---|---|---|
| **Codex** (`codex.py`) | `RuntimeError("Codex CLI exited with code N: <error_detail>")`, `FileNotFoundError`, `TimeoutError`, `MCPToolDiscoveryError` | `last_process_info["is_pre_tool_call"]` (new); spawner also captures daemon-side tool calls via `runtime_session_id` | `MCPToolDiscoveryError.internal_retry_count` (new); `last_process_info["retry_attempted"]`, `["attempt_count"]` | All four map: rate-limit via `_RATE_LIMIT_MARKERS`, auth via `_PROVIDER_AUTH_MARKERS`, model-unavailable via same, timeout via `TimeoutError` | `is_pre_tool_call` added to `last_process_info` on non-zero exit + timeout; `internal_retry_count` + `is_pre_tool_call` added to `MCPToolDiscoveryError` |
| **ClaudeCode** (`claude_code.py`) | `RuntimeError("Claude CLI exited with code N: <detail>")`, `FileNotFoundError`, `TimeoutError` | `last_process_info["is_pre_tool_call"]` (new) | None (Claude CLI has no internal MCP retry loop) | Rate-limit: `RuntimeError` message with `"rate limit"` etc.; Auth: `"authentication"`/`"unauthorized"` etc.; Model-unavailable: `"model unavailable"` etc.; Timeout: `TimeoutError` | `error_detail` field added to `last_process_info` on non-zero exit (was absent, Codex already had it); `is_pre_tool_call=True` added on non-zero exit + timeout |
| **Gemini** (`gemini.py`) | `RuntimeError("Gemini CLI exited with code N: <detail>")`, `FileNotFoundError`, `TimeoutError` | `last_process_info["is_pre_tool_call"]` (new) | None (no internal retry loop) | Rate-limit, auth, model-unavailable: via `RuntimeError` message matching classifier markers; Timeout: `TimeoutError` | `error_detail` added to `last_process_info` on non-zero exit (was absent); `is_pre_tool_call=True` added on non-zero exit + timeout |
| **OpenCode** (`opencode.py`) | `RuntimeError("OpenCode CLI exited with code N: ...")`, `RuntimeError("OpenCode CLI error (exit 0): ...")` for `ProviderModelNotFoundError`/`AuthenticationError` detected via stderr, `FileNotFoundError`, `TimeoutError` | `last_process_info["is_pre_tool_call"]` (new) | None (no internal retry loop) | **Gap identified**: `ProviderModelNotFoundError` (exit 0 + stderr) was not matched by classifier's `_PROVIDER_AUTH_MARKERS`; Auth/model errors on non-zero exit already match. | `error_detail` + `is_pre_tool_call=True` added on non-zero exit + exit-0-stderr path + timeout; classifier extended with `"providermodelnotfounderror"` and `"model not found:"` markers |

---

## Per-Adapter Detailed Findings

### Codex (`src/butlers/core/runtimes/codex.py`)

**Pre-tool-call failure signal:**  
- Non-zero exit: `RuntimeError("Codex CLI exited with code N: <error_detail>")` — `error_detail` already set in `last_process_info["error_detail"]` before this bead.
- Missing binary: `FileNotFoundError("Codex CLI binary not found on PATH...")` raised by `_find_codex_binary()`.
- Timeout: `TimeoutError("Codex CLI timed out after N seconds")`.
- MCP discovery failure: `MCPToolDiscoveryError(...)` — a `RuntimeError` subclass with structured fields.

**Tool-call boundary detection:**  
- Before this bead: no `is_pre_tool_call` field in `last_process_info`. The classifier relied solely on `tool_calls` passed by the spawner from daemon-side capture.
- After this bead: `last_process_info["is_pre_tool_call"] = True` set on all failure paths (non-zero exit, timeout). For the recovered-nonzero-exit path, `is_pre_tool_call` reflects whether the recovered payload contained tool calls.
- The spawner continues to use daemon-side tool-call capture as the authoritative side-effect gate; `is_pre_tool_call` from the adapter is a best-effort supplement.

**Adapter-internal retry visibility:**  
- Transient CLI retries (`_TRANSIENT_CLI_RETRY_DELAYS`): visible via `last_process_info["retry_attempted"]`, `["retry_succeeded"]`, `["attempt_count"]`. These are already set before this bead. The spawner sees a single `RuntimeError` regardless of retry count.
- MCP discovery retries (`_MCP_RETRY_DELAYS`): before this bead, `MCPToolDiscoveryError` had no field exposing how many adapter-internal retries occurred. After this bead: `MCPToolDiscoveryError.internal_retry_count` = number of retries (1 initial + N retries = N+1 attempts total; `internal_retry_count = attempts - 1`).

**Rate-limit / Auth / Model-unavailable / Timeout coverage:**  
All four map cleanly to failover-eligible outcomes via classifier pattern matching:
- Rate-limit: `"rate limit"`, `"too many requests"`, `"quota exceeded"`, `"compact_remote"` etc.
- Auth: `"authentication"`, `"unauthorized"`, `"api key"`, `"credential"` etc.
- Model-unavailable: `"model not found"`, `"model unavailable"`, `"no such model"` etc.
- Timeout: `TimeoutError` class (GATE 5 in classifier).

**Gaps and fixes applied:**  
1. **`is_pre_tool_call` missing** → Added `last_process_info["is_pre_tool_call"] = True` on non-zero exit and timeout paths.  
2. **`MCPToolDiscoveryError` had no provenance fields** → Added `is_pre_tool_call: bool = True` and `internal_retry_count: int` attributes to the exception class. Updated the single raise site to pass `internal_retry_count=attempt_count - 1`.

---

### ClaudeCode (`src/butlers/core/runtimes/claude_code.py`)

**Pre-tool-call failure signal:**  
- Non-zero exit: `RuntimeError("Claude CLI exited with code N: <detail>")` where `<detail>` = `stderr.strip() or stdout.strip() or "exit code N"`.
- Missing binary: `FileNotFoundError("Claude CLI binary not found on PATH...")`.
- Timeout: `TimeoutError("Claude CLI timed out after N seconds")`.

**Tool-call boundary detection:**  
Before this bead: no `is_pre_tool_call` in `last_process_info`. After: `is_pre_tool_call = True` on all failure paths.

**Adapter-internal retry visibility:**  
Claude Code adapter has no internal retry loop. The SDK handles retries at a lower level, invisible to the adapter. No `retry_attempted`/`attempt_count` fields present.

**Rate-limit / Auth / Model-unavailable / Timeout coverage:**  
All four map via the RuntimeError message classifier. However, the classifier previously had to match against the full `RuntimeError` message which already includes `"Claude CLI exited with code N:"` prefix — the meaningful content is in the suffix.

**Gaps and fixes applied:**  
1. **`error_detail` missing from `last_process_info`** → Added `self._last_process_info["error_detail"] = error_detail` on non-zero exit. This mirrors Codex's behavior and gives the classifier a cleaner field to inspect if needed in future enhancements.  
2. **`is_pre_tool_call` missing** → Added `is_pre_tool_call = True` on non-zero exit and timeout.

---

### Gemini (`src/butlers/core/runtimes/gemini.py`)

**Pre-tool-call failure signal:**  
- Non-zero exit: `RuntimeError("Gemini CLI exited with code N: <detail>")`.
- Missing binary: `FileNotFoundError("Gemini CLI binary not found on PATH...")`.
- Timeout: `TimeoutError("Gemini CLI timed out after N seconds")`.

**Tool-call boundary detection:**  
Before this bead: no `is_pre_tool_call`. After: `is_pre_tool_call = True` on all failure paths.

**Adapter-internal retry visibility:**  
No internal retry loop. OAuth auth failures and model-not-found errors surface immediately as non-zero exit.

**Rate-limit / Auth / Model-unavailable / Timeout coverage:**  
All four map via classifier message matching. Gemini's error messages for these conditions typically include recognizable substrings.

**Gaps and fixes applied:**  
1. **`error_detail` missing from `last_process_info`** → Added on non-zero exit.  
2. **`is_pre_tool_call` missing** → Added on non-zero exit and timeout.

---

### OpenCode (`src/butlers/core/runtimes/opencode.py`)

**Pre-tool-call failure signal:**  
- Non-zero exit: `RuntimeError("OpenCode CLI exited with code N: <detail>")`.
- Exit-0 + stderr detection: `RuntimeError("OpenCode CLI error (exit 0): <stderr snippet>")` for `ProviderModelNotFoundError`, `Model not found:`, `AuthenticationError` patterns in stderr.
- Missing binary: `FileNotFoundError("OpenCode CLI binary not found on PATH...")`.
- Timeout: `TimeoutError("OpenCode CLI timed out after N seconds")`.

**Tool-call boundary detection:**  
Before: no `is_pre_tool_call`. After: `is_pre_tool_call = True` on non-zero exit, exit-0-stderr path, and timeout.

**Adapter-internal retry visibility:**  
No internal retry loop.

**Rate-limit / Auth / Model-unavailable / Timeout coverage:**  
- **Gap found**: `ProviderModelNotFoundError` raised via exit-0+stderr produces `RuntimeError("OpenCode CLI error (exit 0): ProviderModelNotFoundError: ...")`. The classifier's `_PROVIDER_AUTH_MARKERS` did not include this string.
- **Fix**: Added `"providermodelnotfounderror"` and `"model not found:"` to `_PROVIDER_AUTH_MARKERS` in `failover_classifier.py`.
- Rate-limit / Auth on non-zero exit: already match via existing classifier markers.
- Timeout: `TimeoutError` class → GATE 5 in classifier.

**Gaps and fixes applied:**  
1. **`error_detail` missing from `last_process_info`** → Added on non-zero exit and exit-0-stderr paths.  
2. **`is_pre_tool_call` missing** → Added on all failure paths.  
3. **Classifier doesn't match `ProviderModelNotFoundError`** → Added to `_PROVIDER_AUTH_MARKERS` in classifier (small targeted update per issue scope allowance).

---

## New Fields Summary

### `MCPToolDiscoveryError` (codex.py)

```python
exc.is_pre_tool_call: bool   # Always True — discovery happens before any tool call
exc.internal_retry_count: int  # Adapter-internal MCP retries (spawner must NOT count as failover)
```

**Usage by spawner:** `internal_retry_count` lets the spawner accurately attribute attempt provenance:
```
logical_failover_attempts = 1   # one MCPToolDiscoveryError = one logical attempt
adapter_internal_retries = exc.internal_retry_count  # informational only
```

### `last_process_info` new fields (all subprocess adapters)

| Field | Type | Adapters | When set | Meaning |
|---|---|---|---|---|
| `error_detail` | `str` | Codex (existing), ClaudeCode (new), Gemini (new), OpenCode (new) | Non-zero exit, exit-0-stderr (OpenCode) | Structured error text extracted from stdout/stderr; cleaner than full `RuntimeError` message for classifier matching |
| `is_pre_tool_call` | `bool` | All subprocess adapters (new) | All failure paths (non-zero exit, timeout) | `True` when the adapter did not observe any tool calls before failure; combines with spawner's daemon-side capture for the authoritative side-effect gate |

---

## What Is Out Of Scope (Filed as Follow-ups)

No out-of-scope gaps were found that require new beads. All identified gaps were addressable within the bead's defined scope:
- Exception class extension (adding fields to `MCPToolDiscoveryError`)
- `last_process_info` enrichment (adding `error_detail` and `is_pre_tool_call`)
- Small classifier update to ingest the new `ProviderModelNotFoundError` signal from OpenCode

Adapters do not need retry-logic rewrites or new DB tables. The provenance table is bu-ojiij.4.
