## Context

The Butlers project has four LLM runtime adapters (Claude Code, Codex, OpenCode, Gemini), each parsing CLI-specific JSON output into a common `(result_text, tool_calls, usage)` tuple. Unit tests mock subprocess output with assumed event shapes, but the OpenCode adapter's parser was entirely broken against real CLI output (v1.2+ envelope format) — text, tool calls, and tokens all returned empty. This was only caught by manually running the binary and comparing output against parser expectations.

OpenCode now has a nightly integration test suite (`test_opencode_integration.py`) that runs the real binary. The other three adapters lack equivalent coverage.

Existing test infrastructure:
- `test_adapter_contract.py` — parametrized unit tests covering shared behavior (Codex + Gemini only)
- Per-adapter unit test files with mocked subprocess output
- `@pytest.mark.nightly` marker excluded from CI via `addopts = "-m 'not nightly'"`
- `@pytest.mark.skipif` pattern for binary availability

## Goals / Non-Goals

**Goals:**
- Define a standard integration test contract that every adapter must implement
- Create nightly integration test suites for Codex, Gemini, and Claude Code adapters
- Build shared test fixtures to reduce boilerplate (binary detection, output parsing helpers)
- Catch CLI output format changes before they silently break production sessions
- Cover the critical data extraction paths: text, tool calls (name/id/input), token usage, and process metadata

**Non-Goals:**
- Replacing unit tests — mocked tests remain the primary fast feedback loop
- Testing the LLM's behavioral correctness (e.g., "does it answer math correctly")
- Achieving deterministic test output — LLM responses are inherently variable; tests assert structural properties only
- Running integration tests in CI — they remain `nightly` (requires API keys + CLI binaries)
- Testing MCP tool execution through adapters — that's E2E scope

## Decisions

### 1. Three-tier test structure per adapter

Each adapter gets integration tests organized in three tiers:

| Tier | What it tests | Example |
|------|--------------|---------|
| **Raw output format** | CLI produces expected event shapes | "tool_use events have part.tool and part.callID" |
| **Parser integration** | `_parse_*_output()` extracts correct data from real CLI output | "parser returns non-empty tool name from real output" |
| **Full adapter invoke** | `adapter.invoke()` end-to-end with real binary | "invoke() returns text + usage + process info" |

**Rationale**: Tier 1 catches format changes early (before they reach the parser). Tier 2 tests the parser against real data. Tier 3 validates the full stack including config generation and env handling.

### 2. Shared conftest fixtures in `tests/adapters/conftest.py`

Add shared fixtures:
- `run_cli(binary, args, prompt)` — subprocess helper returning `(stdout, stderr, returncode)`
- `parse_jsonl_events(stdout)` — parse JSON-lines into event dicts
- `skip_if_no_binary(name)` — reusable `pytest.mark.skipif` for binary availability
- `require_api_key(var_name)` — skip if API key env var not set

**Rationale**: The OpenCode integration tests already implement these patterns inline. Extracting to fixtures eliminates ~30 lines of boilerplate per adapter test file.

### 3. Structural assertions only — no behavioral assertions

Integration tests SHALL assert on:
- Event structure (keys present, types correct)
- Non-empty/non-None extracted values (text, tool name, token counts)
- Positive token counts (> 0)
- Process metadata populated (pid, exit_code, runtime_type)

Tests SHALL NOT assert on:
- Specific text content ("the answer should be 'six'")
- Exact token counts
- Tool call ordering or count (LLM may vary)

**Rationale**: LLM behavior is non-deterministic. The OpenCode multi-step test initially failed because the LLM chose not to use tools. Tests must be resilient to behavioral variance while catching structural format breaks.

### 4. Claude Code uses SDK, not subprocess — different test pattern

ClaudeCodeAdapter uses `claude_agent_sdk.query()` (Python SDK), not subprocess. Its integration tests:
- Skip the "raw output format" tier (no CLI JSON to validate)
- Test `invoke()` directly, asserting `ResultMessage` parsing works
- Verify `ToolUseBlock` extraction from real SDK responses
- Require `ANTHROPIC_API_KEY` env var

**Rationale**: The SDK provides structured Python objects, not JSON-lines. Output format stability is the SDK maintainer's concern, not ours. Our tests verify our extraction logic.

### 5. Gemini adapter — token usage is expected None

GeminiAdapter currently returns `usage = None` always (the Gemini CLI doesn't emit token counts). Integration tests SHALL:
- Assert `usage is None` explicitly (documenting the known limitation)
- Test text and tool call extraction normally

**Rationale**: Documenting the gap as an explicit test expectation prevents future developers from thinking the None is a bug.

## Risks / Trade-offs

- **[Flaky tests from LLM non-determinism]** → Structural-only assertions; prompts designed to strongly encourage specific tool use ("Use the shell to run: echo 'marker'")
- **[API cost from nightly runs]** → Simple prompts with short responses; ~$0.01–0.05 per adapter per run
- **[Binary version drift]** → Tests validate structural format, not exact versions; format breaks surface as test failures which is the intended detection mechanism
- **[Claude Code SDK version coupling]** → SDK tests use `claude_agent_sdk.query()` directly; version bumps may change response shapes, but that's exactly what we want to catch
