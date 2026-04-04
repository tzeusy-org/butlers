## Context

The Butlers ecosystem currently treats all 10 agents uniformly as "butlers." In practice, two archetypes have emerged:

- **Domain butlers** (general, health, relationship, finance, travel, education, home, lifestyle): user-facing agents that own a domain of the user's life.
- **Infrastructure agents** (switchboard, messenger): ecosystem-facing agents that provide routing, delivery, and coordination services.

This distinction is currently implicit — special-cased in daemon startup logic (e.g., `pipeline wiring (switchboard only)`, `buffer start (switchboard only)`), routing rules, and the deterministic job registry. The staffer concept formalizes this split without forking the core engine.

## Goals / Non-Goals

**Goals:**
- Introduce a `type` field in `butler.toml` that distinguishes butlers from staffers at config level
- Formalize the permissions and connectivity differences (cross-butler access for staffers)
- Exclude staffers from user-message routing and daily briefing contributions
- Reframe switchboard and messenger manifestos as infrastructure contracts
- Update heart-and-soul doctrine and relevant RFCs to reflect the two-archetype model
- Design for N staffers — the model must accommodate future infrastructure agents (e.g., QA staffer) without architectural changes

**Non-Goals:**
- Implementing the QA staffer (future work, separate change)
- Forking the daemon into separate butler/staffer codepaths — the engine stays unified
- Creating a separate database for staffers — schema isolation model is unchanged
- Changing the MCP protocol or tool composition model
- Changing the module system or registry — staffers use the same module ABC

## Decisions

### D1: Single `type` field in butler.toml, not a separate config file format

**Decision:** Add `type = "butler" | "staffer"` to the `[butler]` table in `butler.toml`. Default is `"butler"`.

**Alternatives considered:**
- Separate `staffer.toml` config format → rejected because the engine is identical; a parallel config format would create drift and maintenance burden.
- A `role` enum with more categories → rejected because the butler/staffer binary is sufficient. Future distinctions (e.g., staffer subtypes) can be expressed through module composition rather than type proliferation.

**Rationale:** The type field is the single source of truth for the distinction. All behavioral differences flow from this field — routing exclusion, briefing exclusion, connectivity permissions.

### D2: Cross-butler connectivity via `permissions` section, not hardcoded

**Decision:** Add an optional `[butler.permissions]` section to `butler.toml` with a `cross_butler_access` list specifying which other agents this agent may connect to or act on behalf of.

```toml
[butler]
type = "staffer"

[butler.permissions]
cross_butler_access = ["*"]  # or explicit list: ["general", "health", "finance"]
```

