# Module I/O Model: Identity-Scoped User/Bot Tooling

Status: Normative (Architecture Contract)
Last updated: 2026-02-15
Primary owner: Platform/Modules

## 1. Overview

Channel modules (Telegram, Email) expose MCP tools that interact with external identities through two distinct surfaces:

- **User-identity I/O**: Tools that act on behalf of the user's personal account (e.g., send email from user's Gmail, send Telegram message from user's account)
- **Bot-identity I/O**: Tools that act through butler-owned service accounts (e.g., send email from dedicated butler mailbox, send Telegram message from bot token)

This separation ensures:
1. **Clear identity accountability**: Every tool call explicitly declares whose identity it represents
2. **Safety-by-default approval policies**: User-identity outputs are gated by default to prevent unauthorized impersonation
3. **Flexible credential scoping**: User and bot credentials are configured independently per module
4. **Audit clarity**: Session logs and approval events track which identity surface was used

## 2. Design Principles

### 2.1 Identity-Explicit Tooling

Every channel tool name must declare whether it acts as `user_*` or `bot_*`. There are no identity-ambiguous tool names.

**Required naming format:**
- `user_<channel>_<action>`
- `bot_<channel>_<action>`

**Examples:**
- `user_telegram_send_message` (send as user's Telegram account)
- `bot_telegram_send_message` (send as butler's Telegram bot)
- `user_email_search_inbox` (search user's Gmail inbox)
- `bot_email_search_inbox` (search butler's dedicated mailbox)

### 2.2 Direction-Explicit Tooling

Tools are categorized by direction (input vs output) in module metadata:

- **Inputs**: Tools that read or receive data from external sources
  - Examples: `bot_telegram_get_updates`, `bot_email_search_inbox`, `bot_email_read_message`
  - Generally do not require approval
  
- **Outputs**: Tools that send data or perform actions externally
  - Examples: `bot_telegram_send_message`, `bot_telegram_reply_to_message`, `bot_email_send_message`
  - Subject to approval policies based on identity and risk

Modules declare these surfaces via `Module` base class methods:
- `user_inputs() -> tuple[ToolIODescriptor, ...]`
- `user_outputs() -> tuple[ToolIODescriptor, ...]`
- `bot_inputs() -> tuple[ToolIODescriptor, ...]`
- `bot_outputs() -> tuple[ToolIODescriptor, ...]`

### 2.3 Approval-by-Default Safety

Different identity surfaces have different default approval requirements:

**User-identity outputs** (high risk of impersonation):
- `approval_default="always"` for send/reply actions
- Requires explicit human approval unless covered by standing rule
- Examples: `user_telegram_send_message`, `user_email_send_message`

**Bot-identity outputs** (butler service account):
- `approval_default="conditional"` for most actions
- Can be configured per butler based on trust level and use case
- Examples: `bot_telegram_send_message`, `bot_email_send_message`

**All inputs** (read operations):
- `approval_default="none"` by default
- Sensitive read scopes can be flagged in `tool_metadata()` if needed

### 2.4 No Legacy Compatibility

This is a greenfield refactor with strict enforcement:
- No unprefixed tool names are registered
- No backward-compatibility aliases or wrappers
- All code, tests, prompts, and docs use only prefixed names
- Naming compliance is enforced during module registration

## 3. Tool I/O Descriptor

The `ToolIODescriptor` dataclass defines structured metadata for each tool:

```python
@dataclass(frozen=True)
class ToolIODescriptor:
    """Structured descriptor for a module's MCP tool I/O surface.
    
    Attributes:
        name: Registered MCP tool name.
        description: Optional short description of the tool intent.
        approval_default: Default approval behavior for output tools.
    """
    name: str
    description: str = ""
    approval_default: Literal["none", "conditional", "always"] = "none"
```

### Approval Default Values

| Value | Meaning | Typical Use Case |
|-------|---------|------------------|
| `"none"` | Never requires approval | Input tools, safe read operations |
| `"conditional"` | Approval determined by policy | Bot outputs, configurable per butler |
| `"always"` | Always requires approval unless standing rule matches | User outputs (send/reply) |

## 4. Module Contract

### 4.1 Required Base Class Methods

Every module inherits from `Module` ABC and can override these I/O declaration methods:

```python
class Module(abc.ABC):
    def user_inputs(self) -> tuple[ToolIODescriptor, ...]:
        """Return user-facing input tool descriptors declared by this module."""
        return ()

    def user_outputs(self) -> tuple[ToolIODescriptor, ...]:
        """Return user-facing output tool descriptors declared by this module."""
        return ()

    def bot_inputs(self) -> tuple[ToolIODescriptor, ...]:
        """Return bot-facing input tool descriptors declared by this module."""
        return ()

    def bot_outputs(self) -> tuple[ToolIODescriptor, ...]:
        """Return bot-facing output tool descriptors declared by this module."""
        return ()
```

### 4.2 Tool Registration

Modules register tools in `register_tools()` with identity-prefixed names that match the declared descriptors:

```python
async def register_tools(self, mcp: Any, config: Any, db: Any) -> None:
    """Register identity-prefixed MCP tools."""
    
    # User-identity tools
    async def user_telegram_send_message(chat_id: str, text: str) -> dict:
        return await self._send_message_as_user(chat_id, text)
    
    mcp.tool()(user_telegram_send_message)
    
    # Bot-identity tools
    async def bot_telegram_send_message(chat_id: str, text: str) -> dict:
        return await self._send_message_as_bot(chat_id, text)
    
    mcp.tool()(bot_telegram_send_message)
```

## 5. Telegram Module Implementation

### 5.1 Tool Inventory

**User-identity tools:**
- `user_telegram_get_updates` (input, approval_default="none")
- `user_telegram_send_message` (output, approval_default="always")
- `user_telegram_reply_to_message` (output, approval_default="always")

**Bot-identity tools:**
- `bot_telegram_get_updates` (input, approval_default="none")
- `bot_telegram_send_message` (output, approval_default="conditional")
- `bot_telegram_reply_to_message` (output, approval_default="conditional")

### 5.2 Example I/O Descriptors

```python
def user_outputs(self) -> tuple[ToolIODescriptor, ...]:
    """User-identity Telegram output tools.
    
    User send/reply tools are marked as approval-required defaults.
    """
    return (
        ToolIODescriptor(
            name="user_telegram_send_message",
            description="Send as user. approval_default=always (approval required).",
            approval_default="always",
        ),
        ToolIODescriptor(
            name="user_telegram_reply_to_message",
            description="Reply as user. approval_default=always (approval required).",
            approval_default="always",
        ),
    )

def bot_outputs(self) -> tuple[ToolIODescriptor, ...]:
    """Bot-identity Telegram output tools."""
    return (
        ToolIODescriptor(
            name="bot_telegram_send_message",
            description="Send as bot. approval_default=conditional.",
            approval_default="conditional",
        ),
        ToolIODescriptor(
            name="bot_telegram_reply_to_message",
            description="Reply as bot. approval_default=conditional.",
            approval_default="conditional",
        ),
    )
```

### 5.3 Use Cases

**User-identity Telegram flow** (full account visibility):
- **Source**: Telegram client session tied to user account
- **Input tools**: Read conversations the user sees across their account
- **Output tools**: Send/reply as the user (requires approval)
- **Typical scenario**: Butler monitors user's Telegram for mentions, drafts replies that user approves

**Bot-identity Telegram flow** (DM / command model):
- **Source**: Telegram bot webhook/polling
- **Input tools**: Read messages sent to the butler bot
- **Output tools**: Send/reply as the butler bot (conditional approval based on policy)
- **Typical scenario**: Users send commands to butler bot, bot responds directly

### 5.4 Configuration

Credentials are scoped per identity in `butler.toml`:

```toml
[modules.telegram.user]
enabled = true
token_env = "USER_TELEGRAM_TOKEN"  # User account session token

[modules.telegram.bot]
enabled = true
token_env = "BUTLER_TELEGRAM_TOKEN"  # Butler bot API token
```

## 6. Email Module Implementation

### 6.1 Tool Inventory

**User-identity tools:**
- `user_email_search_inbox` (input, approval_default="none")
- `user_email_read_message` (input, approval_default="none")
- `user_email_send_message` (output, approval_default="always")
- `user_email_reply_to_thread` (output, approval_default="always")

**Bot-identity tools:**
- `bot_email_search_inbox` (input, approval_default="none")
- `bot_email_read_message` (input, approval_default="none")
- `bot_email_check_and_route_inbox` (input, approval_default="none")
- `bot_email_send_message` (output, approval_default="conditional")
- `bot_email_reply_to_thread` (output, approval_default="conditional")

### 6.2 Example I/O Descriptors

```python
def user_outputs(self) -> tuple[ToolIODescriptor, ...]:
    """Declare user-identity email output tools.
    
    User send/reply actions are approval-required defaults.
    """
    return (
        ToolIODescriptor(
            name="user_email_send_message",
            description=(
                "Send outbound email from user-scoped tool surface (approval-required default)."
            ),
            approval_default="always",
        ),
        ToolIODescriptor(
            name="user_email_reply_to_thread",
            description=(
                "Reply to email thread from user-scoped tool surface "
                "(approval-required default)."
            ),
            approval_default="always",
        ),
    )

def bot_outputs(self) -> tuple[ToolIODescriptor, ...]:
    """Declare bot-identity email output tools.
    
    Bot send/reply actions are conditionally approval-gated by policy.
    """
    return (
        ToolIODescriptor(
            name="bot_email_send_message",
            description=(
                "Send outbound email from bot-scoped tool surface "
                "(approval_default=conditional)."
            ),
            approval_default="conditional",
        ),
        ToolIODescriptor(
            name="bot_email_reply_to_thread",
            description=(
                "Reply to email thread from bot-scoped tool surface "
                "(approval_default=conditional)."
            ),
            approval_default="conditional",
        ),
    )
```

### 6.3 Use Cases

**User-identity email flow** (user's personal mailbox):
- **Source**: User-owned mailbox via OAuth (e.g., Gmail API)
- **Credentials**: User Gmail OAuth credentials
- **Input tools**: Search user inbox, read messages from user account
- **Output tools**: Send/reply from user's email address (requires approval)
- **Typical scenario**: Butler triages user's inbox, drafts replies for approval

**Bot-identity email flow** (dedicated butler mailbox):
- **Source**: Butler-owned inbox (e.g., support@butler.example.com)
- **Credentials**: Butler mailbox SMTP/IMAP credentials
- **Input tools**: Search butler inbox, check and route incoming mail
- **Output tools**: Send/reply from butler email (conditional approval)
- **Typical scenario**: External parties email butler's dedicated address, butler responds

### 6.4 Configuration

Credentials are scoped per identity in `butler.toml`:

```toml
[modules.email.user]
enabled = true
address_env = "USER_EMAIL_ADDRESS"
password_env = "USER_EMAIL_PASSWORD"

[modules.email.bot]
enabled = true
address_env = "BUTLER_EMAIL_ADDRESS"
password_env = "BUTLER_EMAIL_PASSWORD"
```

## 7. Approval Integration

The Approvals module integrates with identity-scoped I/O through two mechanisms:

### 7.1 Default Gating from I/O Descriptors

During startup, the approvals module merges identity-aware defaults:

1. All user output tools with `approval_default="always"` are automatically added to the gated tools list
2. User send/reply tools are caught by a safety-net heuristic even without explicit metadata
3. Bot outputs are not default-gated unless explicitly configured in `butler.toml`

### 7.2 Naming-Based Safety Net

The approvals gate includes heuristic detection for high-risk tool names:

- Any tool matching `user_*_send*` or `user_*_reply*` is treated as approval-required
- This prevents accidental bypass if a module forgets to declare `approval_default="always"`

### 7.3 Configuration Example

```toml
[modules.approvals]
enabled = true
default_expiry_hours = 48
default_risk_tier = "medium"

[modules.approvals.gated_tools]
# User outputs are auto-gated by I/O descriptors
# Bot outputs require explicit config to gate:
bot_telegram_send_message = { expiry_hours = 24, risk_tier = "low" }
bot_email_send_message = { expiry_hours = 48, risk_tier = "medium" }
```

## 8. Credential Forwarding

When a butler spawns an ephemeral Claude Code instance:

1. Module declares required env vars via `credentials_env` property
2. Startup credential checker validates all required vars are present
3. MCP config generator includes all module credentials in the Claude Code environment
4. Claude Code instance can invoke both user and bot tools with appropriate credentials

**Example credential declaration:**

```python
@property
def credentials_env(self) -> list[str]:
    """Environment variables required for identity-scoped credentials."""
    required = []
    
    if self._config.user.enabled:
        required.append(self._config.user.token_env)
    
    if self._config.bot.enabled:
        required.append(self._config.bot.token_env)
    
    return required
```

## 9. Audit and Session Logging

Session logs and approval events record identity surface used:

**Session log fields:**
- `tool_name`: Includes identity prefix (e.g., `user_telegram_send_message`)
- `tool_args`: Full argument payload
- `result`: Tool execution result

**Approval event fields:**
- `tool_name`: Identity-prefixed name
- `tool_args`: Argument payload (with sensitive fields redacted)
- `approval_rule_id`: Standing rule that matched (if auto-approved)
- `actor`: Human approver for manual decisions

This provides full audit trail of which identity was used for each action.

## 10. Migration from Legacy Names

The refactor from unprefixed to identity-prefixed names was completed in the `butlers-bj0` epic series:

### 10.1 Completed Migration Steps

1. **Module interface additions** (butlers-bj0.1):
   - Added `user_inputs()`, `user_outputs()`, `bot_inputs()`, `bot_outputs()` to `Module` base
   - Added `ToolIODescriptor` dataclass
   - Added naming validation during module registration

2. **Telegram refactor** (butlers-bj0.2):
   - Replaced unprefixed tools (e.g. `telegram_send_msg`, `telegram_reply`, `telegram_updates`)
   - With prefixed `user_telegram_*` and `bot_telegram_*` tools
   - Updated all tests and callsites

3. **Email refactor** (butlers-bj0.3):
   - Replaced unprefixed tools (e.g. `email_send`, `email_search`, etc.)
   - With prefixed `user_email_*` and `bot_email_*` tools
   - Updated all tests and callsites

4. **Approval integration** (butlers-bj0.4):
   - Identity-aware approval defaults
   - Safety-net heuristics for user send/reply tools
   - Approval event audit includes identity context

5. **Config split** (butlers-bj0.5):
   - Telegram config: `[modules.telegram.user]` and `[modules.telegram.bot]`
   - Email config: `[modules.email.user]` and `[modules.email.bot]`
   - Independent enable/disable per identity scope

6. **Routing update** (butlers-bj0.6):
   - Message pipeline includes identity-aware source metadata
   - Switchboard prompts explain when approval is needed for user-identity outputs

7. **Legacy cleanup** (butlers-bj0.7):
   - Removed all unprefixed tool names
   - Enforced naming compliance checks
   - Verified no legacy references in code, tests, prompts, or docs

8. **Test coverage** (butlers-bj0.8):
   - User/bot I/O flow tests for Telegram and Email
   - Approval default validation tests
   - Naming compliance enforcement tests

### 10.2 No Backward Compatibility

There are no compatibility shims, aliases, or fallback paths for legacy names. All tools use identity-prefixed names consistently across the codebase.

## 11. Best Practices

### For Module Developers

1. **Always use identity prefixes**: Never register tools without `user_` or `bot_` prefix
2. **Declare I/O descriptors**: Override `user_inputs()`, `user_outputs()`, etc. with complete metadata
3. **Set approval defaults correctly**:
   - User outputs: `approval_default="always"`
   - Bot outputs: `approval_default="conditional"`
   - Inputs: `approval_default="none"`
4. **Split credentials**: Use separate config scopes for user and bot credentials
5. **Document identity semantics**: Clearly explain what each identity surface represents in tool docstrings

### For Butler Operators

1. **Configure both scopes**: Decide whether to enable user-identity, bot-identity, or both
2. **Set approval policies**: Configure `[modules.approvals.gated_tools]` for bot outputs if needed
3. **Use standing rules**: Create approval rules for routine user-identity actions after manual review
4. **Monitor audit logs**: Review which identity surface was used for each session

### For Claude Code Instances

1. **Choose identity explicitly**: Select user or bot tools based on desired outcome
2. **Explain approval gates**: When user tools are gated, explain why approval is needed
3. **Respect approval responses**: Don't retry blocked user actions without approval
4. **Use bot tools for routine tasks**: Prefer bot-identity for automated, low-risk operations

## 12. Future Extensions

Potential enhancements to the I/O model:

1. **Multi-user support**: Extend `user_*` tools to support multiple user identities per butler
2. **Delegation patterns**: Allow user to delegate approval authority for specific tool/arg combinations
3. **Read-scoped gating**: Add approval requirements for sensitive read operations (e.g., reading specific email folders)
4. **Identity attestation**: Include cryptographic proof of identity in tool execution results
5. **Cross-butler delegation**: Allow user to authorize one butler to act through another butler's bot identity

## 13. Related Documentation

- **Approval module contract**: `docs/modules/approval.md`
- **Implementation plan**: `docs/INPUT_OUTPUT_TOOLING_REFACTOR_PLAN.md`
- **Telegram connector**: `docs/connectors/telegram_bot.md`, `docs/connectors/telegram_user_client.md`
- **Email connector**: `docs/connectors/gmail.md`
- **Module base class**: `src/butlers/modules/base.py`

## 14. Glossary

| Term | Definition |
|------|------------|
| **Identity surface** | The set of tools that act through a specific identity (user or bot) |
| **User-identity I/O** | Tools that act on behalf of the user's personal account |
| **Bot-identity I/O** | Tools that act through butler-owned service accounts |
| **I/O descriptor** | Structured metadata describing a tool's identity, direction, and approval policy |
| **Approval default** | Default approval requirement for a tool (`none`, `conditional`, `always`) |
| **Standing rule** | Pre-approved pattern for automatic approval of matching tool invocations |
| **Gated tool** | Tool subject to approval workflow before execution |
| **Identity prefix** | Required `user_` or `bot_` prefix in tool names |
