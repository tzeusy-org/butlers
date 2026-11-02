# Recorded Gemini CLI stream-json fixtures

Genuine `gemini -o stream-json` recordings from the pinned `@google/gemini-cli@0.66.0`
(`Dockerfile.base`). They back the recorded-stream tests in
`tests/adapters/test_gemini_adapter.py`.

| Fixture | Recorded exit code | What it shows |
| --- | --- | --- |
| `tool_run_success.jsonl` | 0 | user prompt echoed as a `message`, assistant text as `delta` chunks split around a `tool_use`/`tool_result` pair, `result` with `status: "success"` and `stats` |
| `turn_limit_error.jsonl` | 53 | same run with `model.maxSessionTurns: 1`: the tool runs, then `result` with `status: "error"` and `error.type: "FatalTurnLimitedError"` |

Provenance:

- Recorded 2026-11-02 with the real CLI process, but **without provider credentials**: an isolated
  `HOME` whose settings select `gemini-api-key` auth, a dummy `GEMINI_API_KEY`, and
  `GOOGLE_GEMINI_BASE_URL` pointing at a local mock of the Gemini API. The mock scripted the model
  turns (text, then a `list_directory` function call, then text in two chunks) and their
  `usageMetadata`, so token counts and response text are scripted, not provider output. Event
  shapes, field names, ordering, `stats` aggregation and exit codes are the CLI's own.
- Command: `gemini --sandbox=false --skip-trust -o stream-json -m gemini-3.8-flash --prompt
  "list files"`. For the turn-limit run `{"model": {"maxSessionTurns": 1}}` was set in the
  isolated user settings, not the system settings the adapter uses: 0.66.0 skips a system
  settings file unless it and every ancestor directory are root-owned and not group/other-writable
  (see `_settings_staging_root` in `src/butlers/core/runtimes/gemini.py`), which an unprivileged
  recording cannot provide. The turn limit behaves the same from either scope.
- Removed: `timestamp` and `session_id`. Every other field and value is unchanged.
- Re-verified on 0.66.0, same recipe: without `--skip-trust` a headless run exits 55 before any
  API call; with no auth configured it exits 41 (`FatalAuthenticationError`). The CLI's
  `Fatal*Error` classes and exit codes match 0.46.0, so `served_identity._GEMINI_ERROR_TYPES`
  needs no change.

## Model resolution probes (`model_resolution.json`)

Maps CLI version to `{requested -m value: model the CLI actually sent to the API}`.
`test_catalog_gemini_model_ids_were_probed_against_the_pinned_cli` requires every Gemini
catalog `model_id` to have a probe for the `Dockerfile.base` pin, and lets an entry be enabled
only when the CLI serves its id unchanged. The CLI's `stats.models` label is a usage report,
so a runtime rewrite never raises `requested_not_served`; this probe is the only check.

Provenance:

- Probed 2026-11-02 with the same credential-free setup as above (isolated `HOME`,
  `gemini-api-key` auth, dummy key, `GOOGLE_GEMINI_BASE_URL` at a local mock). Each run was
  `gemini --sandbox=false --skip-trust -o stream-json -m <id> --prompt hi`; the served value is
  the model in the mock's request path (`/v1beta/models/<id>:streamGenerateContent`), which
  always equalled the `stats.models` key. `init.model` always echoed the requested id.
- 0.46.0 cause (`resolveModel` in `packages/core/src/config/models.ts`): once Gemini 3.5 Flash
  GA is on, any requested id that ends in `flash` (except the preview flash model) is replaced
  by the default flash model, `gemini-3.5-flash`. GA is always on for `gemini-api-key`, Vertex
  and gateway auth; for OAuth (the deployed auth) it follows the server-side
  `GEMINI_3_5_FLASH_GA_LAUNCHED` experiment flag, and under OAuth with GA on the preview flash
  model is rewritten too. Ids not ending in `flash` (`gemini-4-argon`,
  `gemini-3.1-flash-lite`) pass through.
- 0.66.0 serves `gemini-3.8-flash`, `gemini-4-argon`, `gemini-2.5-*` and the preview flash model
  unchanged, but upgrades `gemini-3.5-flash` to `gemini-3.8-flash` and `gemini-3.1-flash-lite`
  to `gemini-3.5-flash-lite`. These probes used `gemini-api-key` auth; OAuth-gated rewrites
  were not probed.
