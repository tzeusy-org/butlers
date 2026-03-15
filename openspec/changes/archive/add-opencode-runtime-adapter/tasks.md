## 1. Core Adapter Implementation

- [ ] 1.1 Create `src/butlers/core/runtimes/opencode.py` with `OpenCodeAdapter` class implementing `RuntimeAdapter`
- [ ] 1.2 Implement `binary_name` property returning `"opencode"` and `_find_opencode_binary()` helper via `shutil.which()`
- [ ] 1.3 Implement `invoke()`: build command (`opencode run --format json --model <model>`), write temp JSONC config, launch subprocess, parse output
- [ ] 1.4 Implement temp config generation: write `opencode.jsonc` with `mcp` (remote servers), `instructions` (system prompt file path), `permission` (empty object)
- [ ] 1.5 Implement `_parse_opencode_output()`: extract result text, tool calls, and usage from JSON event stream with plain-text fallback
- [ ] 1.6 Implement `parse_system_prompt_file()`: read `OPENCODE.md` with fallback to `AGENTS.md`
- [ ] 1.7 Implement `build_config_file()`: write `opencode.jsonc` to tmp_dir with MCP server configs
- [ ] 1.8 Implement `create_worker()` returning a new `OpenCodeAdapter` instance
- [ ] 1.9 Call `register_adapter("opencode", OpenCodeAdapter)` at module level

## 2. Integration Wiring

- [ ] 2.1 Add `opencode` import to `src/butlers/core/runtimes/__init__.py`
- [ ] 2.2 Update `src/butlers/daemon.py` adapter instantiation to handle `"opencode"` type (generic path, like codex/gemini)

## 3. Tests

- [ ] 3.1 Create `tests/adapters/test_opencode_adapter.py` with unit tests for `_parse_opencode_output()` (text, tool calls, usage, fallback)
- [ ] 3.2 Add tests for `_find_opencode_binary()` (found and not-found paths)
- [ ] 3.3 Add tests for `invoke()` with mocked subprocess (successful invocation, timeout, non-zero exit)
- [ ] 3.4 Add tests for temp config generation (JSONC structure, MCP server mapping, instructions reference, cleanup)
- [ ] 3.5 Add tests for `parse_system_prompt_file()` (OPENCODE.md present, fallback to AGENTS.md, neither present)
- [ ] 3.6 Add tests for `build_config_file()` and `create_worker()`
- [ ] 3.7 Update `tests/adapters/test_runtime_adapter.py` registry tests to include `"opencode"` lookup
- [ ] 3.8 Run full lint + test suite to validate no regressions
