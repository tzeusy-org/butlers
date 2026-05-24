# Failover Classifier Adapter Error-Surface Audit

**Date:** 2026-05-24
**Issue:** bu-zhgsv
**Spec:** `openspec/changes/add-same-tier-model-failover` (tasks 6.1–6.3)
**Scope:** Audit Codex, Claude Code, Gemini, and OpenCode adapter exception surfaces
against the failover classifier rules in `src/butlers/core/failover_classifier.py`.

---

## 1. Classifier Rule Summary

The classifier in `src/butlers/core/failover_classifier.py` uses a default-closed
contract: `eligible=False` unless an explicit allow-list condition matches.

**Gate order:**
1. GATE 1 (side-effect): Any captured MCP tool call → suppress (regardless of error class).
2. GATE 2 (guardrail): `_GUARDRAIL_MARKERS` in message → suppress.
3. GATE 3 (MCP discovery): `MCPToolDiscoveryError` class name (no tool calls) → eligible.
4. GATE 4 (missing binary): `FileNotFoundError` → eligible.
5. GATE 5 (timeout before work): `TimeoutError` (no tool calls) → eligible.
6. GATE 6 (`RuntimeError` with recognized patterns):
   - `_RUNTIME_CONFIG_MARKERS` → eligible
   - `_PROVIDER_AUTH_MARKERS` → eligible
   - `_RATE_LIMIT_MARKERS` → eligible
   - `_MCP_DISCOVERY_MARKERS` → eligible
   - Unmatched → suppress (default-closed within `RuntimeError`)
7. GATE 7 (`ValueError` with `_RUNTIME_CONFIG_MARKERS`) → eligible; else suppress.
8. DEFAULT: Unknown exception class → suppress.

---

## 2. Per-Adapter Error Inventory

### 2.1 Codex Adapter (`src/butlers/core/runtimes/codex.py`)

| Error Path | Exception Class | Typical Message | Classifier Verdict | Coverage |
|---|---|---|---|---|
| Binary not on PATH | `FileNotFoundError` | `"Codex CLI binary not found on PATH. Install it with: npm install -g @openai/codex"` | GATE 4 → eligible | ✅ |
| Non-zero exit code (general) | `RuntimeError` | `"Codex CLI exited with code {N}: {error_detail}"` where detail comes from stderr/stdout | GATE 6 → depends on detail | ⚠️ see §3.1 |
| Transient backend failure (`compact_remote`, `model is at capacity`) | `RuntimeError` | `"Codex CLI exited with code {N}: codex_core::compact_remote ..."` or `"... model is at capacity"` | GATE 6 → `"model is at capacity"` matches `_RATE_LIMIT_MARKERS` → eligible; `compact_remote` → **MISS** | ⚠️ see §3.2 |
| MCP tool discovery failure (exhausted retries) | `MCPToolDiscoveryError` (subclass of `RuntimeError`) | `"MCP tool discovery failed after {N} attempts..."` | GATE 3 → eligible (with no tool calls) | ✅ |
| Subprocess timeout | `TimeoutError` | `"Codex CLI timed out after {N} seconds"` | GATE 5 → eligible (no tool calls) or suppressed (tool calls present) | ✅ |
| Auth.json missing (no Codex login) | `RuntimeError` (indirectly: Codex exits non-zero with auth error in stderr/stdout) | Codex stderr: `"authentication"` or `"unauthorized"` | GATE 6 → `_PROVIDER_AUTH_MARKERS` matches | ✅ |
| Token refresh failed / `refresh_token_reused` | `RuntimeError` | Detail depends on Codex auth error output; likely contains `"authentication"` or `"token"` | GATE 6 → likely matches `"authentication"` or `"token expired"` | ✅ (likely) |
| MCP transport mismatch (augmented message) | `MCPToolDiscoveryError` or `RuntimeError` | Contains `"streamable_http"`, `"method not allowed"`, `"unsupported media type"` appended by `_augment_transport_error_detail` | GATE 3 or GATE 6 `_MCP_DISCOVERY_MARKERS` → eligible | ✅ |
| Guardrail termination (degenerate loop, budget) | `RuntimeError` | Codex session output contains `"degenerate_tool_loop"`, `"tool_call_budget_exceeded"`, `"token_budget_exceeded"` surfaced as error detail | GATE 2 → suppressed | ✅ |
| Non-zero exit, completed response recovered | Returns normally (no exception) | N/A — `_recover_completed_nonzero_exit` returns the payload | Never reaches classifier | ✅ |
| `codex_core::compact_remote` / `remote compaction failed` (transient, non-zero exit) | `RuntimeError` | `"Codex CLI exited with code {N}: codex_core::compact_remote ..."` | GATE 6: does not match any allow-list marker → **suppressed** (default-closed) | ⚠️ FALSE NEGATIVE §3.2 |

