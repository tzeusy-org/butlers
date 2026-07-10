## ADDED Requirements

### Requirement: Module Tool Naming Convention
Module MCP tool names SHALL use the plain `<channel>_<action>` format (e.g. `telegram_send_message`, `email_send_message`). No `user_*` / `bot_*` prefix convention applies: the daemon SHALL NOT wrap module tools in a per-audience (user vs. bot) I/O model. Accordingly, the `Module` ABC SHALL NOT define `user_inputs()`, `user_outputs()`, `bot_inputs()`, or `bot_outputs()` descriptor methods, and the daemon SHALL NOT carry a tool-I/O validation layer — the `ToolIODescriptor` dataclass, `_validate_tool_name()`, `_validate_module_io_descriptors()`, `_is_user_send_or_reply_tool()`, `_with_default_gated_user_outputs()`, `_CHANNEL_EGRESS_ACTIONS`, and `ModuleToolValidationError` are absent and SHALL NOT be reintroduced.

#### Scenario: Tool registered with plain name
- **WHEN** the Telegram module registers a send tool
- **THEN** the tool MUST be named `telegram_send_message` (not `user_telegram_send_message` or `bot_telegram_send_message`)

#### Scenario: Module ABC does not require descriptor methods
- **WHEN** a module class implements the `Module` ABC
- **THEN** it MUST NOT be required to implement `user_inputs()`, `user_outputs()`, `bot_inputs()`, or `bot_outputs()`
