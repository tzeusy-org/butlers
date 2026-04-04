## MODIFIED Requirements

### Requirement: Messenger Butler Identity and Runtime
The messenger is a staffer — a delivery-only execution plane with no domain logic. It serves the ecosystem by owning all outbound channel delivery.

#### Scenario: Identity and port
- **WHEN** the messenger daemon starts
- **THEN** it operates on port 41104 with description "Outbound delivery execution plane for Telegram and Email"
- **AND** its `butler.toml` has `type = "staffer"`
- **AND** it uses the `codex` runtime adapter with a maximum of 3 concurrent sessions
- **AND** its database schema is `messenger` within the consolidated `butlers` database

#### Scenario: Module profile
- **WHEN** the messenger daemon starts
- **THEN** it loads modules: `calendar` (Google provider, suggest conflicts policy), `telegram` (bot-only, user disabled, token from `BUTLER_TELEGRAM_TOKEN`), and `email` (bot-only, user disabled, address from `BUTLER_EMAIL_ADDRESS`, password from `BUTLER_EMAIL_PASSWORD`)

#### Scenario: Cross-butler permissions
- **WHEN** the messenger daemon starts
- **THEN** its `butler.toml` declares `[butler.permissions]` with `cross_butler_access = ["*"]`
- **AND** the messenger is authorized to act on behalf of any butler for outbound delivery

#### Scenario: Messenger excluded from user-message routing
- **WHEN** the switchboard classifies an incoming user message
- **THEN** the messenger SHALL NOT be a routing candidate because `type = "staffer"`

#### Scenario: Messenger excluded from daily briefing
- **WHEN** the daemon syncs scheduled tasks at startup
- **THEN** the messenger SHALL NOT register any `daily_briefing_contribution` job
- **AND** the briefing aggregation SHALL NOT attempt to collect a contribution from the messenger

### Requirement: Messenger Infrastructure Contract
The messenger's MANIFESTO.md uses infrastructure-contract framing rather than user-relationship framing.

#### Scenario: Infrastructure contract content
- **WHEN** the messenger's `MANIFESTO.md` is authored
- **THEN** it defines: service responsibilities (outbound delivery ownership), SLAs (delivery latency, availability), failure modes (channel unavailable, rate limiting, auth failures), recovery procedures (retry, fallback, escalation), dependency graph (depends on switchboard for routing, telegram/email APIs for delivery), and capacity limits (concurrent sessions, queue depth)
