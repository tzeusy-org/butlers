## Why

The ClaudeCodeAdapter is the only runtime adapter that uses an in-process Python SDK (`claude_agent_sdk`) instead of wrapping a CLI binary via subprocess. This creates an asymmetry: Codex, Gemini, and OpenCode all follow the subprocess pattern (binary discovery, `asyncio.create_subprocess_exec`, stdout/stderr capture, process info tracking), while Claude Code takes a fundamentally different code path through the SDK's async generator. This asymmetry means Claude Code sessions lack subprocess-level diagnostics (`last_process_info` always returns `None`), the `claude_agent_sdk` dependency must be installed and version-pinned in the butler's Python environment, and any SDK behavioral changes require code-level fixes rather than just binary updates. Rewriting the adapter to wrap the `claude` CLI binary — the same way Codex is wrapped — eliminates this divergence and brings all four adapters onto one consistent pattern.

## What Changes

- **BREAKING**: Replace `claude_agent_sdk`-based invocation with subprocess invocation of the `claude` CLI binary
- Remove Python dependency on `claude_agent_sdk` (ClaudeAgentOptions, query, ResultMessage, ToolUseBlock, McpSSEServerConfig, McpHttpServerConfig)
- Invoke `claude` in non-interactive print mode (`claude -p`) with `--output-format json` for structured output
- Pass system prompt via `--system-prompt` flag (native CLI support, unlike Codex which must embed in prompt)
- Pass MCP config via `--mcp-config <path>` pointing to the existing `mcp.json` file (reuse `build_config_file()`)
- Pass model via `--model` flag, max turns via `--max-turns`, permission mode via `--permission-mode bypassPermissions`
- Parse JSON-lines stdout output for result text, tool calls, and token usage
- Implement `last_process_info` with full subprocess metadata (pid, exit_code, command, stderr) — previously always returned `None`
- Retain `binary_name = "claude"`, `build_config_file()` (already writes `mcp.json`), and `parse_system_prompt_file()` (reads `CLAUDE.md`) unchanged
- Update adapter contract tests and integration tests

## Capabilities

### New Capabilities

_(none — this is a rewrite of an existing adapter, not a new capability)_

### Modified Capabilities

- `core-spawner`: The "Claude Code adapter invocation" scenario changes from SDK-based async generator to subprocess-based invocation. The adapter now produces `last_process_info` and follows the same subprocess lifecycle as Codex/Gemini/OpenCode. The spawner's process log write path (`session_process_logs`) will now fire for Claude Code sessions (previously skipped due to `None` process info).

## Impact

- **Code**: `src/butlers/core/runtimes/claude_code.py` — full rewrite of `invoke()`, add `_find_claude_binary()`, `_parse_claude_output()`, process info tracking
- **Dependencies**: `claude_agent_sdk` removed from `pyproject.toml` / `uv.lock`
- **Tests**: `tests/adapters/test_claude_code_integration.py` needs rewrite (mocking subprocess instead of SDK), adapter contract tests remain unchanged
- **Spawner**: No changes — the adapter contract is unchanged. Process log writes will now activate for Claude Code sessions (net-positive)
- **Config**: No changes to `butler.toml` or runtime selection — `"claude"` type string and adapter registration are preserved
- **Binary requirement**: The `claude` CLI binary must be installed on PATH (same requirement as codex/gemini/opencode binaries for their adapters)
