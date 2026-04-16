## 1. ABC and Daemon Contract

- [ ] 1.1 Update `Module.register_tools` abstract signature in `src/butlers/modules/base.py` to add `butler_name: str` as 4th positional parameter
- [ ] 1.2 Update daemon `_register_module_tools` in `src/butlers/daemon.py` to pass `self.config.name` as 4th arg to `register_tools`
- [ ] 1.3 Update daemon `_wire_module_runtime` in `src/butlers/daemon.py` to drop `self.config.name` from the `wire_fn()` call

## 2. Identity-Consuming Modules

- [ ] 2.1 Calendar: accept `butler_name` param in `register_tools`, store as `self._butler_name`, delete `_resolve_butler_name()` static method
- [ ] 2.2 Google Drive: accept `butler_name` param in `register_tools`, replace `db.schema` identity derivation
- [ ] 2.3 Metrics: accept `butler_name` param in `register_tools`, replace `db.schema` identity derivation
- [ ] 2.4 QA: accept `butler_name` param in `register_tools`, store `self._butler_name` there instead of in `wire_runtime`; remove `butler_name` from `wire_runtime` signature
- [ ] 2.5 Self-healing: accept `butler_name` param in `register_tools`, store `self._butler_name` there instead of in `wire_runtime`; remove `butler_name` from `wire_runtime` signature

## 3. Signature-Only Modules (src/butlers/modules/)

- [ ] 3.1 Update `register_tools` signature on: email, telegram, spotify, steam, pipeline, approvals, contacts, mailbox, whatsapp, memory

## 4. Signature-Only Modules (roster/)

- [ ] 4.1 Update `register_tools` signature on all roster module implementations (relationship, home, general, health, finance, travel, messenger, switchboard, education, etc.)

## 5. Tests

- [ ] 5.1 Update all `register_tools` call sites in `tests/` (~55 calls across ~22 files) to pass a `butler_name` string
- [ ] 5.2 Update all `register_tools` stub/mock class definitions in tests (~15 stubs) to accept `butler_name`
- [ ] 5.3 Update all `wire_runtime` call sites in tests (~12 calls across 3-4 files) to drop `butler_name` argument
- [ ] 5.4 Review and update `test_mcp_only_inter_butler.py` contract test that asserts on `register_tools` signature shape
- [ ] 5.5 Run full test suite and fix any failures

## 6. Documentation

- [ ] 6.1 Update RFC 0002 (`about/legends-and-lore/rfcs/0002-mcp-tool-surface-and-modules.md`) Module ABC signature block
- [ ] 6.2 Update root `CLAUDE.md` Module ABC quick-reference
- [ ] 6.3 Sync delta spec to main spec via `/opsx:sync`
