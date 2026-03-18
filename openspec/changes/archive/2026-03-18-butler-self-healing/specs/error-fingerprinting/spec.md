# Error Fingerprinting

## Purpose

Deterministic error classification that extracts a stable fingerprint from session failures. Maps duplicate errors to the same key so the self-healing dispatcher can deduplicate investigation attempts. Includes severity scoring for dispatch prioritization. Handles all exception types that can occur within the spawner's `_run()` method scope.

## ADDED Requirements

### Requirement: Fingerprint Computation
The system SHALL compute a fingerprint for each failed session by hashing a structured tuple of `(exception_type, call_site, sanitized_message_pattern)` using SHA-256. The fingerprint is a 64-character lowercase hex string.

#### Scenario: Fingerprint from a standard exception
- **WHEN** a session fails with `asyncpg.exceptions.UndefinedTableError: relation "foo_123" does not exist` at `src/butlers/modules/email.py:send_email`
- **THEN** the fingerprint is `SHA-256("asyncpg.exceptions.UndefinedTableError||src/butlers/modules/email.py:send_email||relation <ID> does not exist")`

#### Scenario: Same root cause produces identical fingerprint
- **WHEN** two sessions fail with `KeyError: 'missing_key'` at the same call site but different timestamps
- **THEN** both sessions produce the same fingerprint

#### Scenario: Different call sites produce different fingerprints
- **WHEN** two sessions fail with `KeyError: 'x'` but at different call sites
- **THEN** they produce different fingerprints

### Requirement: Exception Type Extraction
The system SHALL extract the fully qualified class name of the exception (e.g. `asyncpg.exceptions.UndefinedTableError`, not just `UndefinedTableError`).

#### Scenario: Built-in exception type
- **WHEN** the exception is `ValueError("invalid literal")`
- **THEN** the exception type component is `builtins.ValueError`

#### Scenario: Third-party exception type
- **WHEN** the exception is `asyncpg.exceptions.PostgresError("connection lost")`
- **THEN** the exception type component is `asyncpg.exceptions.PostgresError`

#### Scenario: Chained exception uses root cause
- **WHEN** the exception is `RuntimeError("failed")` caused by `ConnectionRefusedError("port 5432")`
- **THEN** the exception type component is `builtins.RuntimeError` (the outermost exception, not the chain — the call site disambiguates root cause)

### Requirement: Call Site Extraction
The system SHALL extract the call site as `<relative_file_path>:<function_name>` from the innermost non-stdlib, non-third-party frame in the traceback. File paths SHALL be relative to the repository root. Line numbers SHALL NOT be included (they shift across commits).

#### Scenario: Call site from application code
- **WHEN** the traceback's innermost app frame is `send_email()` in `src/butlers/modules/email.py` at line 42
- **THEN** the call site is `src/butlers/modules/email.py:send_email`

#### Scenario: All frames are stdlib or third-party
- **WHEN** the traceback contains no application code frames
- **THEN** the call site is `<unknown>:<unknown>`

#### Scenario: App code detection heuristic
- **WHEN** determining whether a frame is application code
- **THEN** frames with paths under `src/butlers/`, `roster/`, `tests/`, or `conftest.py` are considered application code
- **AND** frames in site-packages, stdlib, or virtualenv paths are excluded

### Requirement: Message Sanitization
The system SHALL replace dynamic values in the error message with typed placeholders before hashing. This collapses semantically equivalent errors into a single fingerprint.

#### Scenario: UUID replaced
- **WHEN** the error message contains `session 550e8400-e29b-41d4-a716-446655440000 not found`
- **THEN** the sanitized message is `session <UUID> not found`

#### Scenario: Timestamp replaced
- **WHEN** the error message contains `timeout at 2026-03-17T14:30:00Z`
- **THEN** the sanitized message is `timeout at <TS>`

#### Scenario: Numeric ID replaced
- **WHEN** the error message contains `row 12345 missing`
- **THEN** the sanitized message is `row <ID> missing`

#### Scenario: Multiple dynamic values in one message
- **WHEN** the error message contains `user 550e8400-e29b-41d4-a716-446655440000 failed at 2026-03-17 with code 500`
- **THEN** the sanitized message is `user <UUID> failed at <TS> with code <ID>`

#### Scenario: Empty or None error message
- **WHEN** `str(exc)` returns an empty string or the exception has no message
- **THEN** the sanitized message component is `<empty>`
- **AND** the fingerprint is still computed from `(exception_type, call_site, "<empty>")`

#### Scenario: Very long error message is truncated before hashing
- **WHEN** the sanitized error message exceeds 500 characters
- **THEN** it is truncated to 500 characters before hashing
- **AND** this prevents hash instability from variable-length tail content (e.g. full SQL query dumps)

### Requirement: Severity Scoring
The system SHALL assign an integer severity score (0=critical, 1=high, 2=medium, 3=low, 4=info) to each fingerprint based on the exception type and call site. The scoring covers all exception types that can occur within the spawner's `_run()` method, including pre-runtime failures.

#### Scenario: Database connection errors are critical
- **WHEN** the exception type is a subclass of `asyncpg.PostgresError`, `asyncpg.InterfaceError`, or involves connection pool exhaustion
- **THEN** the severity is `0` (critical)