#### Codex adapter-internal retry behavior

The Codex adapter has **two internal retry loops** that are separate from the
spawner failover loop:

1. **Transient CLI retry** (`_TRANSIENT_CLI_RETRY_DELAYS = (1.0, 3.0)`): Fires on
   `_should_retry_transient_cli_failure()` hits (`compact_remote`, `model is at capacity`).
   If all internal retries fail, it re-raises the original `RuntimeError` to the spawner.

2. **MCP discovery retry** (`_MCP_RETRY_DELAYS = (2.0, 5.0)`): Fires when zero MCP
   tool calls are observed and stderr suggests transport failure. If all retries fail,
   raises `MCPToolDiscoveryError`.

The classifier sees only the exception that escapes these loops. This is consistent
with task 6.3 ("keep adapter-internal retry behavior separate from cross-model failover").

---

### 2.2 Claude Code Adapter (`src/butlers/core/runtimes/claude_code.py`)

| Error Path | Exception Class | Typical Message | Classifier Verdict | Coverage |
|---|---|---|---|---|
| Binary not on PATH | `FileNotFoundError` | `"Claude CLI binary not found on PATH. Install it with: npm install -g @anthropic-ai/claude-code"` | GATE 4 → eligible | ✅ |
| Non-zero exit code | `RuntimeError` | `"Claude CLI exited with code {N}: {detail}"` where detail is stderr or stdout | GATE 6 → depends on detail | ⚠️ see §3.3 |
| Non-zero with auth error in stderr | `RuntimeError` | `"Claude CLI exited with code 401: unauthorized"` | GATE 6 → `_PROVIDER_AUTH_MARKERS` matches `"unauthorized"` → eligible | ✅ |
| Non-zero with rate limit | `RuntimeError` | `"Claude CLI exited with code 429: too many requests"` | GATE 6 → `_RATE_LIMIT_MARKERS` matches → eligible | ✅ |
| Non-zero with model error | `RuntimeError` | `"Claude CLI exited with code 400: model not found"` | GATE 6 → `_PROVIDER_AUTH_MARKERS` matches `"model not found"` → eligible | ✅ |
| Subprocess timeout | `TimeoutError` | `"Claude CLI timed out after {N} seconds"` | GATE 5 → eligible / suppressed per tool calls | ✅ |
| ANTHROPIC_API_KEY credential store failure | Does not propagate (silently falls back to env) | N/A | N/A | ✅ |
| Non-zero with non-error content (e.g. "Error: ...") | `RuntimeError` | `"Claude CLI exited with code 1: Error: ..."` | GATE 6 → depends on content; unknown patterns → suppressed | ✅ (default-closed is correct) |
| Network connectivity failure surfaced in stderr | `RuntimeError` | `"Claude CLI exited with code 1: connection refused"` | GATE 6 → `_PROVIDER_AUTH_MARKERS` matches `"connection refused"` → eligible | ✅ |

**Key observation:** The Claude Code adapter raises `RuntimeError` for all non-zero
exit codes with the message `"Claude CLI exited with code {N}: {detail}"`. The detail
is the raw `stderr.strip() or stdout.strip()` content, which directly contains the
Anthropic API error message. This means the classifier sees provider-originated messages
verbatim, which is the best possible pattern-matching surface.

---

### 2.3 Gemini Adapter (`src/butlers/core/runtimes/gemini.py`)

