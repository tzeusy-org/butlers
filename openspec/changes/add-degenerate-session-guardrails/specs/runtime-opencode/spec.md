## MODIFIED Requirements

### Requirement: OpenCode CLI Invocation
The `OpenCodeAdapter` SHALL invoke the OpenCode CLI via `opencode run --format json` as an async subprocess. The adapter SHALL locate the `opencode` binary on PATH via `shutil.which()` and raise `FileNotFoundError` if not found. The adapter SHALL hard-terminate the subprocess when the configured timeout fires: a SIGTERM is sent first, and if the process has not exited within a short grace period (default 5 s), SIGKILL is sent. The configured timeout value is sourced from `RuntimeConfigAccessor.get().session_timeout_s` per the existing `core-spawner` spec; the 300 s default applies only when no per-butler override is present.

The strengthened timeout contract exists because a real incident (session `46f18840-4f74-4e0a-a3bf-cafa2b579f3a`, 2026-04-15) observed an opencode session running 436 s under a nominal 300 s budget. Either the timeout was not reaching the subprocess or the subprocess ignored SIGTERM; either way, the adapter must now guarantee the process is dead within `timeout + grace`.

#### Scenario: Successful invocation
- **WHEN** the adapter invokes OpenCode with a valid prompt and config
- **THEN** it runs `opencode run --format json --model <model> <prompt>` as an async subprocess
- **AND** captures stdout/stderr and parses the JSON output

#### Scenario: Binary not found
- **WHEN** the `opencode` binary is not on PATH
- **THEN** the adapter raises `FileNotFoundError` with an install hint (`npm install -g opencode-ai`)

#### Scenario: Timeout exceeded — SIGTERM then SIGKILL
- **WHEN** the OpenCode process exceeds the configured timeout (300 s by default, or the value set via `runtime_config.session_timeout_s`)
- **THEN** the adapter SHALL send SIGTERM to the subprocess
- **AND** SHALL wait up to a short grace period (default 5 s) for the process to exit
- **AND** if the process has not exited within the grace period, the adapter SHALL send SIGKILL
- **AND** the adapter SHALL raise `TimeoutError` only after the process is confirmed dead
- **AND** the subprocess SHALL NOT be reachable (i.e. not running) by the time the caller observes the `TimeoutError`

#### Scenario: Timeout escalation verified against observed 436 s incident
- **WHEN** the adapter is tested against a workload that ignores SIGTERM (e.g. a subprocess trapping SIGTERM and sleeping)
- **THEN** the test SHALL confirm the process is dead within `timeout + grace`
- **AND** the test SHALL reference session `46f18840-4f74-4e0a-a3bf-cafa2b579f3a` as the motivating incident

#### Scenario: Completed startup migration retry
- **WHEN** the first OpenCode process exits nonzero after reporting a completed first-run startup database migration
- **THEN** the adapter retries the same invocation once
- **AND** records retry provenance and the zero-based attempt index in process metadata

#### Scenario: Non-zero exit code without completed startup migration
- **WHEN** the OpenCode process exits with a non-zero return code
- **THEN** the adapter raises `RuntimeError` with the stderr/stdout error detail
