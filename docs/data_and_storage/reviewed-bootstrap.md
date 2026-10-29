# Reviewed bootstrap before online migrations

Every supported database, including ordinary dev, needs the reviewed
`scripts/init-db.sql` before its first online migration. Rerun that managed
procedure when the checked-in bootstrap's managed role, schema or interface
surface changes. A migration command checks the current catalog; it does not
run privileged bootstrap automatically or attest its execution history.

The database and normal connecting login must already exist. Use the deployment's
configured **distinct cluster-superuser bootstrap identity** for the script and
its configured **normal migration identity** for Alembic. Do not invent a postgres
password or route around unavailable privileged authentication. Authentication
availability is an operator prerequisite, not permission for the migration login
to self-bootstrap.

For an authorized operator, the ordering is:

```bash
# Use the configured host, database and distinct privileged bootstrap identity.
# Authentication flows through the existing deployment mechanism, not this doc.
PGOPTIONS="-c butlers.connecting_user=<normal-migration-login>" \
  psql -X -v ON_ERROR_STOP=1 -h <host> -U <bootstrap-superuser> \
  -d <database> -f scripts/init-db.sql

# Then use the configured normal login through the normal application command.
butlers db migrate
```

For a genuine existing database stopped at `core_195`, preserve its tracking and
data, run the same reviewed script as the distinct configured bootstrap identity,
then retry the normal migration. Do not stamp past `core_196`, rewrite an applied
migration, transfer protected objects to the migration login, or weaken its
provenance guard. A caller-owned lookalike may require separate reviewed repair;
the bootstrap intentionally refuses to reclaim an untrusted authority object.

The shared read-only check runs before the programmatic runner creates extensions
and before direct online Alembic creates a target schema, sets its path or opens
version/revision mutations. It validates the finite source-derived extension,
managed role/schema, membership/default-ACL and protected-admin provenance
profile. Missing, partial, untrusted or unreadable state produces a fixed message
pointing to `scripts/init-db.sql`; connectivity/authentication failure remains a
separate channel. Neither message needs private configuration row contents or
connection credentials. Offline SQL generation cannot attest a target database.

Fresh installer-ready and correctly finalized protected states are both supported.
After finalization consumes one-shot installer privileges, repeat migrations,
second-schema replay and mixed-chain tracking do not demand those grants back.
The stable prerequisite profile excludes the ordinary Relationship SELECT default
on Switchboard: immutable `core_001` revokes cross-schema defaults during bounded
replay and `core_077` repairs this one. Requiring it before that continuation would
prevent the repair itself. This exclusion grants no runtime access and does not
waive required own-schema/base ACLs or protected authority. Its absence is not a
bootstrap failure, nor proof of working Relationship reads: actual default-ACL
and effective `SET ROLE` readback separately witness the repair. No revision or
completion marker supplies bootstrap provenance.

The applied `core_196`, `core_197` and `core_198` point-of-use guards remain
independent. Existing managed privileged downgrade authorization and its data
constraints remain; catalog admission grants no downgrade authority to a normal
login. Managed privileged operations use their existing private-config read
permission to identify the normal profile subject, without granting that read to
normal migrations.

The profile checks current catalog truth, not a writable completion flag, script
history, all feature integrity or resistance to a malicious trusted superuser.
When the managed bootstrap surface changes, update the finite profile and this
operator instruction in the same change. A partial profile cannot be waived by
an unrelated migrated revision or an old completion comment.

Bootstrap reserves the recovery role and installs its privileged control plane;
it does not enable the restore executor, provide its secret or activate a drill.
Ordinary dev keeps base-only services. Direct `Database.connect()` development
fallback is still available for ordinary non-DND workloads; it does not authorize
an unbootstrapped online startup. DND mutation/admission and hardened role
verification retain their fail-closed boundaries. See
[backup and restore](../operations/backup-restore.md),
[development setup](../getting_started/dev-environment.md), and
[RFC 0006](../../about/legends-and-lore/rfcs/0006-database-schema-and-isolation.md).

## Implementation Notes

- `src/butlers/bootstrap_prerequisite.py` shares admission between
  `run_migrations()` and the actual online connection in `alembic/env.py`.
  It executes only catalog reads and caches source predicates, never DB verdicts.
- Finalized predicates come from the immutable protected migration sources,
  qualified against `pg_catalog`; the check invokes no installer/finalizer or
  rollback function. A partial installed authority must pass that complete
  finalized proof rather than falling back to the staged grant path.
- Genuine historical controls use checksum-verified old entrypoints and the full
  canonical predecessor chains in disposable PostgreSQL. Generated historical
  files live outside current production source identity so coverage does not
  attribute old code to the current source population. Software ordering or
  collection alone is not PostgreSQL proof.
