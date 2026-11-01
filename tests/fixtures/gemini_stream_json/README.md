# Recorded Gemini CLI stream-json fixtures

Genuine `gemini -o stream-json` recordings from the pinned `@google/gemini-cli@0.46.0`
(`Dockerfile.base`). They back the recorded-stream tests in
`tests/adapters/test_gemini_adapter.py`.

| Fixture | Recorded exit code | What it shows |
| --- | --- | --- |
| `tool_run_success.jsonl` | 0 | user prompt echoed as a `message`, assistant text as `delta` chunks split around a `tool_use`/`tool_result` pair, `result` with `status: "success"` and `stats` |
| `turn_limit_error.jsonl` | 53 | same run with `model.maxSessionTurns: 1` staged as system settings: the tool runs, then `result` with `status: "error"` and `error.type: "FatalTurnLimitedError"` |

Provenance:

- Recorded 2026-11-02 with the real CLI process, but **without provider credentials**: an isolated
  `HOME` whose settings select `gemini-api-key` auth, a dummy `GEMINI_API_KEY`, and
  `GOOGLE_GEMINI_BASE_URL` pointing at a local mock of the Gemini API. The mock scripted the model
  turns (text, then a `list_directory` function call, then text) and their `usageMetadata`, so token
  counts and response text are scripted, not provider output. Event shapes, field names, ordering,
  `stats` aggregation and exit codes are the CLI's own.
- Command: `gemini --sandbox=false --skip-trust -o stream-json -m gemini-2.5-flash --prompt
  "list files"` (plus `GEMINI_CLI_SYSTEM_SETTINGS_PATH` naming `{"model": {"maxSessionTurns": 1}}`
  for the turn-limit run). The CLI resolved the requested model to `gemini-3.5-flash` for the API
  call, which is why `init.model` and `stats.models` differ.
- Removed: `timestamp` and `session_id`. Every other field and value is unchanged.
