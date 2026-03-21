# CLI Runtime Authentication

> **Purpose:** Document how LLM CLI runtimes (Claude, Codex, OpenCode) are authenticated via device-code flows managed from the dashboard.
> **Audience:** Operators deploying butlers, developers adding new CLI providers.
> **Prerequisites:** [Credential Store](../data_and_storage/credential-store.md).

## Overview

Butlers spawn ephemeral LLM CLI instances (Claude, Codex, Gemini, OpenCode) to reason and act. Each CLI requires authentication. Rather than managing token files manually, Butlers provides a dashboard-integrated device-code auth flow that persists tokens to the database, surviving container restarts and pod rescheduling without persistent volumes.

## Supported Providers

The provider registry at `src/butlers/cli_auth/registry.py` defines:

| Provider | Display Name | Auth Mode | Runtime | Binary |
|----------|-------------|-----------|---------|--------|
| `opencode-openai` | OpenCode (OpenAI) | device_code | opencode | `opencode` |
| `codex` | Codex (OpenAI) | device_code | codex | `codex` |
| `opencode-go` | OpenCode Go | api_key | opencode | `opencode` |

### Provider Definition

Each provider is a `CLIAuthProviderDef` dataclass specifying:

- **`command`** -- The CLI command to spawn for login (device_code mode).
- **`url_pattern`** / **`code_pattern`** -- Regex patterns to extract the device authorization URL and code from CLI stdout.
- **`success_pattern`** -- Regex to detect successful login in stdout.
- **`token_path`** -- Filesystem path where the CLI writes its credential file.
- **`status_command`** / **`status_ok_pattern`** -- Health probe command and success pattern.
- **`timeout_seconds`** -- Maximum time to wait for authorization (default: 900 seconds / 15 minutes).

## Device-Code Auth Flow

The auth flow is managed by `CLIAuthSession` in `src/butlers/cli_auth/session.py`:

1. **Dashboard initiates**: User clicks "Login" next to a provider in Settings.
2. **Session created**: A `CLIAuthSession` is created and stored in the process-local session store.
3. **Subprocess spawned**: The provider's login command is executed as an asyncio subprocess.
4. **Stdout parsing**: A reader task scans stdout line-by-line, stripping ANSI escape codes, and applies the provider's regex patterns.
5. **Device code extracted**: When the URL and code patterns match, the session transitions to `awaiting_auth` state and the dashboard displays the code to the user.
6. **User authorizes**: The user visits the auth URL and enters the device code.
7. **Success detection**: When the success pattern matches in stdout (or the process exits cleanly and a token file exists), the session transitions to `success`.
8. **Token persistence**: The `on_success` callback calls `persist_token()` to store the token in the DB.

### Session States

```
starting -> awaiting_auth -> success
                          -> failed
                          -> expired (timeout)
```

### Session Management

- Maximum 20 concurrent/retained sessions to prevent resource leaks.
- Terminal sessions (success/failed/expired) are evicted oldest-first when the cap is reached.
- Subprocess is terminated (SIGTERM, then SIGKILL after 5s) on timeout or cancellation.

## API Key Mode

The `opencode-go` provider uses `api_key` auth mode instead of device-code:

- The API key is entered through the dashboard and stored in the credential store.
- The key is injected as an environment variable (`OPENCODE_GO_API_KEY`) at runtime.
- Validation runs a test command against the provider to verify the key works.

## Health Probes

The health module at `src/butlers/cli_auth/health.py` probes each provider's authentication status:

### Health States

| State | Description |
|-------|-------------|
| `authenticated` | Credentials are valid and usable |
| `not_authenticated` | No credentials found or credentials are invalid/expired |
| `unavailable` | CLI binary not installed or not on PATH |
| `probe_failed` | Status command failed to execute or timed out (15s) |

### Probe Logic

For `device_code` providers:
1. Check if the CLI binary is available on PATH.
2. Run the provider's `status_command` (e.g., `codex login status`).
3. Match output against `status_ok_pattern` (e.g., `"Logged in"`).
4. If no status command is defined, fall back to checking if the token file exists.

For `api_key` providers:
1. Check if the token path exists and contains a valid API key entry.

`probe_all()` runs all probes concurrently and returns a dict of provider name to `AuthHealthResult`.

## Token Persistence

### Storing

After successful auth, `persist_token()` in `src/butlers/cli_auth/persistence.py`:
1. Reads the CLI's token file from disk.
2. Stores the content in `butler_secrets` with key `cli-auth/<provider_name>` and category `cli-auth`.

### Restoring

On application startup, `restore_tokens()`:
1. Iterates over all registered providers.
2. Loads each token from DB via `store.load("cli-auth/<name>")`.
3. Writes the content to the expected filesystem path.
4. Sets file permissions to `0o600`.
5. Handles shared token paths by merging JSON content (e.g., two providers sharing `auth.json`).

This restore happens during the dashboard API lifespan startup, ensuring tokens are available before any butler attempts to spawn a CLI instance.

## Related Pages

- [Credential Store](../data_and_storage/credential-store.md) -- Where tokens are persisted
- [Environment Variables](environment-variables.md) -- Runtime configuration
