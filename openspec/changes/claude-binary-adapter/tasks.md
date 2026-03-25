## 1. Binary Discovery and Helpers

- [ ] 1.1 Implement `_find_claude_binary()` using `shutil.which("claude")` with `FileNotFoundError` and install hint
- [ ] 1.2 Implement `_parse_claude_output(stdout, stderr, returncode)` to parse `stream-json` JSON-line events — extract result text from `result` events, tool calls from `assistant` message content blocks with `tool_use` type, and token usage from `result.usage` (input_tokens, output_tokens, cache_read_input_tokens, cache_creation_input_tokens)

## 2. Core Adapter Rewrite

- [ ] 2.1 Remove all `claude_agent_sdk` imports (ClaudeAgentOptions, query, ResultMessage, ToolUseBlock, McpSSEServerConfig, McpHttpServerConfig)
- [ ] 2.2 Add subprocess imports (asyncio, shutil, tempfile, json) — follow Codex adapter import pattern
- [ ] 2.3 Rewrite `__init__()` — accept `claude_binary: str | None` parameter (like CodexAdapter), store `_last_process_info`, keep `butler_name` and `log_root` for stderr logging
- [ ] 2.4 Implement `last_process_info` property returning subprocess metadata dict (pid, exit_code, command, stderr, runtime_type)
- [ ] 2.5 Rewrite `invoke()` — build command array: `claude -p --output-format stream-json --bare --no-session-persistence --permission-mode bypassPermissions --strict-mcp-config --system-prompt <prompt> --mcp-config <path> [--model <model>] [runtime_args...] -- <prompt>`. Write temp MCP config, spawn subprocess, capture stdout/stderr, enforce timeout, parse output, return (result_text, tool_calls, usage)
- [ ] 2.6 Update `create_worker()` to return new instance with same claude_binary, butler_name, and log_root
- [ ] 2.7 Keep `build_config_file()` unchanged (already writes compatible `mcp.json`)
- [ ] 2.8 Keep `parse_system_prompt_file()` unchanged (already reads `CLAUDE.md`)

## 3. Dependency Cleanup

- [ ] 3.1 Remove `claude-agent-sdk` (or `claude_agent_sdk`) from `pyproject.toml` dependencies
- [ ] 3.2 Run `uv sync --dev` to verify lock file updates cleanly
- [ ] 3.3 Verify no other modules import from `claude_agent_sdk`

## 4. Tests

- [ ] 4.1 Write unit tests for `_find_claude_binary()` — found and not-found cases
- [ ] 4.2 Write unit tests for `_parse_claude_output()` — successful result with usage, tool calls in assistant messages, non-zero exit code, plain text fallback, empty output
- [ ] 4.3 Update `test_claude_code_integration.py` — mock `asyncio.create_subprocess_exec` instead of SDK query, verify command construction, environment passing, timeout handling, stderr capture
- [ ] 4.4 Verify adapter contract tests pass (`test_adapter_contract.py`) — registration, instantiation, interface compliance
- [ ] 4.5 Run full test suite to catch regressions

## 5. Spec Update

- [ ] 5.1 Sync the modified `core-spawner` delta spec to the main spec via `openspec sync`
