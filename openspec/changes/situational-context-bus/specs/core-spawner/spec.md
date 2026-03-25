# LLM CLI Spawner — Context Bus Integration

## MODIFIED Requirements

### Requirement: System Prompt Composition
The system prompt is read from `CLAUDE.md` in the butler's config directory. Include directives (`<!-- @include path/to/file.md -->`) are resolved relative to the roster directory. Shared prompt snippets (`BUTLER_SKILLS.md`, `MCP_LOGGING.md`) are appended if present. When active situational context signals exist, the spawner SHALL call `get_active_context()` and `format_context_preamble()` to prepend a context summary to the system prompt. The context preamble SHALL appear after the identity preamble and before the memory context block. Context preamble injection is fail-open: if the context query fails, the spawner logs the error and proceeds without context.

#### Scenario: System prompt with includes
- **WHEN** `CLAUDE.md` contains `<!-- @include shared/NOTIFY.md -->`
- **THEN** the directive is replaced with the contents of `roster/shared/NOTIFY.md`

#### Scenario: Context preamble injected when signals active
- **WHEN** the spawner prepares an invocation and `get_active_context()` returns active signals
- **THEN** the context preamble is prepended to the system prompt after the identity preamble
- **AND** the preamble format follows `format_context_preamble()` output

#### Scenario: No context preamble when no signals
- **WHEN** the spawner prepares an invocation and `get_active_context()` returns an empty list
- **THEN** no context preamble is added to the system prompt

#### Scenario: Context query failure does not block invocation
- **WHEN** the `get_active_context()` call raises an exception
- **THEN** the failure is logged at WARNING level
- **AND** the invocation proceeds with the system prompt without context preamble
