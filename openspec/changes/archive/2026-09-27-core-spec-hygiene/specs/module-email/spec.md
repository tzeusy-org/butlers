## MODIFIED Requirements

### Requirement: EmailConfig with Credential Scoping

Configuration SHALL support independent enable/disable per identity scope with configurable env var names for credentials.

#### Scenario: Config structure

- **WHEN** `[modules.email]` is configured
- **THEN** it includes `smtp_host` (default "smtp.gmail.com"), `smtp_port` (default 587), `imap_host` (default "imap.gmail.com"), `imap_port` (default 993), `use_tls` (default true)
- **AND** `[modules.email.user]` with `enabled` (default false), `address_env`, `password_env`
- **AND** `[modules.email.bot]` with `enabled` (default true), `address_env`, `password_env`

#### Scenario: Env var name validation

- **WHEN** credential env var names are configured
- **THEN** they must match the pattern `^[A-Za-z_][A-Za-z0-9_]*$`
- **AND** empty or whitespace-only values are rejected

### Requirement: Credential Resolution

Credentials SHALL be resolved at startup via CredentialStore (DB-first, then env) and cached.

#### Scenario: Startup credential resolution

- **WHEN** `on_startup` is called with a credential store
- **THEN** all configured credential keys are resolved and cached in `_resolved_credentials`
- **AND** runtime helpers use the cached values first, falling back to `os.environ`

#### Scenario: credentials_env property

- **WHEN** `credentials_env` is queried
- **THEN** it returns the env var names for the bot scope only (address and password) when the bot scope is enabled
- **AND** user-scope credentials are NOT included; they are resolved from the owner `entity_info` record, not from environment variables

### Requirement: IMAP Inbox Search

Email inbox search SHALL use IMAP SEARCH commands via stdlib `imaplib`.

#### Scenario: Search inbox

- **WHEN** `email_search_inbox` is called with a query string
- **THEN** IMAP SEARCH is executed against the INBOX folder
- **AND** up to 50 most recent matching message headers are returned with `message_id`, `from`, `subject`, `date`
- **AND** blocking IMAP calls are run via `asyncio.to_thread`

### Requirement: IMAP Message Reading

The module SHALL support full message reading via IMAP FETCH.

#### Scenario: Read a message

- **WHEN** `email_read_message` is called with a message_id
- **THEN** the full RFC822 message is fetched via IMAP
- **AND** the response includes `message_id`, `from`, `to`, `subject`, `date`, `rfc_message_id`, `body`
- **AND** multipart messages extract the text/plain part; single-part messages decode the payload
