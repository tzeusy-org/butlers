# Host-authorized owner passkeys and reusable dashboard login

## Why

The browser has no supported owner login while sensitive API controls require a
configured header key. The empty-key default also leaves general dashboard data
unprotected by HTTP authentication. One host-authorized registration followed
by reusable passkey login gives the owner daily access without retaining a
reusable API key in JavaScript.

This successor extends PR4164's existing change, under bu-azqfpk and independent
review bu-eeqmwt. It preserves the September 15 applied decisions: passkey
storage in Bitwarden, host-local enrollment/recovery, Tailscale Serve HTTPS,
and configured-key browser sessions/non-browser headers. It specifies the
previously unresolved host wire mechanism as approval of an exact browser-bound
intent. The old mechanism-neutral review is not adoption of this extension.

## What Changes

- Register one owner passkey only after host approval of a browser-bound intent;
  verify standard WebAuthn with a maintained library and atomic durable state.
- Support routine passkey login after expiry/logout and from synced browsers
  without another host action. Bitwarden selection happens in the browser's
  passkey chooser; Butlers never accesses a vault or derives identity from it.
- Recover through explicit host revocation and replacement, with durable epochs,
  replay/concurrency fencing, honest cancellation and lost-response states.
- Define exact bounded pre-session paths, closed output allowlists, fixed
  origin/RP, dedicated restricted auth persistence and operational procedures.
- Preserve opaque Secure/HttpOnly/Strict sessions, independent CSRF,
  X-API-Key automation and additive domain authorization checks.
- Reconcile active owner-only contracts and whole baseline requirements so the
  central boundary and usable browser access can ship as one vertical outcome.

## Capabilities

### New Capabilities

- `dashboard-owner-auth`: host-authorized passkeys, reusable owner sessions,
  exact ceremony boundaries, lifecycle/recovery and honest browser interaction.

### Modified Capabilities

- `dashboard-admin-gateway`: configured-key compatibility, owner-session
  boundary and truthful status of overall authentication.
- `dashboard-relationship`: central caller authentication before domain owner
  integrity checks, including protected keyless startup.
- `butler-health`: central transport authentication before owner/cache/LLM work.
- Existing active owner-only deltas are reconciled in place as inventoried in
  route-reconciliation.md; no duplicate capability or implementation lane.

## Impact

API middleware/router/owner attribution, isolated auth schema and host CLI,
frontend shell/client/stream calls, deployment configuration and runbooks.
No runtime/source implementation or dependency install is part of this draft.
Configured-key mode keeps the inherited exclusive-mode transition contract:
adding/removing/rotating its key retires prior credentials/sessions; it does not
silently enable passkeys and keys concurrently. Same-host deployments need
separate state/cookies but paths do not form an origin security boundary.

## Scope and non-goals

One owner and one active passkey, host enrollment/recovery, returning login,
sessions/CSRF, global dashboard protection, preserved scoped service callbacks,
and complete API/DB/browser evidence. No multi-tenancy, vault API, account/email
identity provider, custom cryptography, extra certificate authority, private HA
mapping input, private-model-override policy change, or unauthorized live action.

## Feature funnel

Size: large, crossing browser, HTTP, persistence and trusted-host boundaries.
Baseline: `5221178fbcfe60edeec0b7af31b71c7d55f0639e`.

- G0: all five pillars present and constraining; source/spec baseline refreshed.
- G1: low-friction repeated owner access with deliberate initial trust/recovery.
- G2: aligned with vision Rules 1/4, security trusted-host and credential rules;
  dedicated verification records contain no Bitwarden/provider secret.
- G3: dashboard API and CLI own deterministic auth; browser uses WebAuthn;
  existing Tailscale boundary remains canonical (design D1-D2).
- G4: design D3-D8 resolves protocol, races, storage, UX and tradeoffs.
- G5: owner-auth requirements 001-011 plus full dependent reconciliations.
- G6: real verifier, PostgreSQL/role/concurrency, mounted API and HTTPS/browser
  gates, privacy evidence and guarded rollback in tasks.md/runbook.md.

[Observed] Existing source has no passkey implementation. [Inferred] This
contract satisfies the recorded user outcome subject to independent review.
[Unknown] Live canonical HTTPS/Bitwarden interoperability remains unexercised.
Exact successor owner sign-off: pending. After adoption, project-direction
allocates the existing bu-7y7z2 vertical lane with preserved backend work;
implementation and landing follow the user-authorized lifecycle, while live
operations retain their separate boundaries.