#### Scenario: Credential and secret resolution errors are critical
- **WHEN** the exception originates from credential resolution (`CredentialStore.resolve()`) or secret fetching
- **THEN** the severity is `0` (critical) — indicates broken infrastructure, not a code bug

#### Scenario: Runtime adapter errors are high
- **WHEN** the call site is within `src/butlers/core/runtimes/`
- **THEN** the severity is `1` (high)

#### Scenario: System prompt or config resolution errors are high
- **WHEN** the exception originates from `read_system_prompt()`, `_build_env()`, `_resolve_provider_config()`, or adapter initialization
- **THEN** the severity is `1` (high) — these are infrastructure errors that block all sessions

#### Scenario: Module tool errors are medium
- **WHEN** the call site is within `src/butlers/modules/`
- **THEN** the severity is `2` (medium)

#### Scenario: Memory context errors are low
- **WHEN** the exception originates from `fetch_memory_context()` or `store_session_episode()`
- **THEN** the severity is `3` (low) — memory is fail-open and non-critical

#### Scenario: Cancellation errors are excluded
- **WHEN** the exception is `asyncio.CancelledError` or `KeyboardInterrupt`
- **THEN** the severity is `4` (info) — these are intentional terminations, not bugs
- **AND** with the default severity threshold of `2`, these will never trigger healing

#### Scenario: Unknown errors default to medium
- **WHEN** the exception cannot be classified by any specific rule
- **THEN** the severity is `2` (medium)

### Requirement: Fingerprint Scope Boundary
Only exceptions caught within the spawner's `_run()` try/except block SHALL be fingerprinted. Exceptions in the `finally` block (metrics recording, span cleanup, context clearing) SHALL NOT trigger fingerprinting — they are infrastructure cleanup and never represent butler-domain bugs.

#### Scenario: Exception in finally block not fingerprinted
- **WHEN** an exception occurs during `clear_active_session_context()` or `span.end()` in the finally block
- **THEN** no fingerprint is computed and no healing dispatch occurs

### Requirement: Dual-Input Fingerprinting
The system SHALL support two input modes for fingerprint computation: raw Python exception objects (spawner fallback path) and structured string fields (module MCP tool path). Both modes produce identical fingerprints for the same underlying error.

#### Scenario: Raw exception input (spawner fallback)
- **WHEN** `compute_fingerprint(exc, tb)` is called with a Python exception and traceback
- **THEN** it extracts `exception_type`, `call_site`, and `sanitized_message` from the exception/traceback
- **AND** returns a `FingerprintResult` named tuple

#### Scenario: Structured input (module path)
- **WHEN** `compute_fingerprint_from_report(error_type, error_message, call_site, traceback_str, severity_hint)` is called with strings from the MCP tool
- **THEN** it uses the provided `error_type` directly as `exception_type`
- **AND** extracts `call_site` from the provided `call_site` string or parses it from `traceback_str` if not provided
- **AND** sanitizes `error_message` using the same placeholder rules
- **AND** returns a `FingerprintResult` named tuple

#### Scenario: Same error produces same fingerprint regardless of input path
- **WHEN** a `KeyError("missing_key")` at `src/butlers/modules/email.py:send_email` is reported both via `compute_fingerprint(exc, tb)` and via `compute_fingerprint_from_report("builtins.KeyError", "missing_key", "src/butlers/modules/email.py:send_email", ...)`
- **THEN** both produce the same fingerprint hex string

### Requirement: FingerprintResult Type
The system SHALL return fingerprint results as a named tuple / dataclass rather than a bare tuple, for clarity across the module and spawner paths.

#### Scenario: FingerprintResult fields
- **WHEN** any fingerprint function returns
- **THEN** it returns a `FingerprintResult` with fields: `fingerprint` (str, 64-char hex), `severity` (int, 0-4), `exception_type` (str), `call_site` (str), `sanitized_message` (str)

### Requirement: Severity Hint from Agent
When the module path provides a `severity_hint` from the butler agent, the system SHALL use it as a tiebreaker when automatic severity scoring produces `medium` (the default).

#### Scenario: Agent severity hint upgrades default
- **WHEN** automatic scoring returns `2` (medium) and the agent provided `severity_hint="high"`
- **THEN** the final severity is `1` (high) — agent hint overrides the default

#### Scenario: Agent severity hint does not override specific rules
- **WHEN** automatic scoring returns `0` (critical — DB error) and the agent provided `severity_hint="low"`
- **THEN** the final severity is `0` (critical) — specific rules override agent hint

#### Scenario: No severity hint
- **WHEN** no `severity_hint` is provided (spawner fallback or agent didn't include one)
- **THEN** automatic scoring is used directly

### Requirement: Legacy Function Signature (backwards compatibility)
The system SHALL also expose the original `compute_fingerprint(exc, tb)` signature for the spawner fallback path.

#### Scenario: Spawner fallback uses raw exception
- **WHEN** `compute_fingerprint(exc, tb)` is called
- **THEN** it returns a `FingerprintResult` (same type as the structured path)

#### Scenario: Exception without traceback
- **WHEN** `compute_fingerprint(exc, None)` is called with a None traceback
- **THEN** the call site falls back to `<unknown>:<unknown>` and the fingerprint is still computed
