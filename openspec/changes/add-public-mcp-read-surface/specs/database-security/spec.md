## ADDED Requirements

### Requirement: Single Replication Role And Owner-Only Surface Controls
Among Butlers-managed roles, the primary database SHALL have exactly one role with the `REPLICATION` attribute, `butlers_projection_publisher`; the CNPG superuser `postgres` and the CNPG-managed `streaming_replica` role are the only other roles allowed to hold it. The publisher SHALL be `LOGIN NOINHERIT`, hold `CONNECT` on `butlers`, `USAGE` on exactly the schemas and `SELECT` on exactly the tables published by `butlers_public_v1`, own no schema, have no `CREATE` privilege, and be a member of no runtime role group; and no role SHALL be a member of the publisher (`pg_auth_members` SHALL hold no row whose `roleid` is the publisher), because a member could `SET ROLE` to it and decode WAL through the SQL slot functions. `scripts/init-db.sql`, which the owner runs as the superuser, SHALL grant that `CONNECT` and sync that `USAGE` and `SELECT` from `pg_publication_tables`, revoking both everywhere else, and the owner step SHALL repeat the sync. Bootstrap normalization and restore-drill guards SHALL keep forcing `NOREPLICATION` on every other Butlers-managed role and SHALL name this role as the only exception. Because a `REPLICATION` role can decode every change in the database regardless of its table grants, the publisher SHALL have no credential at all: `rolpassword` SHALL be NULL, and no `pg_hba` rule SHALL admit it by certificate, so certificates are excluded by `pg_hba` rather than by their absence. It SHALL log in only over the instance's local socket by peer authentication through the CNPG `local` `pg_ident` map, which SHALL hold exactly `local postgres postgres` and `local postgres butlers_projection_publisher`; `host all butlers_projection_publisher all reject` SHALL precede CNPG's catch-all, so every TCP login as the publisher is refused; and no `pg_hba` entry SHALL name the `replication` pseudo-database for it. A contract test and a live check SHALL assert these database facts and the absence of members; the live check's database half needs the superuser and SHALL run inside the owner step's guard and after each `init-db.sql` run. The live check's Kubernetes half SHALL read no Secret data: it SHALL assert that the CNPG `Cluster` resource's managed-role entry for the publisher names no `passwordSecret` and that no ExternalSecret spec in `butlers-<env>` or `butlers-public-<env>` names the publisher. A critical alert SHALL fire when the primary holds a logical replication slot that is neither the subscription's slot `butlers_public_sub` nor one of the current subscription's table-sync slots (`pg_<suboid>_sync_<relid>_<sysid>`, while that table is not yet in state `r`), or a logical slot whose output plugin is not `pgoutput`; physical slots, such as those CNPG manages for its standbys, SHALL NOT trigger it. The role `mcp_subscription_owner` (`LOGIN`, member of `pg_create_subscription` and `mcp_projection_owner`) keeps its login and password copies at the owner's choice (OD15), but no Butlers workload SHALL mount or use that credential, and no rendered Butlers chart SHALL reference the Secret `postgres-mcp-subscription-owner-user`. Schema `mcp_surface` (tables `controls` and `manifest_state`) SHALL sit outside `public`, SHALL be readable by the publisher for replication. `mcp_surface.manifest_state` SHALL be writable only by the migration role, through `butlers public-mcp reconcile`; `mcp_surface.controls` SHALL be writable only by the migration role (used by `butlers public-mcp disable|enable`) and by the dashboard API's database role for its owner route, which SHALL have no write on `manifest_state`. The Relationship runtime role SHALL have `USAGE` on the schema and `SELECT` on `mcp_surface.controls` only, so the proposal importer can honour the switches. Every other runtime role's privileges on the schema SHALL stay revoked, and no runtime role SHALL be able to write it, across a bootstrap rerun.

ID: REQ-database-security-016
Source: RFC 0039 D3 (same-instance subscription; the owner step) and D9 (roles; the publisher is primary-read-all and has no credential; residual risk: superuser-equivalent access to the instance); owner decisions OD10, OD14 and OD15; [Observed] homelab PR Tzeusy/homelab#40 (`pg_hba_file_rules` and the `local` `pg_ident` map on `postgres-dev`); [Observed] `postgres-dev` 2026-11-04 (the publisher has no `CONNECT` on `butlers`, `USAGE` on no Butlers schema, and no members); [Observed] PostgreSQL 17 `CheckSlotPermissions` (checks the current user's `REPLICATION` attribute) and tablesync slot naming; scripts/init-db.sql NOREPLICATION normalization; craft-and-care/security-and-secrets.md Privilege and Boundary Discipline
Scope: v1-mandatory

