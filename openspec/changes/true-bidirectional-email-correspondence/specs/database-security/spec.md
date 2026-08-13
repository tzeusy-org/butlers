## ADDED Requirements

### Requirement: Narrow correspondence aggregate isolation exception

The system SHALL keep the private correspondence ledger and its qualified-inbound,
coverage, complete account-universe, native-send dispatch/report-fence,
confirmation lease/report-fence, and alias-authority tables free of direct grants
to Relationship, Switchboard,
connector roles, dashboard roles, or `PUBLIC`.  The only Relationship read path
is a read-only `SECURITY DEFINER` function with a fixed, bounded entity-ID input
and fixed aggregate output.  It SHALL use
`SET search_path = pg_catalog`, explicit schema-qualified object
references, `REVOKE ALL ON FUNCTION ... FROM PUBLIC`, and `GRANT EXECUTE` only
to `butler_relationship_rw`.

The only Switchboard SQL write paths into private correspondence storage are two
separate fixed Messenger-owned `SECURITY DEFINER` functions.  The ingress
projection function is
`messenger.record_qualified_email_ingress(text, text, text, uuid, timestamptz,
bytea)`, executable only by `butler_switchboard_rw` under authenticated internal
ingress context.  Its fixed inputs are only broker-derived provider, canonical
account, normalized peer, authenticated ingress epoch, once-captured server
receipt time, and a non-reversible account-scoped source-deduplication token; it
returns only `recorded` or `duplicate`.  It has no raw source event ID, provider
time, content, generic caller-principal, or table-select surface.  It SHALL use
an explicit migration-managed designated definer owner, `SET search_path =
pg_catalog`, explicitly schema-qualified object references, `REVOKE ALL ON
FUNCTION messenger.record_qualified_email_ingress(text, text, text, uuid,
timestamptz, bytea) FROM PUBLIC`, and only `GRANT USAGE ON SCHEMA messenger` plus
`GRANT EXECUTE ON FUNCTION messenger.record_qualified_email_ingress(text, text,
text, uuid, timestamptz, bytea)` to `butler_switchboard_rw`.  The grant SHALL not
confer any other Messenger object access.  Connector, dashboard, Relationship,
and all other butler roles SHALL have neither that schema usage nor function
execute or direct private-table access.  The function deduplicates atomically
within the qualifying accepted-ingress transaction and does not expose a generic
ledger writer.

The coverage-close function is
`messenger.close_qualified_email_coverage(text, text, uuid, text, timestamptz)`.
It accepts only broker-derived provider, canonical account, authenticated ingress
epoch, one checked category (`age_invalid`, `checkpoint_gap`, `reauth`, `rebind`,
or `principal_mismatch`), and once-captured server closure time; it returns only
`closed` or `already_closed`.  It SHALL accept no peer, raw source event ID,
provider time, content, free text, generic caller principal, or table-select
surface.  It SHALL use the same explicit migration-managed designated definer
owner, `SET search_path = pg_catalog`, schema-qualified references, and denial
posture as the projection function, including `REVOKE ALL ON FUNCTION
messenger.close_qualified_email_coverage(text, text, uuid, text, timestamptz)
FROM PUBLIC` and only exact `GRANT EXECUTE ON FUNCTION
messenger.close_qualified_email_coverage(text, text, uuid, text, timestamptz)`
to `butler_switchboard_rw` alongside the minimal existing `USAGE ON SCHEMA
messenger`.  It grants no other Messenger object access.  It SHALL atomically
close/block the matching coverage epoch in the authenticated ingress/control
transaction; an identical closure is idempotent and cannot leave an earlier
watermark usable as fresh.

Because Messenger and Relationship migrations run independently, a
migration-admin-owned idempotent post-chain activation routine SHALL run after
each Messenger/Relationship chain completion and all-migrate finalization.  It
SHALL create/enable the function only after both private Messenger tables and
`relationship.entity_facts` exist and its designated owner/ACLs validate.  A
partial topology SHALL leave the aggregate unavailable and callers
indeterminate; it SHALL not use dynamic SQL, direct grants, or a raw-table
fallback.

#### Scenario: Relationship cannot select or enumerate the ledger

- **WHEN** a connection is running as `butler_relationship_rw`
- **THEN** a direct `SELECT` from `messenger.email_correspondence` is rejected
- **AND** the role cannot list private reconciliation leases or provider alias
  authority rows
- **AND** it can execute only the bounded aggregate function

#### Scenario: Switchboard has only the two fixed atomic ingress write paths

- **WHEN** a connection is running as `butler_switchboard_rw`
- **THEN** direct `SELECT` or `INSERT` on Messenger correspondence tables is
  rejected
- **AND** it has only `USAGE ON SCHEMA messenger` and exact execute on
  `messenger.record_qualified_email_ingress(text, text, text, uuid, timestamptz,
  bytea)` and `messenger.close_qualified_email_coverage(text, text, uuid, text,
  timestamptz)` under the authenticated internal route contract, with no other
  Messenger object access
- **AND** a duplicate opaque source token returns the original categorical result
  without replacing receipt time or advancing coverage
- **AND** each fixed invalid-age/checkpoint-gap/re-auth/rebind/principal-mismatch
  closure atomically returns `closed` or `already_closed` without accepting a
  peer, raw source ID, provider time, content, or free-text reason
- **AND** no closure leaves a previous inbound coverage watermark usable as fresh
- **AND** migrated PostgreSQL tests assert the function's fixed signature,
  designated owner, hardened search path, schema-qualified body, exact grant set,
  caller-controlled search-path resistance, and denial for every other role for
  both functions

#### Scenario: Independent migration ordering fails closed

- **WHEN** Messenger runs before Relationship, Relationship runs before
  Messenger, or either dependent schema is absent
- **THEN** the activation step leaves the aggregate disabled without startup
  failure or widened privilege
- **AND** once both schemas and ACL checks exist, a later idempotent activation
  creates the fixed function
- **AND** a downgrade revokes the function before a dependent schema changes

#### Scenario: Aggregate output is fixed and non-identifying

- **WHEN** `butler_relationship_rw` invokes the aggregate for a valid bounded
  entity-ID batch
- **THEN** it receives at most one row per requested entity with the documented
  count, timestamps, freshness, and tri-state result
- **AND** it receives no address, provider/account identifier, message/thread
  reference, raw ledger row, inbound-event row, content, or audit text

#### Scenario: Untrusted callers cannot widen access

- **WHEN** `PUBLIC`, a connector role, a dashboard role, or another butler role
  invokes the aggregate or attempts to create an object in its function schema
- **THEN** PostgreSQL rejects the action under the migration-managed grants
- **AND** no caller-controlled search path or unbounded selector can alter the
  function's private-table reads

#### Scenario: Connector correspondence is brokered and principal-bound

- **WHEN** Gmail requests a correspondence native-send dispatch, reports its
  direct send result, requests a confirmation lease, or reports confirmation
- **THEN** it authenticates only to Switchboard with a credential scoped to its
  connector/account principal
- **AND** Switchboard routes the typed request to Messenger without granting
  Gmail a Messenger endpoint, table privilege, or generic ledger writer
- **AND** a forged, expired, revoked, wrong-scope, cross-account, or replayed
  dispatch/send-report/lease/confirmation-report request is rejected before any
  ledger state transition
