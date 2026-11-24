# Original Claude and Codex target packages

`Dockerfile.native-package-artifacts` consumes the complete pinned Node toolchain,
the separately reproduced Codex artifact, and the pinned offline Debian runtime
profile. Its public seed downloads only the four exact official npm archives in
`build-inputs.json`; both SHA-256 and npm SHA-512 integrity must match. Every 3xx
is refused, including same-origin redirects. No package resolution occurs here.
These are public build inputs, not additional runtime destinations: workload
profiles and their declared resolver/discovery behavior remain unchanged.

The networkless build retains every member of the original wrapper and
Linux/amd64 platform packages, including licenses, wrapper code, declared
optional target dependencies and all 46 Codex platform entries. It runs Claude's
unchanged `node install.cjs` postinstall with its actual official native package
already installed. The resulting native executable must remain byte-for-byte
equal to the official distribution. Its publishing-only `prepare` script is not
an installation hook and is not invoked.

For Codex, only the main native executable is replaced by the independently
produced, repository-pinned GNU artifact. Its original npm alias and
`vendor/x86_64-unknown-linux-musl/bin/codex` wrapper location remain intact;
that path does not assert the replacement's libc. The original ripgrep,
code-mode, voice and shell resources remain unchanged. The producer requires
the complete expected component digest rather than accepting an arbitrary
successful copy. The expected updated digest was derived from the retained
complete original inventory and the verified fixed-build binary; actual
source-producer reproduction and native parity require separate receipts.

Build with concrete outputs of the source-owned producers:

```sh
docker buildx build -f Dockerfile.native-package-artifacts \
  --build-context system-artifacts=/path/to/system-artifacts \
  --build-context toolchain-artifacts=/path/to/toolchain-components \
  --build-context codex-artifacts=/path/to/codex-artifact \
  --output type=local,dest=/path/to/new/native-packages .
```

The result contains `components/claude`, `components/codex` and deterministic
content-only provenance. Copies preserve canonical executable modes and use
independent regular files. Task timestamps and host build paths do not enter
the result. Final six-component assembly, image installation, complete native
auth/tool/asset behavior and network enforcement remain separate checks.
