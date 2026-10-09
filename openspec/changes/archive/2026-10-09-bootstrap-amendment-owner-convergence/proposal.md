# Converge the retained amendment table owner at managed bootstrap

## Why

A privileged core_255 install can leave the retained amendment table owned by the bootstrap login. Its inherited runtime grants expose metadata but cannot authorize ordinary CREATE INDEX/ALTER during a later core-schema replay. Preserve that causal failure and all historical amendment evidence while repairing the existing privileged bootstrap seam.

## What Changes

- Add catalog classification before durable bootstrap side effects and one fixed, atomic NOWAIT table-owner transition before the restore interface boundary.
- Extend the existing migrated-state node with source-neutralized DBAPI history and bounded actual psql-file success, late refusal, identity substitution, rollback and runtime witnesses.
- Add one mandatory seven-scenario requirement and the owning migration lesson. No applied migration, helper, function, role, grant, FK or FORCE-RLS change.

## Impact

The configured trusted ordinary migration login gains the existing table-owner capability; runtime policy and effective rights remain separately enforced. The whole psql script stays per-statement, with earlier legacy effects independently recorded. Function hardening stays separate. Collected cases do not grow.
