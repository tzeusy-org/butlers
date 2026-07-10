## REMOVED Requirements

### Requirement: I/O model removal
**Reason**: This is the module tool-naming contract (plain `<channel>_<action>` MCP tool names, no `user_*` / `bot_*` I/O descriptor model). It is not a contacts-identity concern — it survived the `retire-contacts-table-specs` archival only because it is not table-centric, and that change deferred its relocation to a follow-up. It is relocated here, not deleted.
**Migration**: See the `core-modules` spec, "Module Tool Naming Convention", which states the live contract in steady-state terms (verified against `src/butlers/modules/telegram.py` / `email.py` and the confirmed absence of the removed I/O-descriptor internals). The transitional "Legacy tool names rejected" scenario is intentionally not carried over — the code enforcing it (`_validate_tool_name()` / `ModuleToolValidationError`) was removed and the behavior is no longer live.
