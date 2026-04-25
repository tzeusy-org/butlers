## 1. Shared Test Harness

- [ ] 1.1 Add `run_cli(binary, prompt, timeout=120)` helper to `tests/adapters/conftest.py` — returns `(stdout, stderr, returncode)` via `subprocess.run()` with `cwd=/tmp`
- [ ] 1.2 Add `parse_jsonl_events(stdout)` helper to `tests/adapters/conftest.py` — parses JSON-lines, skips non-JSON, returns list of dicts
- [ ] 1.3 Refactor `test_opencode_integration.py` to use the shared `run_cli` and `parse_jsonl_events` helpers instead of inline implementations

## 2. Codex Integration Tests

- [ ] 2.1 Create `tests/adapters/test_codex_integration.py` with `@pytest.mark.nightly` + `skipif(not shutil.which("codex"))`
- [ ] 2.2 Add raw output format tests: verify `codex exec --json` event shapes (item.completed, turn.completed, function_call structures)
- [ ] 2.3 Add parser integration tests: feed real `codex exec` output through `_parse_codex_output()`, assert non-empty text, tool name/id/input, positive tokens
- [ ] 2.4 Add full invoke test: `CodexAdapter.invoke()` end-to-end, verify result_text, tool_calls, usage, and last_process_info

## 3. Gemini Integration Tests

- [ ] 3.1 Create `tests/adapters/test_gemini_integration.py` with `@pytest.mark.nightly` + `skipif(not shutil.which("gemini"))`
- [ ] 3.2 Add raw output format tests: verify Gemini CLI event shapes and structural keys
- [ ] 3.3 Add parser integration tests: feed real Gemini output through `_parse_gemini_output()`, assert non-empty text, tool calls with name/id/input
- [ ] 3.4 Add explicit `usage is None` test documenting Gemini's known token tracking limitation
- [ ] 3.5 Add full invoke test: `GeminiAdapter.invoke()` end-to-end, verify result_text, tool_calls, and last_process_info

## 4. Claude Code Integration Tests

- [ ] 4.1 Create `tests/adapters/test_claude_code_integration.py` with `@pytest.mark.nightly` + `skipif` for ANTHROPIC_API_KEY env var
- [ ] 4.2 Add SDK invoke test: `ClaudeCodeAdapter.invoke()` end-to-end, verify non-empty result_text and positive token usage
- [ ] 4.3 Add tool call extraction test: invoke with tool-triggering prompt, verify ToolUseBlock parsing produces non-empty name/id/input
- [ ] 4.4 Skip raw output format tier (SDK provides structured objects, not JSON-lines)

## 5. Adapter Contract Extension

- [ ] 5.1 Add parametrized nightly-tier tests to `test_adapter_contract.py` covering structural text/tool/usage assertions across all subprocess adapters that have binaries available

## 6. Validation

- [ ] 6.1 Run all nightly integration tests locally: `uv run pytest tests/adapters/ -m nightly -v`
- [ ] 6.2 Verify all integration tests are deselected in default CI mode: `uv run pytest tests/adapters/ -m 'not nightly' --collect-only`
- [ ] 6.3 Fix any parser mismatches discovered during integration testing (as happened with OpenCode)
