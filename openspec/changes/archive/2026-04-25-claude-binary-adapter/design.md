## Context

The ClaudeCodeAdapter currently uses the `claude_agent_sdk` Python library to invoke Claude Code sessions in-process. The three other adapters (Codex, Gemini, OpenCode) all follow a subprocess pattern: discover binary on PATH, spawn via `asyncio.create_subprocess_exec`, capture stdout/stderr, parse structured output. This creates a split in the codebase where Claude Code sessions lack process-level diagnostics and require a Python SDK dependency that the other adapters don't.

The `claude` CLI binary (Claude Code) supports rich non-interactive invocation via `-p` (print mode) with `--output-format stream-json` for structured JSON-line event output. It also supports native flags for system prompts, MCP configuration, model selection, permission modes, and budget limits — making it well-suited for subprocess wrapping.

### Current adapter feature surface (SDK-based)

| Feature | SDK mechanism |
|---------|--------------|
| System prompt | `ClaudeAgentOptions(system_prompt=...)` |
| MCP servers | `McpSSEServerConfig` / `McpHttpServerConfig` objects in options |
| Model | `ClaudeAgentOptions(model=...)` |
| Max turns | `ClaudeAgentOptions(max_turns=...)` |
| Permission mode | `ClaudeAgentOptions(permission_mode="bypassPermissions")` |
| Environment | `ClaudeAgentOptions(env=...)` |
| Working directory | `ClaudeAgentOptions(cwd=...)` |
| Token usage | `ResultMessage.usage` dict |
| Tool calls | `ToolUseBlock` objects from stream |
| Result text | `ResultMessage.result` string |
| Stderr capture | Manual file descriptor wiring via `debug_stderr` kwarg |
| Process info | Not available (returns `None`) |

## Goals / Non-Goals

**Goals:**

- Replace SDK invocation with subprocess invocation of the `claude` CLI binary
- Achieve full feature parity with the current SDK-based adapter (system prompt, MCP, model, tokens, tool calls, result text, environment, cwd, timeout)
- Gain subprocess-level diagnostics (`last_process_info` with pid, exit_code, command, stderr)
- Enable process log writes to `session_process_logs` (previously skipped for Claude Code)
- Remove the `claude_agent_sdk` Python dependency
- Follow the same structural patterns as CodexAdapter for maintainability
- Map Claude CLI features that improve butler sessions: `--bare` (skip hooks/LSP), `--no-session-persistence` (ephemeral), `--strict-mcp-config` (isolation), `--effort` (complexity mapping)

**Non-Goals:**

- Changing the `RuntimeAdapter` ABC or adapter registry interface
- Changing the spawner's invocation contract or session lifecycle
- Supporting interactive Claude Code sessions (only print mode)
- Supporting `--input-format stream-json` (streaming input) — butler prompts are single-shot
- Adding `--max-turns` equivalent — the Claude CLI has no such flag; use `--max-budget-usd` or timeout as safety valves instead

## Decisions

### D1: Use `stream-json` output format (not `json`)

**Choice**: `--output-format stream-json`

**Rationale**: The `json` output format returns only the final result object. The `stream-json` format emits JSON-line events including tool use blocks, intermediate messages, and a final result event with usage stats. Since the spawner logs tool calls in `session_complete()`, we need the streaming format to capture tool call records. This mirrors how Codex parses JSON-lines from `codex exec --json`.

**Alternative considered**: `--output-format json` — simpler to parse (single JSON object) but loses tool call visibility. Rejected because tool_calls are part of the adapter return contract and are used for session diagnostics.

### D2: Use `--bare` mode for minimal overhead

**Choice**: Always pass `--bare` flag.

**Rationale**: Butler invocations are ephemeral — they don't need hooks, LSP, plugin sync, auto-memory, background prefetches, or CLAUDE.md auto-discovery. The `--bare` flag skips all of these, reducing startup time and eliminating side effects. System prompts are passed explicitly via `--system-prompt`, and MCP config via `--mcp-config` + `--strict-mcp-config`.

**Alternative considered**: Omitting `--bare` — would pick up host machine's Claude Code configuration, hooks, and installed plugins. Rejected because butler sessions must be deterministic and isolated.

