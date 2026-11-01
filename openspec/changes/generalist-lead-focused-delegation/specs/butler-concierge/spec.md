## MODIFIED Requirements

### Requirement: Concierge Staffer Identity

Concierge SHALL be a staffer-typed agent in the roster at `roster/concierge/`
with `type = "staffer"` in its `butler.toml`. It is excluded from daily
briefing contributions per the staffer archetype contract
(`openspec/specs/staffer-archetype/spec.md`). Concierge answers SHALL run as
staffer-origin goals whose grant is no larger than the capability set declared
in Concierge's infrastructure contract, and that set SHALL contain only
Concierge's read capabilities over its fixed views. Concierge SHALL NOT spawn a
reasoning session of its own.

Source: RFC 0038 D2 and D5; RFC 0030
Scope: v1-mandatory

#### Scenario: Roster configuration

- **WHEN** Concierge's `butler.toml` is loaded
- **THEN** `config.type` is `ButlerType.STAFFER`
- **AND** `config.name` is `"concierge"`
- **AND** `config.db_schema` is `"concierge"`
- **AND** `config.permissions.cross_butler_access` is `["qa"]` (RFC 0015 error relay only;
  view-backed fleet reads require no outbound domain-butler access)

#### Scenario: Staffer behaviors apply automatically

- **WHEN** the Concierge daemon starts
- **THEN** it does not register daily briefing contribution schedules
- **AND** it registers its read-only capabilities with the control plane so
  staffer goals can be granted them

#### Scenario: A Concierge answer runs as a staffer goal

- **WHEN** a system-plane question is answered by Concierge
- **THEN** the answer runs as a staffer-origin goal whose grant holds only the
  read capabilities declared in Concierge's infrastructure contract
- **AND** no domain-data capability and no write capability is listed or
  callable in that goal

### Requirement: System-Plane Scope Boundary

Concierge SHALL answer only system-plane questions (fleet status, spend,
sessions, operational telemetry). A question about a user's own domain data
(finances, health, relationships, calendar, etc.) is out of scope for
Concierge regardless of surface-level phrasing similarity (e.g. "how much did
I spend" vs "how much did the fleet spend"). The boundary SHALL be enforced by
the Concierge staffer goal's grant, which contains no domain-data capability,
and not by message classification or routing.

Source: RFC 0038 D1 and D5; RFC 0030
Scope: v1-mandatory

#### Scenario: Domain questions stay with domain butlers

- **WHEN** a Concierge staffer goal is asked a domain-scoped question (e.g.
  "how much did I spend on groceries")
- **THEN** no finance or other domain-data capability is listed or callable in
  its grant
- **AND** Concierge's guidance instructs the invocation to decline and note the
  domain-butler boundary

#### Scenario: System-plane questions route to Concierge via the catalog

- **WHEN** a catalog search for 'how much did the fleet spend yesterday' runs
  after the `public.memory_catalog` seed (RFC 0030) has been applied
- **THEN** the top hit's `source_butler` is `"concierge"`
- **AND** the hit grants no capability; the question is answered by a
  Concierge staffer goal only through Concierge read capabilities over its
  sanctioned cross-schema views