| Error Path | Exception Class | Typical Message | Classifier Verdict | Coverage |
|---|---|---|---|---|
| Binary not on PATH | `FileNotFoundError` | `"Gemini CLI binary not found on PATH. Install it with: npm install -g @anthropic-ai/gemini-cli or see https://github.com/google-gemini/gemini-cli"` | GATE 4 → eligible | ✅ |
| Subprocess timeout | `TimeoutError` | `"Gemini CLI timed out after {N} seconds"` | GATE 5 → eligible (no tool calls) | ✅ |
| Non-zero exit code | **NOT raised** — returns `("Error: {detail}", [])` instead | N/A — failure is encoded as result text, not exception | **Never reaches classifier** | ⚠️ FALSE NEGATIVE §3.4 |
| Authentication failure (exit non-zero) | **NOT raised** — absorbed into result text | `"Error: authentication failed"` as `result_text` | **Classifier never sees it** | ⚠️ FALSE NEGATIVE §3.4 |
| Rate limit (exit non-zero) | **NOT raised** — absorbed into result text | `"Error: too many requests"` as `result_text` | **Classifier never sees it** | ⚠️ FALSE NEGATIVE §3.4 |
| Network error (exit non-zero) | **NOT raised** — absorbed into result text | `"Error: connection refused"` as `result_text` | **Classifier never sees it** | ⚠️ FALSE NEGATIVE §3.4 |

**Critical gap:** The Gemini adapter's `invoke()` does **not** raise a `RuntimeError`
for non-zero exit codes. It calls `_parse_gemini_output()`, which encodes failures
as a return value `("Error: {detail}", [], None)`. The spawner receives `success=True`
(no exception), and the session completes with an error string in the output rather than
a failed session. This means:

1. The spawner's failover loop never fires for Gemini provider failures.
2. The classifier is never consulted.
3. Auth, rate-limit, model-not-found, and network failures are all silently
   absorbed as successful sessions with error text.

This is a significant behavioral gap compared to the other three adapters.

---

### 2.4 OpenCode Adapter (`src/butlers/core/runtimes/opencode.py`)

| Error Path | Exception Class | Typical Message | Classifier Verdict | Coverage |
|---|---|---|---|---|
| Binary not on PATH | `FileNotFoundError` | `"OpenCode CLI binary not found on PATH. Install it with: npm install -g opencode-ai or see https://opencode.ai/docs"` | GATE 4 → eligible | ✅ |
| Non-zero exit code | `RuntimeError` | `"OpenCode CLI exited with code {N}: {detail}"` | GATE 6 → depends on detail | ⚠️ see §3.3 |
| Exit 0 with `ProviderModelNotFoundError` in stderr | `RuntimeError` | `"OpenCode CLI error (exit 0): ProviderModelNotFoundError: ..."` | GATE 6 → `_PROVIDER_AUTH_MARKERS` matches `"model not found"` **if** the pattern appears in the message; the actual message is `"ProviderModelNotFoundError: Model not found: ..."`. Does `"model not found"` appear? The check pattern is `"Model not found:"` in stderr, and the exception message contains the full stderr → "model not found" (lowercased) present → eligible | ✅ |
| Exit 0 with `AuthenticationError` in stderr | `RuntimeError` | `"OpenCode CLI error (exit 0): AuthenticationError: ..."` | GATE 6 → `_PROVIDER_AUTH_MARKERS` has `"authentication"` → eligible | ✅ |
| Exit 0 with `Model not found:` in stderr | `RuntimeError` | `"OpenCode CLI error (exit 0): Model not found: ..."` | GATE 6 → `"model not found"` in `_PROVIDER_AUTH_MARKERS` → eligible | ✅ |
| Subprocess timeout | `TimeoutError` | `"OpenCode CLI timed out after {N} seconds"` | GATE 5 → eligible (no tool calls) | ✅ |
| Network failure (non-zero exit) | `RuntimeError` | `"OpenCode CLI exited with code 1: connection refused"` | GATE 6 → `_PROVIDER_AUTH_MARKERS` matches → eligible | ✅ |
| Rate limit (non-zero exit) | `RuntimeError` | `"OpenCode CLI exited with code 429: too many requests"` | GATE 6 → `_RATE_LIMIT_MARKERS` matches → eligible | ✅ |

