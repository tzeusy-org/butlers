## Context

The runtime adapter registry (`src/butlers/core/runtimes/`) provides three adapters: `claude-code` (in-process SDK), `codex` (subprocess), and `gemini` (subprocess). OpenCode is a Go-based AI CLI that supports multiple providers through a unified interface. The `opencode` npm package is already installed in the Docker image. OpenCode exposes two programmatic interfaces: a CLI (`opencode run --format json`) and a JS/TS SDK. Since the butler codebase is Python, the subprocess CLI approach (matching Codex/Gemini patterns) is the natural fit.

Key OpenCode CLI characteristics discovered during research:
- Headless mode: `opencode run [message] --format json`
- Model selection: `--model provider/model` (e.g., `anthropic/claude-sonnet-4-5`)
- MCP config: JSONC file with `mcp` section containing `local` (stdio) and `remote` (HTTP) server types
- System prompt: Via `instructions` config array (file paths/globs) or custom agent definitions
- Config precedence: `OPENCODE_CONFIG` env var points to a config file
- Permission bypass: Configurable in config file (no `--full-auto` flag)

## Goals / Non-Goals

**Goals:**
- Implement `OpenCodeAdapter` following the established `RuntimeAdapter` interface
- Support OpenCode CLI invocation via subprocess with JSON output parsing
- Wire MCP servers using OpenCode's JSONC config format with `remote` type
- Pass system prompt via temporary instruction file referenced in config
- Register `"opencode"` in the adapter registry for `butler.toml` selection
- Achieve feature parity with the Codex adapter (model selection, timeout, env, cwd, tool call extraction, usage tracking)

**Non-Goals:**
- JS/TS SDK integration (would require Node.js bridge from Python)
- OpenCode server mode (`opencode serve` + attach) — adds complexity without clear benefit for ephemeral invocations
- OAuth-based MCP server authentication (butlers use direct URL + env-based API keys)
- Structured output support (butlers use free-form text responses)

## Decisions

### Decision 1: Subprocess CLI over SDK
**Choice:** Use `opencode run --format json` via `asyncio.create_subprocess_exec`, matching the Codex/Gemini pattern.

**Rationale:** The OpenCode SDK is JS/TS-only. Using it from Python would require spawning a Node.js bridge process, adding complexity and a Node.js runtime dependency at invocation time. The CLI approach is simpler, proven (Codex/Gemini both use it), and the `--format json` flag provides structured output.

**Alternative considered:** Wrapping the SDK in a small Node.js script — rejected because it adds a build artifact, complicates error handling, and the CLI provides equivalent functionality.

### Decision 2: Temp config file via OPENCODE_CONFIG
**Choice:** Write a temporary `opencode.jsonc` per invocation and pass it via `OPENCODE_CONFIG` env var.

**Rationale:** OpenCode's MCP servers, model, permissions, and instructions are all configured via config file (not CLI flags like Codex's `-c`). Writing a temp config is the cleanest way to inject per-invocation settings. The `OPENCODE_CONFIG` env var overrides all other config sources.

**Alternative considered:** Using `--model` flag + `opencode mcp add` at runtime — rejected because MCP add is interactive and config file gives atomic control over all settings.

### Decision 3: System prompt via instructions config
**Choice:** Write the system prompt to a temp file and reference it in the config's `instructions` array.

**Rationale:** OpenCode reads system instructions from files referenced in its config. This avoids shell escaping issues with long prompts and matches OpenCode's design. The adapter writes a temp `_system_prompt.md` alongside the config and references it as `instructions: ["./_system_prompt.md"]`.

**Alternative considered:** Embedding system prompt in the user prompt (like Codex's XML-tag approach) — acceptable fallback but loses the semantic separation OpenCode provides natively.

### Decision 4: System prompt file convention
**Choice:** Read `OPENCODE.md` first, fall back to `AGENTS.md` (matching Gemini's dual-file pattern).

**Rationale:** Allows butler authors to provide OpenCode-specific instructions while maintaining compatibility with existing `AGENTS.md` files used by Codex.

### Decision 5: MCP server config format
**Choice:** Map butler MCP servers to OpenCode's `remote` type with `url` and empty `headers`.

**Rationale:** All butler MCP servers are HTTP-based (streamable HTTP or SSE). OpenCode's `remote` type accepts a URL and optional headers, which maps directly. No stdio/local servers are needed since butlers always connect to their own MCP endpoint.

### Decision 6: Permission bypass
**Choice:** Set `"permission": {}` in the config (empty object disables all permission prompts).

**Rationale:** Butlers run in fully automated mode. OpenCode doesn't have a `--full-auto` flag; instead, an empty permission config disables interactive approval. This is verified against OpenCode's config docs.

## Risks / Trade-offs

- **[JSON output format undocumented]** The exact event shapes from `--format json` are not fully documented. → Mitigation: Build a flexible parser (like Codex's) that handles multiple event shapes, with fallback to plain text. Add diagnostic logging for unknown event types.
- **[Model format coupling]** OpenCode uses `provider/model` format while butler.toml uses bare model names. → Mitigation: The adapter passes `model` through as-is; butler authors using OpenCode must use the `provider/model` format in their `butler.toml` `[butler.runtime] model` field.
- **[Config file cleanup]** Temp config files must be cleaned up after each invocation. → Mitigation: Use `tempfile.TemporaryDirectory` context manager (same pattern as other adapters if applicable, or explicit cleanup in finally block).
- **[Binary version drift]** OpenCode CLI is actively developed; output format may change. → Mitigation: Pin `opencode-ai` version in Dockerfile, version-gate parser if needed.
