# Runtime-Probe Control Keys

> **Scope:** provisioning and rotating the deployment keys behind runtime-probe
> control capabilities (REQ-core-credentials-002, REQ-database-security-008).
> **Status:** the representation exists; **nothing is mounted or activated yet.**
> Mounting the documents into production Compose is a separate, deliberate step.

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
   accepted before anything signs under the new key.
4. Replace the signing key and restart the signing side.
5. After `accept_until`, publish a keyring with `retiring: []` and restart the
   verifiers again.

Issuance and acceptance intentionally differ during the overlap: the old key
stops **issuing** at `sign_until` (inclusive, no extra skew) but keeps
**verifying** until `accept_until`, so a capability minted a moment before
cutover still works.

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

## Related

* [Environment Config](environment-config.md) — the secrets directory and other
  deployment-provisioned material.
* [Deployment Posture](deployment-posture.md) — dev versus hardened posture.
* `openspec/changes/harden-runtime-auth-and-breaker-attention/` —
  REQ-core-credentials-002 and REQ-database-security-008.