**OpenCode special case:** The adapter detects a specific set of semantic error patterns
in stderr when the process exits 0 (`ProviderModelNotFoundError`, `Model not found:`,
`AuthenticationError`) and converts them to `RuntimeError` explicitly. This is good
design: the classifier can reliably pattern-match on these standardized prefixes.

**Gap note:** The `exit 0` detection only checks three patterns. Other semantic
failures (e.g. quota errors, permission errors) that OpenCode returns with exit 0
and only stderr content would NOT be converted to `RuntimeError` and would be treated
as successful sessions with error-like output text.

---

## 3. Coverage Gaps

### 3.1 Codex: Non-zero exit with non-standard error details

**Severity:** Low

When the Codex CLI exits non-zero with a custom error message that does not match
any `_PROVIDER_AUTH_MARKERS`, `_RATE_LIMIT_MARKERS`, `_MCP_DISCOVERY_MARKERS`, or
`_RUNTIME_CONFIG_MARKERS`, the resulting `RuntimeError` falls into the default-closed
path and failover is suppressed.

For the vast majority of real-world Codex failures, the error detail will include one
of the recognized markers (auth failures produce "authentication", "unauthorized",
rate limits produce "rate limit" or "too many requests", network failures produce
"connection refused"). The current pattern coverage is reasonable.

**Recommendation:** No immediate code change needed, but consider adding Codex-specific
markers for `"openai api error"` or `"api response error"` if observed in production.

### 3.2 Codex: `compact_remote` / `remote compaction failed` (FALSE NEGATIVE)

**Severity:** Medium

**Bug:** When Codex exhausts all internal transient retries for `compact_remote`/`remote
compaction failed` errors, the raised `RuntimeError` message is:

```
"Codex CLI exited with code {N}: codex_core::compact_remote ..."
```

or

```
"Codex CLI exited with code {N}: remote compaction failed ..."
```

These messages do **not** match any `_PROVIDER_AUTH_MARKERS`, `_RATE_LIMIT_MARKERS`,
`_MCP_DISCOVERY_MARKERS`, or `_RUNTIME_CONFIG_MARKERS`. As a result, the classifier
returns `eligible=False` (default-closed), suppressing failover.

**Expected behavior:** `compact_remote` failures are transient Codex backend failures.
After the adapter's internal retries are exhausted, the spawner should attempt failover
to another same-tier model. The `"model is at capacity"` marker already handles one case
(`_looks_like_transient_cli_failure` checks both `compact_remote` and `model is at capacity`),
but only `"model is at capacity"` is in `_RATE_LIMIT_MARKERS`. The `compact_remote` path
is not covered.

**Fix:** Add `"compact_remote"` and `"remote compaction failed"` to either
`_RATE_LIMIT_MARKERS` or create a dedicated `_TRANSIENT_BACKEND_MARKERS` tuple.

### 3.3 Claude Code / OpenCode: Unspecified non-zero exit codes

**Severity:** Low

For both Claude Code and OpenCode, when the CLI exits non-zero with a message not
matching any allow-list pattern, the classifier correctly suppresses failover. This
is the correct default-closed behavior. No gap.

### 3.4 Gemini: Non-zero exit does NOT raise exception (FALSE NEGATIVE)

**Severity:** High

**Bug:** The Gemini adapter absorbs all non-zero exit codes as result text, never
raising an exception. This means auth failures, rate limits, network failures, and
model-not-found errors are all returned as successful sessions with error text in the
output (`"Error: ..."` prefix).

This is architecturally inconsistent with the other three adapters, which all raise
`RuntimeError` for non-zero exits. The failover loop depends on exception propagation
to invoke the classifier.

**Impact:**
- No failover occurs for any Gemini provider-level failure.
- Sessions appear successful (no session failure recorded) but produce
  error-prefixed output text.
- If a Gemini model is misconfigured or rate-limited, the spawner will report success
  while the butler receives `"Error: ..."` as its session output.

**Fix:** The Gemini adapter's `invoke()` should raise `RuntimeError` for non-zero exit
codes, mirroring the Claude Code and OpenCode patterns:

