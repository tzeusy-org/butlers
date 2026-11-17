## Why

A lost, stolen or replaced owner channel still resolves to the owner. Through
`public.resolve_owner_triple` it can answer Telegram approval callbacks, stamp Switchboard ingress
as owner, and receive owner-recipient handoff without approval. Nothing records that an endpoint has
left the owner's custody. Bead bu-s11n0s.1 carries the full released endpoint-custody outcome
(cheap owner-resolved LOCK, protected release, provider-wide fallback, egress fallback, security
question, Finance and recovery evidence); its design field holds the adopted protocol and all eleven
proposed requirements.

## What Changes

This change lands that outcome in serial slices. Its spec carries the two requirements the first
slice starts, copied verbatim from the adopted design (REQ-endpoint-custody-holds-002 and -006).
Later slices add the remaining requirements (-001, -003 to -005, -007 to -011) as they land, so no
requirement appears here before work on it starts.

Slice 1 (core_293):

- `public.custody_holds`: one authoritative hold record per canonical owner endpoint target and
  monotonic generation, keyed by `public.custody_target_digest` over the typed fact universe of
  `resolve_owner_triple`. History is append-only; the only permitted change is one release stamp.
- Row level security with no policy: no runtime role reads or writes holds, despite bootstrap
  public DML regrants. The database owner maintains the table, as in core_275.
- `public.custody_target_held`: content-blind SECURITY DEFINER eligibility read.
- `public.resolve_owner_triple` refuses owner for any sole-owner match with an actively held fact,
  so every owner-only definer consumer fails closed. Ambiguity, inactive, merged and deleted rules
  are unchanged.
- Downgrade refuses while a hold is active, or when the executing login cannot see every hold.

Slice 1 installs no runtime writer. Holds are written only by the trusted database owner until the
admitted Switchboard engine lands.

Slice 2a (core_347) starts REQ-001, -003 and -004, now copied verbatim into the spec, and lands
their protected half:

- `public.custody_commands`: the immutable receipt of each committed command, keyed by the
  caller's command id with a digest of its canonical request (replay returns the stored result; a
  reused id with another request is refused; a lost acknowledgement is read back by id).
- `public.custody_engine_apply`: hold, release and replace commit with the episode's fleet case,
  content-blind evidence and the receipt in one transaction. A hold names owner channel fact ids;
  each must resolve to the owner alone. Release and replace compare the expected generation;
  replace refuses while the owner still holds the binding.
- `public.custody_dashboard` (EXECUTE for `dashboard_auth_api` only) checks the current cookie
  session and CSRF token under the `dashboard_auth` singleton lock in the committing
  transaction. `public.custody_host` has no grant; the host's administrative login runs it.
- Dashboard routes under `/api/custody` and the host commands `butlers custody ...`.

The slice 2 scoping (2026-11-17) places the protected writer in a database-owner definer that
checks owner auth in the function, rather than a Switchboard service call: REQ-002 lets
dashboard/host control be verified at its owning boundary and forwarded as a protected command. The cheap owner-resolved LOCK and the REQ-001 provider fallback (task
2.1b) and P-CUSTODY-1c (task 2.1c) wait for the RFC 0038 cutover; P-CUSTODY-1c stays UNDELIVERED.

## Impact

- Code: `alembic/versions/core/core_293_endpoint_custody_holds.py`,
  `alembic/versions/core/core_347_custody_command_engine.py`,
  `src/butlers/api/routers/custody.py`, `src/butlers/api/owner_auth/custody_cli.py`.
- Tests: `tests/migrations/test_resolve_owner_triple_migration.py`,
  `tests/migrations/test_core_347_custody_command_engine.py`, `tests/api/test_custody_router.py`,
  `tests/cli/test_custody_cli.py`.
- Docs: `docs/identity_and_secrets/owner-identity.md`, `docs/modules/approvals.md`,
  `docs/data_and_storage/schema-topology.md`.
- No security.md, manifesto or RFC amendment: the design's authority amendments stay unadopted.
