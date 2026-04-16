## 1. Config Schema Extension

- [ ] 1.1 Add `ButlerType` enum (`BUTLER`, `STAFFER`) to `src/butlers/config.py`
- [ ] 1.2 Add `type` field to `ButlerConfig` dataclass (default `ButlerType.BUTLER`), parse from `[butler].type` in TOML
- [ ] 1.3 Add `PermissionsConfig` dataclass with `cross_butler_access: list[str]` field, parse from `[butler.permissions]` in TOML
- [ ] 1.4 Write unit tests for config parsing: default type, explicit butler type, explicit staffer type, permissions section present/absent, wildcard vs scoped access

## 2. TOML Updates (Reclassify Switchboard and Messenger)

- [ ] 2.1 Add `type = "staffer"` and `[butler.permissions]` with `cross_butler_access = ["*"]` to `roster/switchboard/butler.toml`
- [ ] 2.2 Add `type = "staffer"` and `[butler.permissions]` with `cross_butler_access = ["*"]` to `roster/messenger/butler.toml`

## 3. Daemon Type-Aware Logic

- [ ] 3.1 Update schedule sync in `daemon.py` to skip `daily_briefing_contribution` registration when `config.type == ButlerType.STAFFER`
- [ ] 3.2 Update switchboard registration payload to include the agent's `type` field
- [ ] 3.3 Write unit tests for staffer briefing exclusion and registration type inclusion

## 4. Switchboard Routing Exclusion

- [ ] 4.1 Update switchboard classifier to exclude `type = "staffer"` agents from user-message routing candidate set
- [ ] 4.2 Update switchboard agent registry to store and expose the `type` field from registration
- [ ] 4.3 Update `correct_route` tool to reject re-dispatch targets that are staffer-typed
- [ ] 4.4 Write unit tests for routing exclusion, butler-to-staffer routing preserved, and misroute re-dispatch rejection

## 5. Briefing Aggregation Exclusion

- [ ] 5.1 Update `collect_briefing_contributions` job to filter collection to butler-typed agents only
- [ ] 5.2 Write unit test verifying staffers are excluded from briefing contribution collection

## 6. Infrastructure Contract Manifesto Reframing

- [ ] 6.1 Rewrite `roster/switchboard/MANIFESTO.md` with infrastructure-contract framing (SLAs, responsibilities, failure modes, dependencies, capacity)
- [ ] 6.2 Rewrite `roster/messenger/MANIFESTO.md` with infrastructure-contract framing
- [ ] 6.3 Update `roster/switchboard/AGENTS.md` to reflect staffer identity
- [ ] 6.4 Update `roster/messenger/AGENTS.md` to reflect staffer identity

## 7. Heart-and-Soul Doctrine Updates

- [ ] 7.1 Update `about/heart-and-soul/vision.md`: add staffer to vocabulary, amend non-negotiable rule #6 to cover both butlers and staffers
- [ ] 7.2 Update `about/heart-and-soul/architecture.md`: add staffer archetype section under "Domain Specialization Over Monolith"
- [ ] 7.3 Update `about/heart-and-soul/v1.md`: reclassify switchboard and messenger from "Butlers" to "Staffers" in scope listing

## 8. legends-and-lore RFC Updates

- [ ] 8.1 Update `about/legends-and-lore/rfcs/0003-switchboard-routing-and-ingestion.md`: formalize staffer routing exclusion and butler-to-staffer routing
- [ ] 8.2 Update `about/legends-and-lore/rfcs/0006-database-schema-and-isolation.md`: describe staffer schema permissions and cross-butler access model

## 9. Dashboard API

- [ ] 9.1 Expose `type` field in butler management API response (butler list endpoint)
- [ ] 9.2 Update dashboard frontend to visually separate butlers from staffers (separate section or badge)

## 10. Spec Reconciliation

- [ ] 10.1 Sync delta specs to main openspec specs: `staffer-archetype`, `butler-base-spec`, `butler-switchboard`, `butler-messenger`, `core-daemon`, `cross-butler-briefing-contribution`
- [ ] 10.2 Verify all code changes conform to spec scenarios via targeted test runs

## 11. Cruft Cleanup

- [ ] 11.1 Audit daemon.py for hardcoded `config.name == "switchboard"` and `config.name == "messenger"` checks that should be `config.type == ButlerType.STAFFER`
- [ ] 11.2 Remove any deprecated butler-specific special-casing that is now covered by the type field
- [ ] 11.3 Update any documentation or comments that reference "core butlers" to use the staffer terminology