```python
if returncode != 0:
    error_detail = stderr.strip() or stdout.strip() or f"exit code {returncode}"
    logger.error("Gemini CLI exited with code %d: %s", returncode, error_detail)
    raise RuntimeError(f"Gemini CLI exited with code {returncode}: {error_detail}")
```

The current `_parse_gemini_output()` function handles this at parse-time but the
adapter's `invoke()` does not re-raise after calling it. The fix should be in
`invoke()`, not `_parse_gemini_output()` (which is used for zero-exit parsing).

### 3.5 OpenCode: Exit-0 semantic errors only partially detected

**Severity:** Low

The three patterns checked for exit-0 semantic errors (`ProviderModelNotFoundError`,
`Model not found:`, `AuthenticationError`) cover common failure modes but not all.
Rate limit errors, network errors, and other provider-specific errors that OpenCode
returns with exit 0 and only stderr output will be treated as successful sessions.

**Recommendation:** Expand the exit-0 stderr detection in `OpenCodeAdapter.invoke()`
to include rate-limit and network-error patterns, or make the check generic by looking
for any known error keyword in stderr when stdout is empty.

---

## 4. Classifier Pattern Analysis

### 4.1 `_PROVIDER_AUTH_MARKERS` — Real-world message matching

The current patterns cover the expected provider error message vocabulary well for
Codex CLI, Claude Code CLI, and OpenCode CLI. Key validated matches:

- `"authentication"`, `"unauthorized"` → Codex exit 401, Claude Code exit 401, OpenCode `AuthenticationError`
- `"connection refused"` → Codex/Claude Code network failure
- `"model not found"`, `"no such model"` → Codex/Claude Code/OpenCode model errors
- `"service unavailable"` → Provider 503 responses

**Gap:** Codex CLI can emit `"Codex CLI exited with code 1: openai api error"` for
some backend failures. The pattern `"openai api error"` is not in any allow-list.
This would hit default-closed for RuntimeError, suppressing failover.

### 4.2 `_RATE_LIMIT_MARKERS` — Real-world message matching

Coverage is good for standard rate-limit vocabulary. The `"model is at capacity"`
marker specifically covers Codex's transient backend signal. The `"compact_remote"`
gap is documented in §3.2.

### 4.3 `_MCP_DISCOVERY_MARKERS` — Real-world message matching

The patterns match Codex's augmented transport error messages (via
`_augment_transport_error_detail`). The classifier correctly handles both
`MCPToolDiscoveryError` (class check in GATE 3) and RuntimeError-wrapped discovery
failures (GATE 6).

---

## 5. Summary Table

| Adapter | Error Category | Raises Exception | Classifier Sees It | Correct Decision | Status |
|---|---|---|---|---|---|
| Codex | Binary missing | `FileNotFoundError` | Yes (GATE 4) | eligible | ✅ |
| Codex | MCP discovery failure | `MCPToolDiscoveryError` | Yes (GATE 3) | eligible | ✅ |
| Codex | Timeout (no tool calls) | `TimeoutError` | Yes (GATE 5) | eligible | ✅ |
| Codex | Auth/provider error | `RuntimeError` | Yes (GATE 6, pattern match) | eligible | ✅ |
| Codex | Rate limit | `RuntimeError` | Yes (GATE 6, pattern match) | eligible | ✅ |
| Codex | `compact_remote` failure | `RuntimeError` | Yes (GATE 6) | suppressed (should be eligible) | ⚠️ FALSE NEGATIVE |
| Codex | Guardrail termination | `RuntimeError` | Yes (GATE 2) | suppressed | ✅ |
| Claude Code | Binary missing | `FileNotFoundError` | Yes (GATE 4) | eligible | ✅ |
| Claude Code | Non-zero exit auth/rate-limit | `RuntimeError` | Yes (GATE 6) | eligible | ✅ |
| Claude Code | Timeout | `TimeoutError` | Yes (GATE 5) | eligible | ✅ |
| Claude Code | Unknown non-zero exit | `RuntimeError` | Yes (GATE 6, default-closed) | suppressed | ✅ |
| Gemini | Binary missing | `FileNotFoundError` | Yes (GATE 4) | eligible | ✅ |
| Gemini | Timeout | `TimeoutError` | Yes (GATE 5) | eligible | ✅ |
| Gemini | Non-zero exit (any reason) | **Never raised** | **No** | should be eligible | ⚠️ FALSE NEGATIVE (HIGH) |
| OpenCode | Binary missing | `FileNotFoundError` | Yes (GATE 4) | eligible | ✅ |
| OpenCode | Non-zero exit auth/rate-limit | `RuntimeError` | Yes (GATE 6) | eligible | ✅ |
| OpenCode | Exit-0 `ProviderModelNotFoundError` | `RuntimeError` (explicit) | Yes (GATE 6) | eligible | ✅ |
| OpenCode | Exit-0 `AuthenticationError` | `RuntimeError` (explicit) | Yes (GATE 6) | eligible | ✅ |
| OpenCode | Exit-0 other semantic errors | Not raised | No | varies | ⚠️ partial gap |
| OpenCode | Timeout | `TimeoutError` | Yes (GATE 5) | eligible | ✅ |

