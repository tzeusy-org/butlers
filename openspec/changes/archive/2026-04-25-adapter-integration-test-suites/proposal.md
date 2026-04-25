## Why

Every LLM runtime adapter (Claude Code, Codex, OpenCode, Gemini) parses CLI output differently, but we discovered that the OpenCode adapter's parser was completely broken against real v1.2+ output — text, tool calls, and token usage all returned empty/None. The root cause: the adapter was written against a speculative output format that didn't match reality, and there were no integration tests to catch it. Only OpenCode now has nightly integration tests; the other three adapters have the same gap. When CLI tools update their output format, we need automated detection.

## What Changes

- Define a standard integration test contract that every adapter must implement: text extraction, tool call parsing (name, id, input), token/usage tracking, and skill/command execution verification
- Require each adapter to have a nightly integration test suite that runs the real CLI binary and validates the parser against actual output
- Add a shared test harness (fixtures, helpers) to reduce boilerplate across adapter integration tests
- Add integration tests for Codex, Gemini, and Claude Code adapters (OpenCode already has them)
- Extend `test_adapter_contract.py` with parametrized integration-tier tests

## Capabilities

### New Capabilities
- `adapter-integration-testing`: Standard integration test contract, shared harness, and per-adapter nightly test suites that verify parser correctness against real CLI output

### Modified Capabilities
- `testing`: Add adapter integration testing requirements to the testing spec (new nightly test category, binary availability skipif pattern, shared fixtures)

## Impact

- **Test files**: New `tests/adapters/test_codex_integration.py`, `tests/adapters/test_gemini_integration.py`, `tests/adapters/test_claude_code_integration.py`; updates to `tests/adapters/conftest.py` for shared fixtures
- **Existing tests**: `test_adapter_contract.py` extended with integration-tier parametrized tests
- **CI/CD**: No impact — all integration tests are `@pytest.mark.nightly` (excluded from default CI via addopts)
- **Dependencies**: Requires each CLI binary on PATH and valid API credentials for nightly runs
- **Adapters**: Codex, Gemini, and Claude Code adapters may need parser fixes if integration tests reveal mismatches (as happened with OpenCode)
