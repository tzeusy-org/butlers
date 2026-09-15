# Owner access operations: proposed runbook

Reader: the host operator. Purpose: enroll, sign in and recover without exposing
authentication material. These commands are a specified interface to implement,
not commands that exist today. This artifact authorizes no operation.

## Before any live operation

The implementation must have adopted specification, exact-head independent
review, mounted API/verifier/real-Postgres/isolated-browser evidence and the
required landing identity. Obtain separately bounded authority for the named
instance and operation (migration, proxy configuration, deployment/restart,
enrollment, recovery or rollback). Never use real vault data to debug synthetic
failures. No private mapping values belong in this feature or Beads.

Verify without printing secrets: expected image SHA, healthy authoritative auth
schema/role grants, fixed canonical origin/RP, separate instance identity,
loopback-only exposure, trusted Serve header handling, no auth body/header/query
capture in proxy/app telemetry, and the content-blind status. The expected
origin is the configured Tailscale HTTPS hostname, not an HTTP localhost URL.
Keep deployment-specific cookie names and state separate. Dev/prod URL paths on
the same host are the same browser security origin; mutual distrust requires
separate hostnames, not cookie paths. Do not change live Serve configuration as
part of a read-only check.

## First enrollment

1. Open the canonical HTTPS dashboard and select Register passkey. The UI must
   show an inert request ID, intended operation and canonical origin, with no
   private data. Loading this page grants nothing.
2. On the trusted host, compare the full ID/origin with that same browser.
   Use `butlers auth authorize-registration --request <request-id>` through the
   existing administrative DB connection. Use only a request shown in your own
   browser; never an unsolicited command. This ID is a locator, not a bearer
   secret, but do not retain it in audit packets or command telemetry.
3. Return to the browser, check authorization, select Register passkey and
   choose Bitwarden in its native passkey chooser. Butlers never asks for the
   Bitwarden master password or reads the vault.
4. Wait for server verification and committed session before claiming success.
   A prompt opening or a passkey manager closing is insufficient. The server
   cannot independently certify which provider stored the credential.
5. If cancelled/expired, start a fresh request and approve it on the host.
   If the response is lost after possible commit, try Sign in with passkey;
   do not repeatedly submit the old finish response.

## Daily login and logout

Select Sign in with passkey and choose the existing passkey. Synced browsers
use the same flow without a host command. Expiry/logout/all-session revocation
preserves the credential. Reload obtains an in-memory CSRF token through the
bounded authenticated endpoint. Do not resubmit an interrupted unsafe action
until the UI offers an explicit retry after login.

Logout revokes the current session. Revoke all sessions invalidates all browser
sessions but keeps the passkey usable. The host emergency command
`butlers auth revoke-sessions --confirm-revoke` advances session epoch and
revokes pending login ceremonies, without replacing the passkey. A valid
configured-key caller can use the existing plural session-delete contract.

## Lost credential or vault: explicit recovery

1. Select Recover access in the canonical browser, producing a recovery intent.
2. Understand the immediate effect: host approval retires the old passkey and
   revokes all browser sessions before any replacement is registered.
3. Compare the intended request/origin and run
   `butlers auth authorize-recovery --request <request-id> --confirm-revoke`.
4. Register the replacement in the browser and verify committed login. If
   cancelled/expired, the old passkey does not reactivate: create a fresh
   recovery intent and repeat explicit host recovery.
5. Keep only timestamp, deployed SHA, operation category and pass/fail outcome
   as evidence. No credential IDs, handles, cookies, CSRF, key digests, request
   IDs, screenshot of a vault or raw HTTP transcript.

## Configuration changes

Before restarting after adding, removing or rotating DASHBOARD_API_KEY, run
`butlers auth reconcile-mode --confirm-revoke` using the intended deployment
configuration and trusted host DB access. It atomically records the new mode
and invalidates authority; repeated unchanged configuration is a no-op. API
startup refuses a configuration mismatch and cannot perform this privileged
transition itself. This is a deliberate mode transition:
all prior sessions and passkey authority are retired. Configured-key mode keeps
X-API-Key automation and HTTPS key-to-session login; it does not also accept
passkeys. Removing the key requires fresh host enrollment. Never place the key
in a frontend environment variable, URL, persistent browser storage or runbook.

A hostname/RP change requires planned canonical HTTPS configuration, then
`butlers auth rebind-origin --confirm-revoke` on the host. It retires old
credentials/sessions and enters recovery_pending. Enroll a replacement for the
new origin through explicit host recovery. Existing credentials cannot be
ported by changing their stored RP ID. Missing/corrupt auth storage requires
host repair and epoch invalidation, never deletion to reopen public enrollment.

## Failed deployment, restore and rollback

Stop exposure before restoring old auth state. Preserve a backup through the
existing separately authorized backup workflow. Restoration may resurrect old
sessions/challenges, so explicit host epoch invalidation is mandatory before
re-exposure; the restored DB cannot detect its own rollback. Do not report a
restore safe because a health endpoint responds.

Rollback to a legacy image is permitted only after sessions/ceremonies are
revoked and either a nonempty configured API key is established or every
browser/network exposure is removed. Never run a reachable legacy empty-key
image as a recovery shortcut. Do not drop auth history/schema during rollback.
A forward cutover invalidates old authority again before exposure. Failed
preflight means stop the operation; it never falls back to public access.

## Acceptance evidence

Synthetic tests prove protocol mechanics without a real vault. Separately
approved canonical-HTTPS acceptance must prove actual cookie acceptance and
return, HttpOnly absence from JS, CSRF reload/mutation, real passkey registration
and returning login, cancellation, expiry and recovery outcomes. Report each
named seam independently; tests, PR merge, deployment and owner-vault success
are different facts. A blocked live operation leaves that evidence outstanding.