---

## 6. Concrete Fixes

### Fix 1: Add `compact_remote` to rate-limit markers

**File:** `src/butlers/core/failover_classifier.py`
**Change:** Add `"compact_remote"` and `"remote compaction failed"` to `_RATE_LIMIT_MARKERS`.

These are Codex-internal transient backend signals that indicate the Codex backend
is temporarily overloaded, analogous to rate-limit events. After the adapter's
own internal retries are exhausted, the spawner should attempt cross-model failover.

### Fix 2: Gemini adapter should raise RuntimeError for non-zero exits

**File:** `src/butlers/core/runtimes/gemini.py`
**Change:** In `GeminiAdapter.invoke()`, raise `RuntimeError` when `returncode != 0`,
mirroring the Claude Code and OpenCode patterns.

This is the highest-severity finding. Without this fix, the entire Gemini failover
path is dead code — the spawner failover loop never fires.

---

## 7. Recommendations for Follow-Up Beads

1. **Fix Gemini adapter non-zero exit handling** (High priority): Change `GeminiAdapter.invoke()`
   to raise `RuntimeError` for non-zero exit codes. Add tests to `tests/runtimes/test_gemini.py`
   covering auth, rate-limit, and network failure exits.

2. **Add `compact_remote` to classifier** (Medium priority): Extend `_RATE_LIMIT_MARKERS` in
   `failover_classifier.py` to cover Codex transient backend failures. Add a unit test in
   `tests/core/test_failover_classifier.py`.

3. **OpenCode exit-0 semantic error expansion** (Low priority): Add rate-limit and network-error
   pattern detection to the exit-0 stderr check in `OpenCodeAdapter.invoke()`.

4. **Add adapter error surface tests** (Medium priority, task 6.2): Add or update adapter-level
   unit tests that verify:
   - Pre-tool-call CLI failure raises the expected exception class and message.
   - The classifier correctly classifies the raised exception.
   This closes the loop between adapter behavior and classifier coverage.

---

## 8. Task 6.3 — Adapter-Internal vs. Cross-Model Retry Separation

**Status: VERIFIED COMPLIANT**

- **Codex**: Has two internal retry loops (transient CLI, MCP discovery). Both exhaust
  before propagating to the spawner. `last_process_info` records `retry_attempted`,
  `retry_succeeded`, and `attempt_count` without conflating internal retries with
  spawner failover.
- **Claude Code**: No internal retry loop. Single subprocess invocation.
- **Gemini**: No internal retry loop. Single subprocess invocation.
- **OpenCode**: No internal retry loop. Single subprocess invocation.

The spawner's failover loop and the Codex adapter's internal retry loops are cleanly
separated. The spawner only sees the final exception from the adapter, not intermediate
retry attempts. Both forms of provenance (`retry_attempted` in `session_process_logs`
vs `dispatch_failures` table for spawner-level failovers) are distinct and non-conflated.

---

*Audit conducted by bu-zhgsv worker, 2026-05-24.*
