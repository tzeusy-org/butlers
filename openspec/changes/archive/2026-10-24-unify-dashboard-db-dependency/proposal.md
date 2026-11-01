# Unify dashboard database dependency

## Why
The accepted bu-q11586.3 S1 removes per-router raising stubs and duplicated startup override lists. Two existing dashboard-api implementation clauses still prescribe those stubs.

## What Changes
Replace only factory and roster DB dependency wiring clauses with one public `get_db`/`Db` dependency. Preserve singleton initialization/teardown, optional uninitialized reads, QA credential status binding, pool ownership, authorization, routes and response contracts.

## Impact
Capability: dashboard-api. Source: api/deps.py, router_registry.py, app registration, core/roster declarations and actual test overrides. Later extraction, renames, pool redesign and router line caps remain outside S1.
