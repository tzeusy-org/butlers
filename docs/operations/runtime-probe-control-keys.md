# Runtime-Probe Control Keys

> **Scope:** provisioning and rotating the deployment keys behind runtime-probe
> control capabilities (REQ-core-credentials-002, REQ-database-security-008),
> and the control plane those keys authorise
> (REQ-dashboard-model-settings-001).
> **Status:** the keys, the endpoint, and the client all exist; **nothing is
> mounted or activated yet.** Without a verifier mount Switchboard answers every
> control request `503/unavailable`, and without a signing mount the client signs
> nothing. Mounting the documents into production Compose is a separate,
> deliberate step, gated on the condition in [Activation](#activation).

A runtime-probe control capability is a short-lived signed statement that lets
Dashboard or the scheduler ask Switchboard to probe one model-catalog entry. It
is asymmetric on purpose: Dashboard holds a private signing key, every verifier
holds only public keys, and a stolen verifier keyring buys an attacker nothing.

This page is the operator contract: what the two documents are, where they live,
how they are provisioned, and what rotation looks like. It contains no key
material and none of these procedures should ever produce any in a log, a
terminal transcript, a ticket, or a commit.

## The two documents

| Document | Path | Secret? | Held by |
| --- | --- | --- | --- |
| Signing key | `/run/secrets/runtime_probe_control_signing_key` | **yes** | the signing side only |
| Verifier keyring | `/run/secrets/runtime_probe_control_verifiers` | no | every verifying process |

Both are strict UTF-8 JSON, read once at process start. There is no environment
variable, no database row, no `CredentialStore` entry, and no generic Secrets
surface for either — `RUNTIME_PROBE_CONTROL_SIGNING_KEY` is a **reserved name**
in the Secrets API: it is excluded from the inventory, reads answer as if it
were absent, and every mutation is refused. Adding a fallback would restore the
shared-value path the file mount exists to remove.

### Signing key

```json
{
  "version": 1,
  "alg": "EdDSA",
  "kid": "probe-2026-05a",
  "private_key_b64u": "<43-character unpadded base64url of the 32-byte Ed25519 seed>",
  "sign_from": "2026-05-01T12:00:00Z",
  "sign_until": null
}
```

* Every field is required and no other field is accepted.
* `alg` must be exactly `EdDSA`; `version` must be exactly `1`.
* `kid` matches `[A-Za-z0-9._-]{1,64}`.
* `private_key_b64u` is the **raw 32-byte seed**, unpadded base64url,
  canonically encoded. It is never a PEM, a PKCS#8 blob, or hex.
* Timestamps are UTC RFC 3339 to the second, `Z` form only.
* `sign_until` is `null` for the current key and set for a retiring one.
* The public half is **derived** from the seed at startup, never read from the
  document, and must match the keyring entry with the same `kid`.

File properties: a regular file (not a symlink), mode `0400`, owned by the
process identity. That is the deployment property the loader checks. It is **not**
isolation from a child process running as the same identity — such a child can
open the file whatever its mode says. Keeping the signer away from spawned
runtimes is the launcher's job.

### Verifier keyring

```json
{
  "version": 1,
  "current": {
    "alg": "EdDSA",
    "kid": "probe-2026-05a",
    "public_key_b64u": "<43-character unpadded base64url of the 32-byte public key>",
    "sign_from": "2026-05-01T12:00:00Z"
  },
  "retiring": []
}
```

* `retiring` is always present: `[]` states "no rotation in flight" explicitly
  rather than leaving it to be inferred from a missing field. It holds at most
  one entry.
* A retiring entry adds `sign_until` and `accept_until`.
* The two entries must differ in both `kid` and public key.
* `current.sign_from` must equal `retiring.sign_until` — one cutover instant,
  written once on each side.
* The keyring holds no secret, so it may be root-owned and world-readable. It
  must not be group- or world-**writable**: whoever can write it chooses which
  keys Switchboard will accept.

## Provisioning

Generate the keypair on an operator machine, outside the repository, and write
both documents directly to their deployment locations. Never echo the private
half, never paste it into a ticket, and never commit either document.

1. Generate a 32-byte Ed25519 seed from a cryptographic source and derive its
   public key.
2. Choose a `kid` that names the rotation, e.g. `probe-2026-05a`.
3. Write the signing-key document, `chmod 0400`, owned by the signing process's
   identity.
4. Write the keyring document with the derived public key and the same
   `sign_from`, readable by every verifying process.
5. Restart the affected services and confirm readiness before relying on the
   capability path.

If the two documents disagree — different `kid`, a public key that is not the
seed's, or a different `sign_from` — startup fails closed: the signing client
reports itself unavailable and signs nothing. It does not fall back to an
unsigned or shared-bearer path, and it does not take unrelated startup down with
it.

## Rotation

Rotation is **restart-driven and readiness-gated**. A running process reads its
documents once and keeps that snapshot for its lifetime, so replacing a file
under a live process changes nothing until it restarts. This is deliberate: it
makes the cutover an operation you schedule rather than a race you discover.

Pick a cutover instant `T`, then:

1. Publish a keyring whose `current` is the new key with `sign_from = T`, and
   whose `retiring` entry is the old key with `sign_until = T` and
   `accept_until = T + overlap`.
2. `overlap` must be between **70 seconds and 5 minutes**. Below 70s an
   in-flight one-minute capability could outlive the key that signed it; above
   5 minutes the old key stays live longer than the rotation contract allows.
3. Restart every verifying process first, so the new keyring is universally
   accepted before anything signs under the new key. The readiness gate below
   is how you confirm that landed rather than assume it.
4. Replace the signing key and restart the signing side.
5. After `accept_until`, publish a keyring with `retiring: []` and restart the
   verifiers again.

Issuance and acceptance intentionally differ during the overlap: the old key
stops **issuing** at `sign_until` (inclusive, no extra skew) but keeps
**verifying** until `accept_until`, so a capability minted a moment before
cutover still works.

## What the keys authorise

A capability buys exactly one thing: a bounded probe of one model-catalog entry.

```
POST /_control/runtime-probe/v1
Authorization: Bearer <compact JWS>
```

on Switchboard's own port — a plain route beside `/health`, deliberately **not**
an MCP tool, so no model session and no ordinary MCP client can enumerate or
call it. The request carries no query string, no body, and no cookies; the
catalog entry comes from the signed claim, and there is no parameter for a
prompt, a model, or runtime arguments. Anything else in the request is a `401`.

Switchboard verifies the capability, commits a SHA-256 nonce receipt, and only
then resolves the entry and launches it — in the same shared runtime home, with
the same authority, adapter construction, canonical-to-execution mapping,
generated configuration, and catalog arguments a new daemon invocation would
get, minus every domain MCP tool. The probe runs under a 30-second deadline,
global concurrency 8, and per-entry concurrency 1.

| Response | Meaning |
| --- | --- |
| `200` `{"status": "completed", "ok": …}` | a probe ran; `ok` is its verdict |
| `401` `{"status": "unauthorized"}` | the capability or the request shape was refused |
| `409` `{"status": "replay"}` | this capability had already been used |
| `429` `{"status": "busy"}` | probe capacity is saturated; nothing ran |
| `503` `{"status": "unavailable"}` | no verifier mount, unknown entry, or missing authority |
| `504` `{"status": "timeout"}` | the probe exceeded its deadline |

**Only `200` writes anything.** A completed probe updates the four
`model_catalog` verification columns through a `SECURITY DEFINER` function that
cannot reach `enabled`, `priority`, or breaker state — so a probe never closes
an open breaker, and never creates a dispatch attempt, routed provenance, or a
session. Every other outcome leaves the entry's verification history exactly as
it was. A `504` in particular records **nothing**: a probe the coordinator
abandoned at its own deadline is evidence about the coordinator, not the model,
and recording it would let a slow afternoon evict a healthy model from routing.

A capability is single-use, and the receipt is taken **before** the busy gate.
A request that arrives while capacity is saturated therefore spends its
capability and gets a `429`. That is intentional: if a rejected request could
keep its nonce, one capability could be retried until a slot opened and would no
longer be single-use.

## The readiness gate

The canonical full-stack launcher may bring Dashboard up before all-butlers, so
the signed client does not assume Switchboard can verify what it is about to
sign. It asks:

```
GET /_control/runtime-probe/v1/readiness?kid=<kid>
```

on the same private surface. This is the **only** route on this plane with a
query string, and the only one that takes no capability at all. The exception
holds because the rule protects capabilities: a `kid` is a key identifier, not
key material, and it already travels in the clear in the protected header of
every capability the plane carries.

| Response | Meaning |
| --- | --- |
| `200` `{"status": "ready"}` | that key id may **issue** right now; sign away |
| `503` `{"status": "unavailable"}` | anything else at all |

There is deliberately nothing between those two. An unknown key id, a malformed
one, an unmounted keyring, a request carrying a capability, a cookie, a body, or
any other query parameter all produce the identical `503` — same status, same
bytes, same headers — so the route cannot be used to enumerate which key ids a
deployment loaded.

"Ready" means **issuance**, not acceptance. During a rotation overlap the
retiring key still verifies until `accept_until`, but it stops issuing at
`sign_until`; readiness reports it as unavailable from that instant, because a
client that signed under it would be signing into a rejection.

The client asks once and latches the answer. A first "no" is an ordinary
startup state and the next probe asks again; a "yes" holds for the life of the
process, which is safe because both sides freeze their key snapshots at startup
and rotation is restart-driven. Nothing about Dashboard's ordinary `/health`
depends on this, so `oauth-gate` can still start all-butlers without a
dependency cycle.

## Activation

Do not mount either document yet. The production signing-key mount is gated on
every Dashboard runtime-CLI child path being removed or forced through the
per-invocation identity and kernel-containment launcher that
REQ-core-credentials-002 requires. Until then:

* the code path is exercised only against **isolated fixture keys generated
  inside tests** — see `butlers/testing/runtime_probe_control.py`;
* production Compose contains no signer or verifier mount, and a test pins that;
* Dashboard `Test`, `verify-all`, and the scheduled sweep still use their
  existing local verification path and are **not** cut over;
* the readiness gate is mounted and answers, but with no keyring mounted it
  answers `503` for every key id, which is the correct fail-closed state and
  not a fault to chase.

Rolling back after activation retains the child sandbox and makes
model-verification callers unavailable; it does not restore a local adapter
probe.

## Troubleshooting

Every failure is reported with a fixed diagnostic string that never contains key
material, a key id, a timestamp, or file content. Read them as categories:

| Message | What to check |
| --- | --- |
| `key file is missing or unreadable` | the mount exists and the process can open it |
| `key file is not a regular file` | a symlink, directory, or FIFO is in the way |
| `key file mode is not owner-read-only` | `chmod 0400` the signing key |
| `key file is not owned by this process` | ownership of the signing key |
| `key file is group or world writable` | tighten the keyring's mode |
| `key document has unknown or missing fields` | field set is exact; no extras, no omissions |
| `key material is not unpadded base64url` / `not 32 raw bytes` | raw seed/public key, not PEM or hex |
| `retiring overlap is outside 70s..5m` | the `accept_until - sign_until` window |
| `keyring cutover instants disagree` | `current.sign_from` vs `retiring.sign_until` |
| `signer does not match the current verifier` | the keyring was published from a different key |

Capability rejections are logged the same way — a fixed reason such as
`capability signature does not verify` or `capability has expired`, never the
capability, a segment of it, a nonce, a key, or a fingerprint. Nothing in a log
line, a telemetry span, an API payload, or a runtime prompt reproduces the
material it is describing, and the receipt table stores only a digest, so a
leaked receipt cannot be reconstructed into a working capability.

## Related

* [Environment Config](environment-config.md) — the secrets directory and other
  deployment-provisioned material.
* [Deployment Posture](deployment-posture.md) — dev versus hardened posture.
* `openspec/changes/harden-runtime-auth-and-breaker-attention/` —
  REQ-core-credentials-002 and REQ-database-security-008.