### D3: Use `--strict-mcp-config` for MCP isolation

**Choice**: Always pass `--strict-mcp-config` alongside `--mcp-config <path>`.

**Rationale**: Without `--strict-mcp-config`, the `claude` CLI merges servers from `--mcp-config` with any MCP servers configured in the user's Claude Code settings. This would leak the host's MCP servers into butler sessions, violating the isolation requirement. The strict flag ensures only the butler's declared MCP server is used.

### D4: Pass system prompt via `--system-prompt` flag (not file)

**Choice**: Pass the system prompt string directly via `--system-prompt <prompt>`.

**Rationale**: The `claude` CLI supports both `--system-prompt <string>` and `--system-prompt-file <path>`. Direct string passing avoids creating and cleaning up a temp file. The system prompt is already in-memory from `read_system_prompt()`. For very large prompts, the OS argument length limit (typically 2MB+ on Linux) is not a concern given butler system prompts are well under 100KB.

**Alternative considered**: `--system-prompt-file <path>` — more robust for edge cases with shell-special characters, but adds temp file management. Can be adopted later if argument-length issues arise.

### D5: Map max_turns to `--max-budget-usd` as safety valve

**Choice**: No direct max_turns mapping. Use timeout as the primary safety valve.

**Rationale**: The `claude` CLI does not support a `--max-turns` flag. The SDK exposed `max_turns` in `ClaudeAgentOptions`, but the CLI equivalent doesn't exist. The spawner's `timeout` parameter (via `asyncio.wait_for`) is the primary safety mechanism. Optionally, `--max-budget-usd` could be used as a cost cap, but this requires dollar-denominated configuration rather than turn counts.

**Impact**: The `max_turns` parameter in `invoke()` will be accepted but not enforced by the CLI. This is consistent with how GeminiAdapter handles it (also ignores `max_turns`). Document this in the adapter docstring.

### D6: Map complexity to `--effort` level

**Choice**: When the spawner resolves complexity and passes runtime_args, the adapter can accept an `--effort` flag from `runtime_args`.

**Rationale**: The `claude` CLI supports `--effort low|medium|high|max` which controls reasoning effort. The spawner's complexity parameter (low/medium/high) maps naturally to this. Rather than adding a dedicated parameter, we let the spawner pass `--effort <level>` via `runtime_args` (or the catalog's `extra_args`), keeping the adapter interface unchanged.

### D7: Use `--no-session-persistence` for ephemeral sessions

**Choice**: Always pass `--no-session-persistence`.

**Rationale**: Butler sessions are ephemeral — they're tracked by the butler's own session system, not Claude Code's internal session store. Saving to Claude Code's session store wastes disk I/O and creates orphan session data.

### D8: Parse `stream-json` events for result text, tool calls, and usage

**Choice**: Parse JSON-lines from stdout with a dedicated `_parse_claude_output()` function.

**Rationale**: The `stream-json` output emits events of types including:
- `assistant` — contains content blocks (text and tool_use)
- `result` — final result with `result` text, `usage` dict, `cost_usd`, `session_id`
- System events (init, progress) — can be skipped

The parser follows the same pattern as `_parse_codex_output()`: iterate lines, parse JSON, switch on `type` field, accumulate text/tools/usage. The `result` event's `usage` object contains `input_tokens`, `output_tokens`, `cache_read_input_tokens`, and `cache_creation_input_tokens`.

### D9: Reuse existing `build_config_file()` for MCP config

**Choice**: Keep the existing `build_config_file()` that writes `mcp.json` with `{"mcpServers": {...}}`. Pass the path via `--mcp-config <path>`.

**Rationale**: The `claude` CLI's `--mcp-config` flag accepts JSON files in the same `{"mcpServers": {...}}` format that `build_config_file()` already produces. No changes needed to config generation — only the invocation method changes.

### D10: Stderr capture via subprocess pipe (not `--debug-file`)

**Choice**: Capture stderr via `asyncio.subprocess.PIPE`, write to per-butler log file manually.

**Rationale**: The existing adapter already had per-butler stderr log files at `{log_root}/butlers/{butler_name}_cc_stderr.log`. With subprocess invocation, stderr is captured naturally via the PIPE. The `--debug-file` flag would duplicate this and create Claude Code's own debug log format. Keeping stderr capture consistent with other adapters (Codex captures stderr the same way) is simpler.

