## MODIFIED Requirements

### Requirement: OpenCode CLI Invocation
The `OpenCodeAdapter` SHALL invoke the OpenCode CLI via `opencode run --format json` as an async subprocess. The adapter SHALL locate the `opencode` binary on PATH via `shutil.which()` and raise `FileNotFoundError` if not found. When the configured timeout fires, the adapter SHALL terminate the subprocess (`proc.kill()`, i.e. SIGKILL), await its exit, and raise `TimeoutError`. The configured timeout value is sourced from the spawner's per-spawn timeout resolution (default 300 s when no per-butler override is present).

> Note: the motivating incident (session `46f18840-4f74-4e0a-a3bf-cafa2b579f3a`, 2026-04-15) observed an opencode session running 436 s under a nominal 300 s budget. The as-built adapter issues an immediate SIGKILL on timeout (no SIGTERM grace period). A graduated SIGTERM→grace→SIGKILL escalation and a SIGTERM-trapping verification test are NOT implemented; if that hardening is desired it should be tracked as a separate behavioral change rather than asserted by this spec.

#### Scenario: Successful invocation
- **WHEN** the adapter invokes OpenCode with a valid prompt and config
- **THEN** it runs `opencode run --format json --model <model> <prompt>` as an async subprocess when the prompt is small enough for argv
- **AND** captures stdout/stderr and parses the JSON output

#### Scenario: Large prompt uses file attachment
- **WHEN** the prompt is too large to pass safely as a command-line argument
- **THEN** the adapter writes the prompt to a temporary Markdown file for the invocation
- **AND** the command uses `--file <prompt-file>` with a short instruction message instead of placing the full prompt in argv
- **AND** the temporary prompt file is cleaned up with the invocation directory

#### Scenario: Binary not found
- **WHEN** the `opencode` binary is not on PATH
- **THEN** the adapter raises `FileNotFoundError` with an install hint (`npm install -g opencode-ai`)

#### Scenario: Timeout exceeded
- **WHEN** the OpenCode process exceeds the configured timeout (default 300 s, or the value resolved per-spawn)
- **THEN** the adapter SHALL kill the subprocess (`proc.kill()`) and await its exit
- **AND** SHALL raise `TimeoutError` reporting the timeout duration
- **AND** SHALL record `exit_code = -1`, the timeout stderr marker, and the attempt index/count in `last_process_info`

#### Scenario: SQLite migration bootstrap retried once
- **WHEN** the first OpenCode process exits with a non-zero return code, stdout is empty, and stderr exactly matches the known one-time SQLite migration completion banner
- **THEN** the adapter retries the same invocation once
- **AND** records retry provenance, the retry reason, one-based attempt count, and zero-based attempt index in `last_process_info`
- **AND** if the retry succeeds, the adapter returns the retry result
- **AND** if stderr is partial, has extra lines, stdout is non-empty, or the retry fails, the adapter follows the normal error path

#### Scenario: Non-zero exit code without completed startup migration
- **WHEN** the OpenCode process exits with a non-zero return code
- **THEN** except for the one-time SQLite migration bootstrap retry case, the adapter raises `RuntimeError` with the stderr/stdout error detail