**Alternatives considered:**
- Implicit "all staffers can access everything" → rejected because a future QA staffer might need different access than messenger. Explicit permissions enable least-privilege.
- Database-level cross-schema grants → rejected because inter-butler communication is MCP-only (non-negotiable rule #3). Cross-butler access means MCP connectivity, not SQL access.

**Rationale:** This makes the permissions model extensible. Switchboard and messenger get `["*"]` (they serve all butlers). A future QA staffer might get `["*"]` for read access but restricted write access via module-level gating.

### D3: Staffers excluded from routing at the classification layer

**Decision:** The switchboard's message classifier excludes agents with `type = "staffer"` from the candidate set for user-message routing. Butlers can still route *to* staffers via switchboard using the existing `notify → messenger` mechanism (or future analogues).

**Alternatives considered:**
- Exclude at the registration layer (staffers don't register with switchboard) → rejected because staffers still need to be reachable via switchboard for butler-to-staffer routing.
- Exclude via a `routable = false` flag → rejected because this is derivable from `type = "staffer"` — adding another flag creates redundancy.

**Rationale:** The switchboard already tracks a registry of available agents. Adding a type-aware filter to classification is minimal code change (the eligibility sweep and routing logic just skip staffer-typed entries for user-message routing).

### D4: Infrastructure contract replaces manifesto for staffers

**Decision:** Staffers use `MANIFESTO.md` with infrastructure-contract framing. Same file, different content structure:

**Butler manifesto framing:** User relationship, value proposition, domain expertise, personality.
**Staffer infrastructure contract framing:** Service responsibilities, SLAs (availability, throughput), failure modes and recovery, dependency graph, capacity limits.

**Alternatives considered:**
- Rename to `CONTRACT.md` for staffers → rejected because it breaks the roster convention that every agent has `MANIFESTO.md`. The convention is the important thing; the framing is content.

**Rationale:** Keeping the same filename means roster tooling and documentation conventions don't need special cases. The content framing naturally differs based on `type`.

### D5: Config enum is source of truth; daemon behavior is type-aware, not type-forked

**Decision:** The daemon reads `config.type` and uses it for conditional behavior at specific decision points:

1. **Switchboard registration:** Staffers with `type = "staffer"` still register with switchboard (for butler-to-staffer routing) but are tagged as non-routable in the registry.
2. **Briefing contribution:** Daemon skips `daily_briefing_contribution` job registration for staffer-typed agents.
3. **Dashboard grouping:** API exposes the type field so the dashboard can visually separate butlers from staffers.

There is no separate `StafferDaemon` class or forked startup sequence.

**Alternatives considered:**
- Subclass `ButlerDaemon` → `StafferDaemon` → rejected because the behavioral differences are too small to justify a class hierarchy. Conditional checks at 2-3 decision points are cleaner.

**Rationale:** The engine stays unified. The type field acts as a tag that influences behavior at specific, well-defined points rather than creating a parallel execution path.

### D6: Heart-and-soul updates are additive, not rewriting

**Decision:** Update the doctrine documents to acknowledge the butler/staffer split:

- `vision.md`: Add staffer to the vocabulary. Amend non-negotiable rule #6 ("each butler has a manifesto") to cover both butlers (manifesto) and staffers (infrastructure contract).
- `architecture.md`: Add a section on the staffer archetype under "Domain Specialization Over Monolith." Staffers are infrastructure-specialized rather than domain-specialized.
- `v1.md`: Reclassify switchboard and messenger from "Butlers" to "Staffers" in the v1 scope listing.

**Rationale:** The doctrine is foundational. Changes should be minimal and precise — extending the vocabulary rather than rewriting the philosophy.

## Risks / Trade-offs

**[Risk] Type proliferation** → Mitigated by keeping the enum binary (butler | staffer). If a third type is ever needed, it should be justified by genuinely different engine behavior, not just organizational preference.

**[Risk] Cross-butler permissions are declarative but not enforced** → The `permissions.cross_butler_access` field is initially advisory (for documentation and validation). Actual enforcement requires MCP-level auth, which is a larger project. Mitigation: document this as a known gap; the permissions field is a stepping stone toward enforcement.

**[Risk] Switchboard is both a staffer and the routing hub** → The switchboard's dual role (infrastructure staffer + routing hub for all agents) could create confusion. Mitigation: the switchboard spec clearly documents this. Being a staffer doesn't change its routing responsibilities; it formalizes that users don't talk to it directly.

**[Risk] Existing code has hardcoded butler-name checks** → Some daemon logic checks `if config.name == "switchboard"` or `if config.name == "messenger"`. These should be migrated to `if config.type == "staffer"` where the behavior is type-generic, but name-specific checks may remain for truly unique behaviors (e.g., buffer management is switchboard-only, not staffer-generic). Mitigation: migration is incremental; name checks are not wrong, just less general.

## Migration Plan

1. **Schema change:** Add `type` field to `ButlerConfig` dataclass with default `"butler"`. No database migration needed (this is config, not DB).
2. **TOML updates:** Add `type = "staffer"` to `roster/switchboard/butler.toml` and `roster/messenger/butler.toml`. All other butlers default to `"butler"`.
3. **Daemon logic:** Add type-aware conditionals at routing-exclusion and briefing-exclusion decision points.
4. **Manifesto reframing:** Update switchboard and messenger `MANIFESTO.md` to use infrastructure-contract framing.
5. **Doctrine updates:** Update heart-and-soul docs (vision, architecture, v1) and relevant RFCs.
6. **Dashboard:** Expose type field in butler management API; update frontend to group by type.
7. **Rollback:** Remove `type` field from config, revert TOML changes, revert daemon conditionals. Zero data migration needed.

## Open Questions

1. **Should the `permissions.cross_butler_access` field be enforced at the MCP layer in v1?** Current design treats it as declarative. Full enforcement requires MCP auth tokens, which is a significant addition. Recommendation: defer enforcement to a follow-up change.
2. **Should the switchboard's eligibility sweep track staffer liveness differently?** Staffers are infrastructure-critical; their liveness SLA may differ from domain butlers. Recommendation: address in the QA staffer change when liveness requirements are better understood.
