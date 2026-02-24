## Tasks

This change is a **spec-only baseline capture** — no code changes are needed. The tasks below complete the OpenSpec lifecycle.

### Sync and Validation

- [x] Create change scaffolding (.openspec.yaml, proposal.md)
- [x] Write proposal with capability inventory
- [x] Write core-daemon spec (from daemon.py, config.py, startup_guard.py, base_butler.md)
- [x] Write core-state spec (from state.py, base_butler.md)
- [x] Write core-scheduler spec (from scheduler.py, scheduler.md)
- [x] Write core-spawner spec (from spawner.py, runtimes/)
- [x] Write core-sessions spec (from sessions.py, base_butler.md)
- [x] Write core-modules spec (from base.py, registry.py, base_butler.md)
- [x] Write core-credentials spec (from credential_store.py, credentials.py, google_credentials.py)
- [x] Write core-skills spec (from skills.py, roster skills)
- [x] Write core-telemetry spec (from telemetry.py, logging.py, metrics.py)
- [x] Write core-notify spec (from daemon.py notify, base_butler.md section 11.1)
- [x] Write switchboard spec (from switchboard_butler.md, route_inbox.py, buffer.py, audit.py)
- [x] Write connectors shared interface spec (from interface.md, heartbeat.py, metrics.py, mcp_client.py, contracts.py)
- [x] Write connector-telegram-bot spec (from telegram_bot.py, docs/connectors/telegram_bot.md)
- [x] Write connector-telegram-user-client spec (from telegram_user_client.py, docs/connectors/telegram_user_client.md)
- [x] Write connector-gmail spec (from gmail.py, docs/connectors/gmail.md)
- [x] Write connector-discord spec (from docs/connectors/draft_discord.md)
- [x] Write module-approvals spec (from approval.md, approvals/)
- [x] Write module-calendar spec (from calendar.md, calendar.py)
- [x] Write module-contacts spec (from contacts.md, contacts/)
- [x] Write module-email spec (from email.py, gmail.md)
- [x] Write module-mailbox spec (from mailbox/)
- [x] Write module-memory spec (from memory.md, memory/)
- [x] Write module-telegram spec (from telegram.py)
- [x] Write module-pipeline spec (from pipeline.py)
- [x] Write dashboard-shell spec (from frontend layout, navigation, design system)
- [x] Write dashboard-visibility spec (from frontend trace/session/timeline pages)
- [x] Write dashboard-butler-management spec (from frontend butler pages, switchboard views)
- [x] Write dashboard-admin-gateway spec (from frontend secrets, OAuth, approvals, connectors)
- [x] Write dashboard-domain-pages spec (from frontend health, contacts, calendar, memory, costs pages)
- [x] Write dashboard-api spec (from api/, backend-api-contract.md, frontend hooks/queries)
- [x] Write butler-roles spec (from roster/, role docs)
- [x] Write butler-switchboard spec (from roster/switchboard/)
- [x] Write butler-general spec (from roster/general/)
- [x] Write butler-relationship spec (from roster/relationship/)
- [x] Write butler-health spec (from roster/health/)
- [x] Write butler-messenger spec (from roster/messenger/)
- [x] Write butler-finance spec (from roster/finance/)
- [x] Write butler-travel spec (from roster/travel/)
- [x] Write testing spec (from tests/, e2e docs, conftest.py)
- [x] Write design.md
- [x] Write tasks.md
- [ ] Review specs for cross-capability consistency
- [ ] Sync to main specs via `/opsx:sync`
- [ ] Archive change via `/opsx:archive`