#### Scenario: Only the publisher can replicate among Butlers roles
- **WHEN** the role catalog is inspected after a bootstrap rerun
- **THEN** the roles with `rolreplication = true` SHALL be exactly `butlers_projection_publisher`, `postgres` and `streaming_replica`

#### Scenario: A second replication role is refused
- **WHEN** any other Butlers-managed role is given `REPLICATION` and the bootstrap or restore-drill guard runs
- **THEN** the guard SHALL fail and name the role

#### Scenario: The publisher logs in only by local peer authentication
- **WHEN** the contract test inspects `pg_hba_file_rules` and `pg_ident_file_mappings` on the primary
- **THEN** the only rule that can admit `butlers_projection_publisher` SHALL be the CNPG `local all all peer map=local` rule, `host all butlers_projection_publisher all reject` SHALL precede the catch-all, no rule naming `replication` SHALL admit it, and the `local` map SHALL hold exactly its two lines

#### Scenario: A TCP login as the publisher is refused
- **WHEN** any client connects over TCP to `butlers` as `butlers_projection_publisher`, from any address
- **THEN** `pg_hba` SHALL reject the connection

#### Scenario: No publisher credential exists anywhere
- **WHEN** the live check runs inside the owner step's guard and against the CNPG `Cluster` resource and the ExternalSecret specs
- **THEN** the publisher's `rolpassword` SHALL be NULL, its CNPG managed-role entry SHALL name no `passwordSecret`, no ExternalSecret spec in `butlers-<env>` or `butlers-public-<env>` SHALL name the publisher, and the check SHALL read no Secret data

#### Scenario: No role can become the publisher
- **WHEN** any role is granted membership in `butlers_projection_publisher` and the contract test or the owner step's live check runs
- **THEN** it SHALL fail and name the member, because `pg_auth_members` must hold no row whose `roleid` is the publisher

#### Scenario: The publisher can run the initial copy
- **WHEN** `init-db.sql` or the owner step has synced the publisher's grants and the subscription starts its initial copy
- **THEN** the publisher SHALL hold `USAGE` on exactly the schemas of `pg_publication_tables` for `butlers_public_v1` and `SELECT` on exactly its tables, and every table's copy SHALL complete without `permission denied for schema`

#### Scenario: Only reconcile writes the manifest digest
- **WHEN** the dashboard API's database role tries to update `mcp_surface.manifest_state`
- **THEN** the update SHALL fail with `insufficient_privilege`, while its update of `mcp_surface.controls` SHALL succeed

#### Scenario: No Butlers workload uses the subscription owner's credential
- **WHEN** the `butlers` and `butlers-public-mcp` charts are rendered
- **THEN** no pod, volume or environment entry SHALL reference `postgres-mcp-subscription-owner-user`

#### Scenario: An unexpected slot raises a critical alert
- **WHEN** a logical slot using the `test_decoding` plugin, or any logical slot other than the subscription's slot and its current table-sync slots, is created on the primary
- **THEN** a critical alert SHALL fire naming the slot

#### Scenario: An initial copy does not raise the slot alert
- **WHEN** the owner step's first sync or rebuild runs and the subscription creates `pgoutput` table-sync slots named `pg_<suboid>_sync_<relid>_<sysid>` for its own oid
- **THEN** the unexpected-slot alert SHALL NOT fire for them

#### Scenario: The importer can read the switches but not write them
- **WHEN** the Relationship runtime role selects from `mcp_surface.controls` and then tries to update it
- **THEN** the select SHALL succeed and the update SHALL fail with `insufficient_privilege`

#### Scenario: Butlers cannot flip kill switches
- **WHEN** any butler runtime role inserts or updates `mcp_surface.controls`, including after a bootstrap rerun
- **THEN** the write SHALL fail with `insufficient_privilege`

## Source References

- Non-Negotiable Rule 3 (cross-domain access; RFC 0039 names the replication role and projection as the one guarded exception), `about/heart-and-soul/vision.md`.
- `about/heart-and-soul/security.md`, schema isolation, as amended by RFC 0039.
- RFC 0006 (database schema and isolation) and RFC 0039 (`about/legends-and-lore/rfcs/0039-public-mcp-read-surface.md`), D3 and D9; owner decisions OD14 and OD15.