## Risks / Trade-offs

**[Binary availability]** → The `claude` CLI must be installed on PATH. Mitigation: Same requirement as codex/gemini/opencode. `_find_claude_binary()` raises `FileNotFoundError` with an install hint.

**[Argument length limit]** → Very large system prompts passed via `--system-prompt` could hit OS argument length limits. Mitigation: Butler system prompts are typically <50KB. Can switch to `--system-prompt-file` if needed (D4 alternative).

**[No max_turns enforcement]** → The CLI doesn't support turn limits. Mitigation: Timeout is the primary safety valve. Budget caps via `--max-budget-usd` could be added as a catalog-level config. In practice, the SDK's `max_turns` was set to 20 and most butler sessions complete in 1-3 turns.

**[stream-json output format stability]** → Claude CLI's `stream-json` event schema may change across versions. Mitigation: Parser uses defensive field extraction (`.get()` with defaults), same resilience pattern as Codex parser. Unknown event types are logged and skipped.

**[Temp file for MCP config]** → Each invocation writes a temp `mcp.json` file. Mitigation: Already happening via `build_config_file()`. Use `tempfile.TemporaryDirectory` for automatic cleanup (same pattern as OpenCodeAdapter).

**[SDK removal]** → Removing `claude_agent_sdk` is a breaking change for any code that imported it transitively. Mitigation: Only `claude_code.py` imports the SDK. No other module uses it. The adapter registry interface is unchanged.

## Migration Plan

1. Implement new subprocess-based `invoke()` and output parser in `claude_code.py`
2. Remove `claude_agent_sdk` imports and SDK-specific code
3. Remove `claude_agent_sdk` from `pyproject.toml` dependencies
4. Update adapter tests to mock subprocess instead of SDK
5. Run full adapter contract test suite to verify interface compliance
6. Run spawner integration tests to verify session lifecycle works with new adapter

**Rollback**: Revert the commit. The `claude_agent_sdk` dependency can be re-added to `pyproject.toml` and the old `invoke()` restored from git history.

### D11: Authenticate via `ANTHROPIC_API_KEY` from CLI Runtime Authentication settings

**Choice**: Register a Claude provider in the CLI auth registry using `api_key` mode. The dashboard Settings → CLI Runtime Authentication card collects an Anthropic API key from the user, stores it in the credential store (`butler_secrets` with key `cli-auth/claude`), and the spawner injects it as `ANTHROPIC_API_KEY` in the subprocess environment.

**Rationale**: The `claude` CLI binary respects the `ANTHROPIC_API_KEY` environment variable, which takes precedence over its own credential file (`~/.claude/.credentials.json`). This follows the same pattern as the `opencode-go` provider (API key mode), keeping the authentication UX consistent across all runtimes in the dashboard. No special "external-auth" or probe-only mode is needed.

**Implementation**: A new `CLIAuthProviderDef` is registered in `cli_auth/registry.py` with `auth_mode="api_key"`, `env_var="ANTHROPIC_API_KEY"`, and `runtime="claude"`. The spawner's credential isolation logic already resolves declared env vars via `CredentialStore.resolve()`, so `ANTHROPIC_API_KEY` is picked up from the credential store and injected into the subprocess environment alongside other declared vars.

**Health probe**: The provider can define a `test_command` that runs `claude -p --output-format json "respond with ok"` to validate the key, or rely on format-based validation (Anthropic keys start with `sk-ant-`).

## Open Questions

1. **Should `--append-system-prompt` be used instead of `--system-prompt`?** The `--system-prompt` flag replaces the default system prompt entirely, while `--append-system-prompt` appends to it. Since `--bare` mode has no default system prompt, `--system-prompt` is correct. But if we ever remove `--bare`, we'd want `--append-system-prompt` to preserve Claude Code's built-in system prompt.

2. **Should we use `--fallback-model` for resilience?** The CLI supports `--fallback-model <model>` for automatic failover on overload. This could be useful for production butler sessions but adds configuration surface. Defer to catalog-level `extra_args`.
