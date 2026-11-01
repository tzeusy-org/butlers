# Recorded Claude Code stream-json fixtures

Genuine `claude -p` stream-json recordings, reduced to the events and fields the adapter's
output parser reads. They back the recorded-stream tests in
`tests/adapters/test_claude_code_adapter.py`.

| Fixture | Recorded exit code | What it shows |
| --- | --- | --- |
| `tool_use_success.jsonl` | 0 | native shape: assistant content nested under `message`, one `tool_use` split into its own event, final text repeated as the result |
| `subagent_tool_use.jsonl` | 0 | an `Agent` call whose subagent runs one `Bash` call: subagent events carry the `Agent` call's id as `parent_tool_use_id`; the result's `num_turns` counts only the parent's turns |

Provenance:

- Recorded 2026-11-02 with Claude Code CLI 2.1.316, first-party auth, in an empty directory:
  `claude -p "Run the shell command true exactly once with the Bash tool, then reply with the
  single word done." --output-format stream-json --verbose --strict-mcp-config --model haiku
  --max-turns 3 --allowedTools=Bash`
- `subagent_tool_use.jsonl` recorded the same way on 2026-11-02 with
  `claude -p "Use the Agent tool exactly once to launch a general-purpose subagent whose task is:
  run the shell command true exactly once with the Bash tool, then reply done. Do not call Bash
  yourself. After the subagent returns, reply with the single word done." --output-format
  stream-json --verbose --strict-mcp-config --model haiku --max-turns 4
  --allowedTools=Bash,Agent`. Its `Agent` `tool_result` content (a block list carrying a
  subagent id) was dropped; string `tool_result` content is kept.
- Kept: `system/init` (`model`, `claude_code_version`); `assistant` and `user` events
  (`type`, `parent_tool_use_id`, `message.role`, and per content block `type` plus `id`/`name`/
  `input` for `tool_use`, `text` for `text`, `tool_use_id`/`content`/`is_error` for
  `tool_result`); `result` (`subtype`, `is_error`, `num_turns`, `result`, the four `usage` token
  buckets).
- Removed: hook, thinking-token and rate-limit events; session ids, uuids, request ids,
  timestamps, cwd, tool/MCP inventories, thinking text and signatures, and every other field.
  Values that were kept are unchanged from the recording.
