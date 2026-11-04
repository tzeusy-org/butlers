## ADDED Requirements

### Requirement: Single Replication Role And Owner-Only Surface Controls
Among Butlers-managed roles, the primary database SHALL have exactly one role with the `REPLICATION` attribute, `butlers_projection_publisher`; the CNPG superuser `postgres` and the CNPG-managed `streaming_replica` role are the only other roles allowed to hold it. The publisher SHALL be `LOGIN NOINHERIT`, hold `SELECT` on exactly the tables published by `butlers_public_v1`, own no schema, have no `CREATE` privilege, and be a member of no runtime role group. Bootstrap normalization and restore-drill guards SHALL keep forcing `NOREPLICATION` on every other Butlers-managed role and SHALL name this role as the only exception. Because a `REPLICATION` role can decode every change in the database regardless of its table grants, the publisher's credential SHALL be guarded as primary-read-all material: the primary's `pg_hba` SHALL admit it only as `hostssl` to database `butlers` from the projection's pod address range, with certificate authentication (or `scram-sha-256` where a client certificate cannot be presented), and no `pg_hba` entry SHALL name the `replication` pseudo-database for it; its credential SHALL be held only in a Secret in the projection's namespace and never in the public server's namespace; and a critical alert SHALL fire when the primary holds any replication slot other than the subscription's, or any slot whose output plugin is not `pgoutput`. Schema `mcp_surface` (tables `controls` and `manifest_state`) SHALL sit outside `public`, SHALL be readable by the publisher for replication, and SHALL be writable only by the migration role (used by `butlers public-mcp reconcile` and `butlers public-mcp disable|enable`) and by the dashboard API's database role for its owner route. The Relationship runtime role SHALL have `USAGE` on the schema and `SELECT` on `mcp_surface.controls` only, so the proposal importer can honour the switches. Every other runtime role's privileges on the schema SHALL stay revoked, and no runtime role SHALL be able to write it, across a bootstrap rerun.

ID: REQ-database-security-016
Source: RFC 0039 D9 (roles; the publisher credential is primary-read-all material); scripts/init-db.sql NOREPLICATION normalization; craft-and-care/security-and-secrets.md Privilege and Boundary Discipline
Scope: v1-mandatory

#### Scenario: Only the publisher can replicate among Butlers roles
- **WHEN** the role catalog is inspected after a bootstrap rerun
- **THEN** the roles with `rolreplication = true` SHALL be exactly `butlers_projection_publisher`, `postgres` and `streaming_replica`

#### Scenario: A second replication role is refused
- **WHEN** any other Butlers-managed role is given `REPLICATION` and the bootstrap or restore-drill guard runs
- **THEN** the guard SHALL fail and name the role

#### Scenario: The publisher has no physical replication entry
- **WHEN** the contract test inspects the primary's rendered `pg_hba` rules
- **THEN** the only rule admitting `butlers_projection_publisher` SHALL be a `hostssl` rule for database `butlers` from the projection's address range, and no rule naming `replication` SHALL admit it

#### Scenario: An unexpected slot raises a critical alert
- **WHEN** a replication slot using the `test_decoding` plugin, or any slot other than the subscription's, is created on the primary
- **THEN** a critical alert SHALL fire naming the slot

#### Scenario: The importer can read the switches but not write them
- **WHEN** the Relationship runtime role selects from `mcp_surface.controls` and then tries to update it
- **THEN** the select SHALL succeed and the update SHALL fail with `insufficient_privilege`

#### Scenario: Butlers cannot flip kill switches
- **WHEN** any butler runtime role inserts or updates `mcp_surface.controls`, including after a bootstrap rerun
- **THEN** the write SHALL fail with `insufficient_privilege`

## Source References

- Non-Negotiable Rule 3 (cross-domain access; RFC 0039 names the replication role and projection as the one guarded exception), `about/heart-and-soul/vision.md`.
- `about/heart-and-soul/security.md`, schema isolation, as amended by RFC 0039.
- RFC 0006 (database schema and isolation) and RFC 0039 (`about/legends-and-lore/rfcs/0039-public-mcp-read-surface.md`), D9.
