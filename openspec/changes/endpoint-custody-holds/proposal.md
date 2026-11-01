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

## Impact

- Code: `alembic/versions/core/core_293_endpoint_custody_holds.py`.
- Tests: `tests/migrations/test_resolve_owner_triple_migration.py`.
- Docs: `docs/identity_and_secrets/owner-identity.md`, `docs/modules/approvals.md`.
- No security.md, manifesto or RFC amendment: the design's authority amendments stay unadopted.
