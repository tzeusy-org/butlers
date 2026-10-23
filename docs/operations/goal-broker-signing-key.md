# Goal broker signer acquisition (dark source)

RFC 0038 D7 and the named exception in `about/heart-and-soul/security.md`
permit only `butlers-control` to read `GOAL_BROKER_SIGNING_KEY`. Domains receive
public verifier configuration only. This source does not provision a key,
change mounts, enable a profile, start a listener, or establish process isolation.
`KEY_ABSENT_STAGE` still rejects every private-key configuration.

`AssertionSigner.from_file` is the explicit control-process construction seam.
It defaults to disabled and returns `None` before checking configuration or
performing filesystem I/O. A production factory must separately supply the real
catalog, ledger, domain adapters and authenticated runner inputs before the
broker can serve any invocation. A loaded key does not certify those inputs.

Enabled acquisition requires an absolute deployment-file path, a key ID of
1–64 ASCII letters/digits/dots/underscores/hyphens, and that key's exact public
Ed25519 verifier in trusted current/next configuration. The private document is
one canonical, unencrypted PKCS8 PEM Ed25519 key, at most 8192 bytes. It must be
a regular file owned by the process, with mode `0400`. Descriptor-bound checks
refuse final symlinks, FIFOs, changed reads, unsafe modes, malformed documents,
other algorithms, trailing/concatenated material and public-key mismatches.
No environment value, credential store, database or alternate path is tried.
Failures expose only the existing closed `broker_unavailable` reason.

The returned signer is an in-memory startup snapshot. File changes do not
replace its key. Rotation follows D7: publish the next public key to domains,
restart domains, switch the control signer on restart, then remove the previous
public key after twice the assertion lifetime. This loader does not perform
those operations or prove that remote domains adopted the configured verifier.
The existing assertion wire fields, signature and 60-second expiry are unchanged.

File permissions do not isolate same-identity processes or runtime descendants.
Deployment must still mount the private file only into the separated control
process and preserve the runner's kernel-enforced filesystem/process boundaries.
Tests use ephemeral generated material only; actual provisioning, deployment,
remote verification and runtime activation remain unperformed.
