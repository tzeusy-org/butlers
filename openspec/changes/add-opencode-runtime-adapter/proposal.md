## Why

The switchboard spec and Dockerfile already reference OpenCode as a supported runtime family, but no adapter exists. All production butlers currently run on the Codex adapter. Adding an OpenCode adapter completes the spec-mandated runtime support, gives butlers access to any provider OpenCode supports (Anthropic, OpenAI, Google, etc. via a single CLI), and provides a Go-based runtime alternative with built-in structured output and server/SDK modes.

## What Changes

- Add `OpenCodeAdapter` implementing `RuntimeAdapter` for the OpenCode CLI (`opencode run --format json`)
- Add OpenCode-specific MCP config generation (JSONC format with `local`/`remote` server types)
- Add OpenCode output parser for JSON event stream (text, tool calls, usage extraction)
- Add `OPENCODE.md` system prompt file convention (fallback to `AGENTS.md`)
- Register `"opencode"` in the adapter registry so butlers can use `[runtime] type = "opencode"` in `butler.toml`
- Add daemon instantiation path for the opencode adapter (alongside existing codex/gemini paths)

## Capabilities

### New Capabilities
- `runtime-opencode`: OpenCode runtime adapter — CLI invocation, config generation, output parsing, system prompt handling, MCP server wiring

### Modified Capabilities
- `core-spawner`: Add OpenCode to the list of registered runtime adapters and daemon instantiation logic

## Impact

- **Code:** New file `src/butlers/core/runtimes/opencode.py`, modifications to `src/butlers/daemon.py` (adapter instantiation)
- **Config:** Butlers can set `[runtime] type = "opencode"` in `butler.toml`; requires `opencode` binary on PATH (already installed in Dockerfile via `opencode-ai`)
- **Tests:** New test file `tests/adapters/test_opencode_adapter.py`, update registry tests
- **Dependencies:** No new Python dependencies (subprocess-based like Codex/Gemini)
- **Environment:** Receives declared `[butler.env]` vars; runtime authentication uses CLI-level OAuth tokens
