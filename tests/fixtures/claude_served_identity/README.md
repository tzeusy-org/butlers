# Recorded Claude Code served-identity fixtures

Genuine `claude -p` stream-json recordings, reduced to serving metadata by `sanitize.py`
(sanitizer version 1). They back `tests/core/runtimes/test_claude_served_identity.py`.

| Fixture | Recorded exit code | What it shows |
| --- | --- | --- |
| `two_model_success.jsonl` | 0 | `modelUsage` with two models (Sonnet main loop, Haiku subagent) |
| `error_max_turns.jsonl` | 1 | `is_error: true`, `subtype: error_max_turns` |
| `no_model_usage.jsonl` | 1 | unknown model: `is_error: true`, empty `modelUsage`, zero usage |

Provenance:

- Recorded 2026-11-01 with Claude Code CLI 2.1.316 (`claude --version`), first-party auth.
- Command shape (prompts abbreviated):
  - two models: `claude -p "<use the Agent tool once with model haiku>" --output-format stream-json
    --verbose --strict-mcp-config --model sonnet --max-turns 4 --allowedTools=Agent`
  - max turns: `claude -p "<run the shell command true, then reply>" --output-format stream-json
    --verbose --strict-mcp-config --model haiku --max-turns 1 --allowedTools=Bash`
  - no model usage: `claude -p ok --output-format stream-json --verbose --strict-mcp-config
    --model not-a-real-model-xyz --max-turns 1`
- Kept: `system/init` (`model`, `claude_code_version`) and `result` (`subtype`, `is_error`,
  `num_turns`, `total_cost_usd`, the four token buckets of `usage` and of each `modelUsage`
  entry, `costUSD`, the permission-denial count).
- Removed: every other event and field, including session ids, uuids, cwd, tool/MCP/skill
  inventories, hook output, rate-limit data and all prompt/response text. The response is
  replaced by a placeholder.
- Every kept value was compared against the raw recording before the raw files were discarded;
  no raw transcript is retained.

CLI 2.1.316 exits 1 on `error_max_turns`; earlier CLIs exited 0 with the same terminal result.
The test replays the recorded exit code and, separately, the exit-0 shape the adapter must also
classify as a failed terminal result.
